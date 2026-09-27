"""v8.2-authoritative benchmark and ML-association priority engine."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from ..offline.v8_2.benchmark_policy import select_benchmark
from .inference_schema import COACH_FEATURES
from .tactical_role_engine import UNKNOWN_ROLE, normalize_role, role_analysis_policy

CONTEXT_SENSITIVE_METRICS = {"damage_taken_per_min"}
SAFE_BENCHMARK_DIRECTIONS = {
    feature: 1 for feature in COACH_FEATURES if feature not in CONTEXT_SENSITIVE_METRICS
}


def _finite_values(games: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    values = []
    for game in games:
        value = game.get(key)
        if value is None:
            continue
        number = float(value)
        if math.isfinite(number):
            values.append(number)
    return values


def _main_value(games: Sequence[Mapping[str, Any]], key: str, fallback: str) -> str:
    values = [str(game.get(key)).strip() for game in games if game.get(key) not in (None, "")]
    return Counter(values).most_common(1)[0][0] if values else fallback


def build_priority_candidates(
    games: Sequence[Mapping[str, Any]],
    *,
    tactical_role: str,
    power_curve: str | None,
    tier: str | None,
    benchmark_rows: Sequence[Mapping[str, Any]],
    rf_importance_rows: Sequence[Mapping[str, Any]],
    direction_rows: Sequence[Mapping[str, Any]],
    team_position: str | None = None,
) -> dict[str, Any]:
    if not games:
        raise ValueError("priority analysis requires at least one game")
    position = team_position or _main_value(games, "team_position", "UNKNOWN")
    role = normalize_role(tactical_role)
    curve = str(power_curve or "ALL")
    benchmark = select_benchmark(benchmark_rows, position, tier, curve)
    role_result = {
        "tactical_role": role,
        "role_source": "provided" if role != UNKNOWN_ROLE else "unknown",
    }
    policy = role_analysis_policy(role_result)

    data_quality = {
        **policy["data_quality"],
        "priority_mode": policy["priority_mode"],
        "role_specific_rf_allowed": policy["role_specific_rf_allowed"],
        "benchmark_found": benchmark is not None,
        "benchmark_scope": benchmark.get("benchmark_scope") if benchmark else None,
    }
    if benchmark is None:
        return {
            "status": "benchmark_unavailable",
            "team_position": position,
            "tier": str(tier or "UNKNOWN").upper(),
            "power_curve": curve,
            "tactical_role": role,
            "priority_mode": policy["priority_mode"],
            "benchmark_scope": None,
            "priority_candidates": [],
            "data_quality": data_quality,
        }

    rf_map = {
        (str(row.get("tactical_role")), str(row.get("feature"))): float(
            row.get("rf_importance") or 0.0
        )
        for row in rf_importance_rows
    }
    direction_map = {
        (str(row.get("tactical_role")), str(row.get("feature"))): row for row in direction_rows
    }

    candidates = []
    for key in COACH_FEATURES:
        values = _finite_values(games, key)
        if not values:
            continue
        my_value = mean(values)
        benchmark_mean = benchmark.get(f"{key}_mean")
        benchmark_std = benchmark.get(f"{key}_std")
        if benchmark_mean is None:
            continue
        benchmark_mean = float(benchmark_mean)
        benchmark_std = float(benchmark_std or 0.0)
        gap_z = (my_value - benchmark_mean) / benchmark_std if benchmark_std > 1e-9 else 0.0
        context_sensitive = key in CONTEXT_SENSITIVE_METRICS

        if role == UNKNOWN_ROLE:
            direction = 0 if context_sensitive else SAFE_BENCHMARK_DIRECTIONS.get(key, 0)
            rf_importance = 0.0
            correlation = 0.0
            statistically_valid = False
            priority_score = max(0.0, -direction * gap_z) if direction else 0.0
        else:
            stats = direction_map.get((role, key), {})
            direction = int(stats.get("direction") or 0)
            correlation = float(stats.get("corr", stats.get("correlation", 0.0)) or 0.0)
            statistically_valid = bool(stats.get("significant", False))
            rf_importance = rf_map.get((role, key), 0.0)
            priority_score = (
                max(0.0, -direction * gap_z) * rf_importance
                if statistically_valid and direction != 0 and not context_sensitive
                else 0.0
            )

        candidates.append(
            {
                "key": key,
                "my_value": round(my_value, 6),
                "benchmark_mean": round(benchmark_mean, 6),
                "benchmark_std": round(benchmark_std, 6),
                "gap_z": round(gap_z, 6),
                "direction": direction,
                "rf_importance": round(rf_importance, 6),
                "correlation": round(correlation, 6),
                "statistically_valid": statistically_valid,
                "priority_score": round(priority_score, 6),
                "benchmark_scope": benchmark["benchmark_scope"],
                "context_sensitive": context_sensitive,
                "standalone_allowed": not context_sensitive,
                "primary_goal_eligible": (
                    role != UNKNOWN_ROLE
                    and statistically_valid
                    and priority_score > 0
                    and not context_sensitive
                ),
                "ranking_group": (
                    "benchmark_only_reference" if role == UNKNOWN_ROLE else "role_specific_rf_fdr"
                ),
                "score_comparable_to_role_specific": role != UNKNOWN_ROLE,
                "signal_type": (
                    "benchmark_deviation" if role == UNKNOWN_ROLE else "rf_fdr_association"
                ),
            }
        )

    candidates.sort(key=lambda candidate: candidate["priority_score"], reverse=True)
    return {
        "status": "ok",
        "team_position": position,
        "tier": str(tier or "UNKNOWN").upper(),
        "power_curve": curve,
        "tactical_role": role,
        "priority_mode": policy["priority_mode"],
        "benchmark_scope": benchmark["benchmark_scope"],
        "priority_candidates": candidates,
        "data_quality": data_quality,
        "ranking_policy": {
            "score_scale": (
                "benchmark_deviation_reference"
                if role == UNKNOWN_ROLE
                else "rf_importance_weighted_gap"
            ),
            "comparable_to_role_specific_rf_fdr": role != UNKNOWN_ROLE,
            "primary_goal_eligible": role != UNKNOWN_ROLE,
        },
        "method_note": (
            "RF importance and Pearson/FDR direction are association-based priority signals, "
            "not causal effects."
        ),
    }
