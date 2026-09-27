"""Benchmark scope constants and deterministic fallback selection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

BENCHMARK_SCOPES = (
    "EXACT",
    "TIER_POSITION",
    "ALL_TIER_POWER_CURVE",
    "POSITION_ONLY",
)


def benchmark_candidates(position: str, tier: str | None, power_curve: str | None):
    normalized_tier = str(tier or "UNKNOWN").strip().upper()
    normalized_curve = str(power_curve or "ALL").strip() or "ALL"
    return (
        (position, normalized_tier, normalized_curve, "EXACT"),
        (position, normalized_tier, "ALL", "TIER_POSITION"),
        (position, "ALL_TIER", normalized_curve, "ALL_TIER_POWER_CURVE"),
        (position, "ALL_TIER", "ALL", "POSITION_ONLY"),
    )


def select_benchmark(
    rows: Iterable[Mapping[str, Any]],
    position: str,
    tier: str | None,
    power_curve: str | None,
) -> Mapping[str, Any] | None:
    indexed = {
        (row.get("team_position"), row.get("tier"), row.get("power_curve")): row for row in rows
    }
    for candidate_position, candidate_tier, candidate_curve, scope in benchmark_candidates(
        position, tier, power_curve
    ):
        row = indexed.get((candidate_position, candidate_tier, candidate_curve))
        if row is not None:
            return {**row, "benchmark_scope": scope}
    return None
