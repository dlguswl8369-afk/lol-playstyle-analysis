"""Contextual combat interpretation without standalone damage-taken judgments."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from statistics import mean
from typing import Any

from .tactical_role_engine import normalize_role


COMBAT_METRICS = (
    "deaths", "kda", "kill_participation", "damage_per_min",
    "damage_taken_per_min", "damage_share", "damage_taken_share",
    "damage_efficiency", "gold_share",
)


def _average(games: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = []
    for game in games:
        value = game.get(key)
        if value is None:
            continue
        number = float(value)
        if math.isfinite(number):
            values.append(number)
    return mean(values) if values else None


def build_combat_context(
    games: Sequence[Mapping[str, Any]],
    *,
    tactical_role: str,
    damage_taken_benchmark_gap: float | None,
    benchmark_gaps: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    if not games:
        raise ValueError("combat context requires at least one game")

    averages = {key: _average(games, key) for key in COMBAT_METRICS}
    avg_deaths = averages["deaths"]
    avg_kda = averages["kda"]
    avg_kp = averages["kill_participation"]
    avg_efficiency = averages["damage_efficiency"]
    wins = [bool(game.get("win")) for game in games if game.get("win") is not None]
    role = normalize_role(tactical_role)
    supplied_gaps = dict(benchmark_gaps or {})

    def classify(
        metric: str,
        value: float | None,
        *,
        benchmark_high: float = 0.5,
        benchmark_low: float = -0.5,
        heuristic_high: float,
        heuristic_low: float,
    ) -> tuple[bool, bool, str]:
        gap = supplied_gaps.get(metric)
        if gap is not None:
            return gap >= benchmark_high, gap <= benchmark_low, "benchmark_gap"
        if value is None:
            return False, False, "unavailable"
        return value >= heuristic_high, value <= heuristic_low, "heuristic_fallback"

    high_taken = damage_taken_benchmark_gap is not None and damage_taken_benchmark_gap >= 0.5
    high_deaths, low_deaths, deaths_basis = classify(
        "deaths", avg_deaths, heuristic_high=6.0, heuristic_low=4.0
    )
    _, low_efficiency, efficiency_basis = classify(
        "damage_efficiency", avg_efficiency, heuristic_high=1.25, heuristic_low=1.0
    )
    good_kda, _, kda_basis = classify(
        "kda", avg_kda, heuristic_high=3.0, heuristic_low=1.5
    )
    good_kp, _, kp_basis = classify(
        "kill_participation", avg_kp, heuristic_high=0.55, heuristic_low=0.35
    )
    assessment_basis = {
        "damage_taken_per_min": (
            "benchmark_gap" if damage_taken_benchmark_gap is not None else "unavailable"
        ),
        "deaths": deaths_basis,
        "kda": kda_basis,
        "kill_participation": kp_basis,
        "damage_efficiency": efficiency_basis,
    }
    tank_role = role in {"FRONTLINE", "BRUISER"}
    carry_role = role in {"BURST_CARRY", "DPS_CARRY"}

    signals = []
    if high_taken:
        signals.append("damage_taken_above_benchmark")
    if high_deaths:
        signals.append("high_deaths")
    if low_deaths:
        signals.append("low_deaths")
    if low_efficiency:
        signals.append("low_damage_efficiency")
    if good_kda:
        signals.append("healthy_kda")
    if good_kp:
        signals.append("healthy_kill_participation")
    if tank_role:
        signals.append("damage_absorption_role_context")
    if carry_role:
        signals.append("carry_survival_context")

    interpretation = "no_damage_taken_interpretation"
    coaching_candidates = []
    if high_taken and high_deaths and low_efficiency:
        interpretation = "engagement_risk_review_candidate"
        coaching_candidates.append(
            {
                "type": "combat_review",
                "topic": "engagement_selection_and_survival",
                "evidence_keys": [
                    "damage_taken_per_min", "deaths", "damage_efficiency"
                ],
                "message_hint": (
                    "받은 피해, 데스, 피해 효율이 함께 불리한 패턴이므로 교전 선택과 "
                    "생존 과정을 복기할 후보입니다. 데이터만으로 진입 원인을 확정하지 않습니다."
                ),
                "standalone_allowed": False,
            }
        )
    elif high_taken and low_deaths and (good_kda or good_kp):
        interpretation = "absorbed_damage_and_survived_possible"
    elif high_taken and tank_role:
        interpretation = "role_damage_absorption_possible"
    elif high_taken and carry_role and high_deaths:
        interpretation = "carry_survival_review_candidate"
        coaching_candidates.append(
            {
                "type": "combat_review",
                "topic": "carry_survival_context",
                "evidence_keys": ["damage_taken_per_min", "deaths", "tactical_role"],
                "message_hint": (
                    "캐리 역할에서 받은 피해와 데스가 함께 높아 생존·교전 타이밍을 "
                    "점검할 후보입니다. 포지셔닝 실패로 단정하지 않습니다."
                ),
                "standalone_allowed": False,
            }
        )

    return {
        "games_analyzed": len(games),
        "tactical_role": role,
        "avg_deaths": round(avg_deaths, 6) if avg_deaths is not None else None,
        "avg_kda": round(avg_kda, 6) if avg_kda is not None else None,
        "avg_kill_participation": round(avg_kp, 6) if avg_kp is not None else None,
        "avg_damage_per_min": round(averages["damage_per_min"], 6) if averages["damage_per_min"] is not None else None,
        "avg_damage_taken_per_min": round(averages["damage_taken_per_min"], 6) if averages["damage_taken_per_min"] is not None else None,
        "avg_damage_share": round(averages["damage_share"], 6) if averages["damage_share"] is not None else None,
        "avg_damage_taken_share": round(averages["damage_taken_share"], 6) if averages["damage_taken_share"] is not None else None,
        "avg_damage_efficiency": round(avg_efficiency, 6) if avg_efficiency is not None else None,
        "avg_gold_share": round(averages["gold_share"], 6) if averages["gold_share"] is not None else None,
        "win_rate": round(sum(wins) / len(wins), 6) if wins else None,
        "damage_taken_benchmark_gap": damage_taken_benchmark_gap,
        "benchmark_gaps": {
            key: supplied_gaps.get(key)
            for key in ("deaths", "kda", "kill_participation", "damage_efficiency")
        },
        "assessment_metadata": {
            "basis_by_metric": assessment_basis,
            "deaths_benchmark_available": supplied_gaps.get("deaths") is not None,
            "deaths_assessment_basis": deaths_basis,
            "benchmark_preferred": True,
            "heuristic_fallback_used": any(
                basis == "heuristic_fallback" for basis in assessment_basis.values()
            ),
            "heuristic_thresholds_validated": False,
            "heuristic_note": (
                "Global thresholds are unvalidated fallback heuristics and are not role/position "
                "domain standards. Benchmark gaps take precedence when supplied."
            ),
        },
        "damage_taken_context": {
            "status": "contextual",
            "interpretation": interpretation,
            "signals": signals,
            "standalone_judgment": False,
            "standalone_allowed": False,
        },
        "coaching_candidates": coaching_candidates,
        "causal_claim_allowed": False,
    }
