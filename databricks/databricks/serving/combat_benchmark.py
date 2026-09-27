"""Combat-only benchmark gaps that must never become Priority candidates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from ..offline.v8_2.benchmark_policy import select_benchmark


def build_combat_benchmark_context(
    games: Sequence[Mapping[str, Any]],
    *,
    benchmark_rows: Sequence[Mapping[str, Any]],
    team_position: str,
    tier: str | None,
    power_curve: str | None,
    priority_candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build auxiliary gaps, including deaths only when real stats exist."""
    candidate_by_key = {
        str(candidate.get("key")): candidate for candidate in priority_candidates
    }
    gaps = {
        key: (candidate_by_key.get(key) or {}).get("gap_z")
        for key in (
            "kda",
            "kill_participation",
            "damage_efficiency",
            "damage_taken_per_min",
        )
    }

    benchmark = select_benchmark(
        benchmark_rows, team_position, tier, power_curve
    )
    death_values = [
        float(game["deaths"])
        for game in games
        if game.get("deaths") is not None and math.isfinite(float(game["deaths"]))
    ]
    deaths_mean = benchmark.get("deaths_mean") if benchmark else None
    deaths_std = benchmark.get("deaths_std") if benchmark else None
    deaths_gap = None
    if death_values and deaths_mean is not None and deaths_std is not None:
        numeric_std = float(deaths_std)
        if math.isfinite(numeric_std) and numeric_std > 1e-9:
            deaths_gap = (mean(death_values) - float(deaths_mean)) / numeric_std
    gaps["deaths"] = round(deaths_gap, 6) if deaths_gap is not None else None

    return {
        "benchmark_gaps": gaps,
        "deaths_benchmark": {
            "available": deaths_gap is not None,
            "assessment_basis": (
                "benchmark_gap" if deaths_gap is not None else "heuristic_fallback"
            ),
            "benchmark_mean": float(deaths_mean) if deaths_mean is not None else None,
            "benchmark_std": float(deaths_std) if deaths_std is not None else None,
            "gap_z": gaps["deaths"],
            "benchmark_scope": benchmark.get("benchmark_scope") if benchmark else None,
        },
    }


__all__ = ["build_combat_benchmark_context"]
