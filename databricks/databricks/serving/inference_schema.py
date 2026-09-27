"""Canonical input schema for real-time Databricks inference."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

ITEM_FIELDS = tuple(f"item{index}" for index in range(6))
COACH_FEATURES = (
    "kda",
    "kill_participation",
    "cs_per_min",
    "gold_per_min",
    "gold_share",
    "damage_per_min",
    "damage_taken_per_min",
    "damage_share",
    "damage_efficiency",
    "vision_score_per_min",
    "wards_placed_per_min",
    "wards_killed_per_min",
    "vision_wards_bought_per_min",
    "objective_damage_per_min",
)
CONTEXT_FEATURES = ("deaths", "damage_taken_share")
IDENTITY_FIELDS = (
    "match_id",
    "champion_id",
    "champion_name",
    "team_position",
    "win",
    "game_start_datetime",
)
REQUIRED_FIELDS = IDENTITY_FIELDS + ITEM_FIELDS + COACH_FEATURES + CONTEXT_FEATURES
NUMERIC_FIELDS = ("champion_id", "deaths") + ITEM_FIELDS + COACH_FEATURES + ("damage_taken_share",)


class InferenceSchemaError(ValueError):
    """Raised when a recent-game payload cannot be normalized safely."""


def _number(value: Any, field: str, *, integer: bool = False) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise InferenceSchemaError(f"{field} must be numeric") from error
    if not math.isfinite(number):
        raise InferenceSchemaError(f"{field} must be finite")
    return int(number) if integer else number


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "1.0", "win", "w"}:
        return True
    if normalized in {"false", "0", "0.0", "loss", "l"}:
        return False
    raise InferenceSchemaError("win must be a boolean-compatible value")


def _parse_games(recent_games_json: str | Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    if isinstance(recent_games_json, str):
        try:
            parsed = json.loads(recent_games_json)
        except json.JSONDecodeError as error:
            raise InferenceSchemaError("recent_games_json is not valid JSON") from error
    else:
        parsed = recent_games_json
    if not isinstance(parsed, list):
        raise InferenceSchemaError("recent_games_json must contain a JSON array")
    if not parsed:
        raise InferenceSchemaError("recent_games_json must contain at least one game")
    if not all(isinstance(game, Mapping) for game in parsed):
        raise InferenceSchemaError("every recent game must be a JSON object")
    return list(parsed)


def normalize_game(game: Mapping[str, Any], index: int = 0) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if field not in game]
    if missing:
        raise InferenceSchemaError(
            f"recent_games[{index}] is missing required fields: {', '.join(missing)}"
        )

    normalized = {field: game.get(field) for field in REQUIRED_FIELDS}
    normalized["match_id"] = str(normalized["match_id"]).strip()
    normalized["champion_name"] = str(normalized["champion_name"]).strip()
    normalized["team_position"] = str(normalized["team_position"]).strip().upper()
    normalized["game_start_datetime"] = str(normalized["game_start_datetime"]).strip()
    normalized["win"] = _boolean(normalized["win"])

    for field in NUMERIC_FIELDS:
        normalized[field] = _number(
            normalized[field],
            field,
            integer=field in {"champion_id", "deaths", *ITEM_FIELDS},
        )

    for field in ("match_id", "champion_name", "team_position", "game_start_datetime"):
        if not normalized[field]:
            raise InferenceSchemaError(f"recent_games[{index}].{field} must not be empty")
    if normalized["champion_id"] is None or normalized["deaths"] is None:
        raise InferenceSchemaError(f"recent_games[{index}] requires champion_id and deaths")

    gold_share = normalized["gold_share"]
    damage_share = normalized["damage_share"]
    normalized["resource_efficiency"] = (
        damage_share / gold_share
        if damage_share is not None and gold_share is not None and gold_share > 0
        else None
    )
    return normalized


def normalize_recent_games(
    recent_games_json: str | Sequence[Mapping[str, Any]],
    *,
    player_id: str,
    tier: str | None = None,
) -> dict[str, Any]:
    games = [
        normalize_game(game, index) for index, game in enumerate(_parse_games(recent_games_json))
    ]
    normalized_tier = str(tier or "UNKNOWN").strip().upper() or "UNKNOWN"
    return {
        "player_id": str(player_id),
        "tier": normalized_tier,
        "recent_games": games,
        "data_quality": {
            "games_received": len(games),
            "schema_version": "recent_games_v1",
            "resource_efficiency_zero_division": sum(
                1 for game in games if game["gold_share"] in (None, 0)
            ),
        },
    }
