from __future__ import annotations

from statistics import fmean
from typing import Any

from .opponent_comparison import compare_with_position_opponent

AGGREGATE_METRICS = (
    "kda",
    "kills",
    "deaths",
    "assists",
    "cs_per_min",
    "gold_per_min",
    "damage_per_min",
    "damage_taken_per_min",
    "vision_per_min",
    "objective_damage_per_min",
)


class MatchAnalysisService:
    def __init__(self, matches: list[dict[str, Any]], participants: list[dict[str, Any]]) -> None:
        self.matches = list(matches)
        self.participants = list(participants)

    def get_player_matches(
        self, player_id: str, champion_name: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = [row for row in self.participants if row.get("player_id") == player_id]
        if champion_name:
            folded = champion_name.casefold()
            rows = [row for row in rows if str(row.get("champion_name", "")).casefold() == folded]
        return sorted(
            rows, key=lambda row: str(row.get("game_start_utc") or ""), reverse=True
        )[:limit]

    def get_latest_champion_match(
        self, player_id: str, champion_name: str
    ) -> dict[str, Any] | None:
        rows = self.get_player_matches(player_id, champion_name, limit=1)
        return rows[0] if rows else None

    def get_match_summary(self, match_id: str, player_id: str) -> dict[str, Any]:
        rows = [
            row
            for row in self.participants
            if row.get("match_id") == match_id and row.get("player_id") == player_id
        ]
        if len(rows) != 1:
            return {"status": "unavailable", "reason": "target_not_unique", "match_id": match_id}
        match = next((row for row in self.matches if row.get("match_id") == match_id), {})
        return {"status": "ok", "match": match, "participant": rows[0]}

    def compare_with_position_opponent(self, match_id: str, player_id: str) -> dict[str, Any]:
        return compare_with_position_opponent(self.participants, match_id, player_id)

    def compare_recent_periods(
        self, player_id: str, recent_count: int = 5, previous_count: int = 15
    ) -> dict[str, Any]:
        if recent_count < 1 or previous_count < 1:
            raise ValueError("period counts must be positive")
        rows = self.get_player_matches(player_id, limit=recent_count + previous_count)
        recent = rows[:recent_count]
        previous = rows[recent_count : recent_count + previous_count]
        if len(recent) != recent_count or len(previous) != previous_count:
            return {
                "status": "unavailable",
                "reason": "insufficient_matches",
                "available_matches": len(rows),
            }
        recent_values = _aggregate(recent)
        previous_values = _aggregate(previous)
        return {
            "status": "ok",
            "recent_count": recent_count,
            "previous_count": previous_count,
            "recent": recent_values,
            "previous": previous_values,
            "delta": {
                key: round(recent_values[key] - previous_values[key], 4)
                for key in recent_values
            },
        }

    def build_personal_match_evidence(self, match_id: str, player_id: str) -> dict[str, Any]:
        summary = self.get_match_summary(match_id, player_id)
        comparison = self.compare_with_position_opponent(match_id, player_id)
        participant = summary.get("participant") or {}
        return {
            "question_type": "personal_match_comparison",
            "match_id": match_id,
            "status": summary.get("status"),
            "target": {
                "champion_name": participant.get("champion_name"),
                "team_position": participant.get("team_position"),
            },
            "opponent": comparison.get("opponent"),
            "metric_comparisons": comparison.get("metric_comparisons", []),
            "strengths": comparison.get("strengths", []),
            "weaknesses": comparison.get("weaknesses", []),
            "limitations": comparison.get("limitations", []),
            "source_tables": ["matches", "match_participants"],
        }


def safe_rate(value: Any, duration_seconds: Any) -> float | None:
    if not isinstance(value, (int, float)) or not isinstance(duration_seconds, (int, float)):
        return None
    if duration_seconds <= 0:
        return None
    return round(float(value) / (float(duration_seconds) / 60.0), 4)


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for metric in AGGREGATE_METRICS:
        values = [float(row[metric]) for row in rows if isinstance(row.get(metric), (int, float))]
        result[metric] = round(fmean(values), 4) if values else 0.0
    result["win_rate"] = round(fmean(1.0 if row.get("win") else 0.0 for row in rows), 4)
    return result
