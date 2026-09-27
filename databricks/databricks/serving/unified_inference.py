"""Pure-Python orchestration for the single-call unified inference pipeline."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from copy import deepcopy
from typing import Any

from .combat_context_engine import build_combat_context
from .combat_benchmark import build_combat_benchmark_context
from .final_candidate_assembler import assemble_final_candidates
from .inference_adapters import AnalysisDataAdapter, LLMClient
from .inference_schema import InferenceSchemaError, normalize_recent_games
from .playstyle import (
    ArtifactCompatibilityError,
    create_playstyle_analyzer,
)
from .priority_engine import build_priority_candidates
from .tactical_role_engine import (
    UNKNOWN_ROLE,
    infer_tactical_role,
    role_analysis_policy,
)
from .unified_prompt import (
    LLMOutputValidationError,
    build_unified_prompt,
    validate_llm_response,
)


EventHook = Callable[[str], None]


def _emit(hook: EventHook | None, event: str) -> None:
    if hook:
        hook(event)


def _mode(values: list[str], fallback: str) -> str:
    if not values:
        return fallback
    counts = Counter(values)
    top_count = max(counts.values())
    winners = sorted(value for value, count in counts.items() if count == top_count)
    return winners[0] if len(winners) == 1 else fallback


def _compact_playstyle(result: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {"_debug_scores"}
    return {key: deepcopy(value) for key, value in result.items() if key not in excluded}


def _error_result(
    *,
    code: str,
    message: str,
    player_id: str,
    tier: str,
    data_quality: Mapping[str, Any] | None = None,
    analysis: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "error",
        "error": {"code": code, "message": message},
        "player": {"player_id": player_id, "tier": tier},
        "analysis": deepcopy(dict(analysis or {})),
        "coaching": None,
        "data_quality": deepcopy(dict(data_quality or {})),
    }


def run_unified_inference(
    *,
    player_id: str,
    recent_games_json: str | list[dict[str, Any]],
    tier: str | None,
    data_adapter: AnalysisDataAdapter,
    llm_client: LLMClient,
    playstyle_analyzer: Any | None = None,
    playstyle_analyzer_factory: Callable[[], Any] = create_playstyle_analyzer,
    event_hook: EventHook | None = None,
) -> dict[str, Any]:
    """Run all deterministic analysis, call the LLM once, then validate once."""
    normalized_tier = str(tier or "UNKNOWN").strip().upper() or "UNKNOWN"
    try:
        canonical = normalize_recent_games(
            recent_games_json, player_id=player_id, tier=normalized_tier
        )
    except InferenceSchemaError as error:
        return _error_result(
            code="invalid_inference_schema",
            message=str(error),
            player_id=player_id,
            tier=normalized_tier,
        )
    _emit(event_hook, "inference_schema")
    games = canonical["recent_games"]

    _emit(event_hook, "context_lookup")
    try:
        item_role_map = data_adapter.get_item_role_map()
        champion_names = {game["champion_name"] for game in games}
        profiles = data_adapter.get_champion_profiles(champion_names)
        benchmark_rows = data_adapter.get_benchmark_rows()
        rf_rows = data_adapter.get_rf_importance_rows()
        direction_rows = data_adapter.get_direction_rows()
    except Exception as error:
        return _error_result(
            code="analysis_artifact_error",
            message=str(error),
            player_id=player_id,
            tier=normalized_tier,
            data_quality=canonical["data_quality"],
        )

    role_results = []
    for game in games:
        profile = profiles.get(game["champion_name"], {})
        role_results.append(
            infer_tactical_role(
                [game[f"item{index}"] for index in range(6)],
                item_role_map,
                profile.get("default_tactical_role"),
            )
        )
    valid_roles = [
        result["tactical_role"] for result in role_results
        if result["tactical_role"] != UNKNOWN_ROLE
    ]
    tactical_role = _mode(valid_roles, UNKNOWN_ROLE)
    role_policy = role_analysis_policy(
        {
            "tactical_role": tactical_role,
            "role_source": "recent_games_majority" if valid_roles else "unknown",
        }
    )

    main_position = _mode([game["team_position"] for game in games], "UNKNOWN")
    main_champion = _mode([game["champion_name"] for game in games], "UNKNOWN")
    main_profile = profiles.get(main_champion, {})
    power_curve = str(main_profile.get("power_curve") or "ALL")

    _emit(event_hook, "priority_engine")
    priority_output = build_priority_candidates(
        games,
        tactical_role=tactical_role,
        power_curve=power_curve,
        tier=normalized_tier,
        benchmark_rows=benchmark_rows,
        rf_importance_rows=rf_rows,
        direction_rows=direction_rows,
        team_position=main_position,
    )

    _emit(event_hook, "playstyle_analyzer")
    try:
        analyzer = playstyle_analyzer or playstyle_analyzer_factory()
        playstyle_output = analyzer.analyze(games)
    except (ArtifactCompatibilityError, FileNotFoundError) as error:
        return _error_result(
            code="playstyle_artifact_error",
            message=str(error),
            player_id=player_id,
            tier=normalized_tier,
            data_quality={**canonical["data_quality"], **role_policy["data_quality"]},
            analysis={"priority": priority_output},
        )
    except Exception as error:
        return _error_result(
            code="playstyle_analysis_error",
            message=str(error),
            player_id=player_id,
            tier=normalized_tier,
            data_quality={**canonical["data_quality"], **role_policy["data_quality"]},
            analysis={"priority": priority_output},
        )

    combat_benchmark = build_combat_benchmark_context(
        games,
        benchmark_rows=benchmark_rows,
        team_position=main_position,
        tier=normalized_tier,
        power_curve=power_curve,
        priority_candidates=priority_output.get("priority_candidates") or [],
    )
    combat_benchmark_gaps = combat_benchmark["benchmark_gaps"]

    _emit(event_hook, "combat_context_engine")
    combat_context = build_combat_context(
        games,
        tactical_role=tactical_role,
        damage_taken_benchmark_gap=combat_benchmark_gaps.get("damage_taken_per_min"),
        benchmark_gaps=combat_benchmark_gaps,
    )
    combat_context["deaths_benchmark"] = combat_benchmark["deaths_benchmark"]

    _emit(event_hook, "final_candidate_assembler")
    assembled = assemble_final_candidates(
        priority_output,
        playstyle_output,
        combat_context,
    )

    data_quality = {
        **canonical["data_quality"],
        **role_policy["data_quality"],
        "tier": normalized_tier,
        "benchmark_scope": priority_output.get("benchmark_scope"),
        "priority_mode": priority_output.get("priority_mode"),
        "games_with_unknown_role": sum(
            result["tactical_role"] == UNKNOWN_ROLE for result in role_results
        ),
        "deaths_benchmark_available": combat_benchmark["deaths_benchmark"]["available"],
        "deaths_assessment_basis": combat_benchmark["deaths_benchmark"]["assessment_basis"],
    }
    player = {
        "player_id": player_id,
        "main_champion": main_champion,
        "main_position": main_position,
        "tier": normalized_tier,
        "tactical_role": tactical_role,
        "power_curve": power_curve,
    }
    playstyle_context = {
        "play_style": playstyle_output.get("play_style"),
        "style_confidence": playstyle_output.get("style_confidence"),
        "style_evidence": playstyle_output.get("style_evidence"),
        "coaching_mode": playstyle_output.get("coaching_mode"),
    }

    _emit(event_hook, "build_unified_prompt")
    prompt = build_unified_prompt(
        player_context=player,
        playstyle_context=playstyle_context,
        assembled_result=assembled,
        data_quality=data_quality,
    )

    # The only LLM call in the pipeline. Validation never retries or calls it again.
    _emit(event_hook, "llm_call")
    try:
        raw_coaching = llm_client.generate(
            system_prompt=prompt["system"], user_prompt=prompt["user"]
        )
    except Exception as error:
        return _error_result(
            code="llm_call_error",
            message=str(error),
            player_id=player_id,
            tier=normalized_tier,
            data_quality=data_quality,
            analysis={"assembled": assembled},
        )

    _emit(event_hook, "validate_llm_response")
    try:
        coaching = validate_llm_response(raw_coaching, assembled_result=assembled)
    except LLMOutputValidationError as error:
        return {
            "status": "validation_error",
            "error": error.as_error()["error"],
            "player": player,
            "analysis": {
                "tactical_role": role_policy,
                "power_curve": {"value": power_curve, "source": "champion_profile_data_driven"},
                "priority": priority_output,
                "playstyle": _compact_playstyle(playstyle_output),
                "combat_context": combat_context,
                "assembled": assembled,
            },
            "coaching": None,
            "data_quality": data_quality,
        }

    return {
        "status": "ok",
        "player": player,
        "analysis": {
            "tactical_role": role_policy,
            "power_curve": {"value": power_curve, "source": "champion_profile_data_driven"},
            "priority": priority_output,
            "playstyle": _compact_playstyle(playstyle_output),
            "combat_context": combat_context,
            "assembled": assembled,
        },
        "coaching": coaching,
        "data_quality": data_quality,
    }


__all__ = ["run_unified_inference"]
