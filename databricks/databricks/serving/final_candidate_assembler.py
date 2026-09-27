"""Assemble Priority, Playstyle, and Combat Context without mixing their roles."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


METRIC_FAMILIES = {
    "growth": {"cs_per_min", "gold_per_min", "gold_share"},
    "damage_output": {
        "damage_per_min", "damage_share", "damage_efficiency", "resource_efficiency",
    },
    "survival_combat": {"kda", "deaths"},
    "participation": {"kill_participation"},
    "vision": {
        "vision_score_per_min", "wards_placed_per_min", "wards_killed_per_min",
        "vision_wards_bought_per_min",
    },
    "objective": {"objective_damage_per_min"},
    "damage_taken_context": {"damage_taken_per_min", "damage_taken_share"},
}
DAMAGE_TAKEN_KEYS = METRIC_FAMILIES["damage_taken_context"]
MAX_STRENGTHS = 3
MAX_IMPROVEMENTS = 3


def metric_family(key: str | None) -> str:
    for family, members in METRIC_FAMILIES.items():
        if key in members:
            return family
    return f"other:{key or 'unknown'}"


def _style_confidence(playstyle_output: dict[str, Any]) -> dict[str, Any]:
    return (
        playstyle_output.get("style_confidence")
        or (playstyle_output.get("play_style") or {}).get("confidence")
        or {"level": "low", "score": 0.0, "reasons": ["confidence unavailable"]}
    )


def _reliable(item: dict[str, Any], playstyle_output: dict[str, Any]) -> bool:
    if item.get("low_reliability") or item.get("is_relative"):
        return False
    reliability = item.get("reliability")
    minimum = (playstyle_output.get("reliability_filter") or {}).get("min_reliability")
    if reliability is not None and minimum is not None:
        return float(reliability) >= float(minimum)
    return True


def _priority_item(candidate: dict[str, Any], source: str, assertion: str) -> dict[str, Any]:
    key = candidate.get("key")
    return {
        "key": key,
        "metric": candidate.get("metric", key),
        "source": source,
        "metric_family": metric_family(key),
        "confidence": assertion,
        "reliability": None,
        "evidence": deepcopy(candidate),
        "prompt_hint": (
            "역할군 학습 데이터의 RF 중요도와 Pearson/FDR 연관성에 기반한 우선순위다. "
            "인과관계로 표현하지 않는다."
            if source == "v8.2_rf_fdr"
            else "역할을 확정할 수 없어 benchmark 차이만 참고한다. 확정 개선점으로 단정하지 않는다."
        ),
        "assertion_level": assertion,
    }


def _playstyle_item(item: dict[str, Any], confidence: dict[str, Any], kind: str) -> dict[str, Any]:
    key = item.get("key")
    return {
        "key": key,
        "metric": item.get("metric", key),
        "source": "ml_training_playstyle",
        "metric_family": metric_family(key),
        "confidence": deepcopy(confidence),
        "reliability": item.get("reliability"),
        "evidence": deepcopy(item),
        "prompt_hint": item.get("prompt_hint") or (
            "플레이스타일 기준의 보조 개선 후보다. v8.2 우선순위를 대체하지 않는다."
            if kind == "improvement"
            else "신뢰도와 플레이스타일 기준을 함께 밝혀 강점으로 설명한다."
        ),
        "assertion_level": "confirmed" if confidence.get("level") == "high" else "tentative",
    }


def _combat_item(candidate: dict[str, Any]) -> dict[str, Any]:
    evidence_keys = list(candidate.get("evidence_keys") or [])
    return {
        "key": candidate.get("topic", "combat_review"),
        "metric": candidate.get("topic", "combat review"),
        "source": "combat_context",
        "metric_family": (
            "damage_taken_context"
            if DAMAGE_TAKEN_KEYS.intersection(evidence_keys)
            else metric_family(evidence_keys[0] if evidence_keys else None)
        ),
        "confidence": "contextual_review",
        "reliability": None,
        "evidence": deepcopy(candidate),
        "prompt_hint": candidate.get("message_hint"),
        "assertion_level": "review_only",
        "contextual_only": True,
        "causal_claim_allowed": False,
    }


def _safe_combat_reviews(combat_context: dict[str, Any]) -> list[dict[str, Any]]:
    if combat_context.get("causal_claim_allowed") is not False:
        return []
    safe = []
    for candidate in combat_context.get("coaching_candidates") or []:
        evidence_keys = set(candidate.get("evidence_keys") or [])
        non_damage_context = evidence_keys - DAMAGE_TAKEN_KEYS
        if candidate.get("standalone_allowed") is False and non_damage_context:
            safe.append({**deepcopy(candidate), "causal_claim_allowed": False})
    return safe


def _sanitize_trend(playstyle_output: dict[str, Any]) -> dict[str, Any]:
    trend = deepcopy(playstyle_output.get("recent_trend") or {})
    if not trend:
        return {}
    if trend.get("status") == "insufficient":
        trend["changes"] = []
        return trend
    original = list(trend.get("changes") or [])
    trend["changes"] = [item for item in original if item.get("key") not in DAMAGE_TAKEN_KEYS]
    if trend.get("status") == "changed" and not trend["changes"]:
        trend["status"] = "stable"
        trend["note"] = "독립적으로 해석 가능한 임계값 이상 변화가 없습니다."
    return trend


def assemble_final_candidates(
    priority_output: dict[str, Any],
    playstyle_output: dict[str, Any],
    combat_context: dict[str, Any],
) -> dict[str, Any]:
    """Return bounded, deduplicated candidates without cross-source score mixing."""
    priority_candidates = list(priority_output.get("priority_candidates") or [])
    style_confidence = _style_confidence(playstyle_output)
    style_level = str(style_confidence.get("level", "low")).lower()
    style_state = (playstyle_output.get("play_style") or {}).get("status")

    eligible_priority = sorted(
        (
            candidate for candidate in priority_candidates
            if candidate.get("ranking_group") == "role_specific_rf_fdr"
            and candidate.get("statistically_valid") is True
            and candidate.get("primary_goal_eligible") is True
            and float(candidate.get("priority_score") or 0) > 0
            and candidate.get("standalone_allowed") is True
            and candidate.get("context_sensitive") is not True
            and candidate.get("key") not in DAMAGE_TAKEN_KEYS
        ),
        key=lambda candidate: float(candidate.get("priority_score") or 0),
        reverse=True,
    )

    primary_goal = _priority_item(eligible_priority[0], "v8.2_rf_fdr", "high") if eligible_priority else None
    improvements: list[dict[str, Any]] = []
    selected_families: set[str] = set()

    def add_improvement(item: dict[str, Any]) -> bool:
        family = item["metric_family"]
        if len(improvements) >= MAX_IMPROVEMENTS or family in selected_families:
            return False
        improvements.append(item)
        selected_families.add(family)
        return True

    # Tier 1: role-specific RF/FDR. Scores are compared only inside this tier.
    for candidate in eligible_priority:
        add_improvement(_priority_item(candidate, "v8.2_rf_fdr", "high"))

    # Tier 2: only high-confidence, reliable playstyle core weaknesses.
    if style_level == "high":
        for item in playstyle_output.get("improvements") or []:
            if (
                item.get("key") not in DAMAGE_TAKEN_KEYS
                and item.get("finding_tag") == "core_weakness"
                and _reliable(item, playstyle_output)
            ):
                add_improvement(_playstyle_item(item, style_confidence, "improvement"))

    # Tier 3: contextual review candidates; never causal claims.
    safe_reviews = _safe_combat_reviews(combat_context)
    for candidate in safe_reviews:
        add_improvement(_combat_item(candidate))

    # Tier 4: UNKNOWN-role benchmark references. Separate scale, tentative only.
    benchmark_references = sorted(
        (
            candidate for candidate in priority_candidates
            if candidate.get("ranking_group") == "benchmark_only_reference"
            and candidate.get("primary_goal_eligible") is False
            and candidate.get("standalone_allowed") is True
            and candidate.get("context_sensitive") is not True
            and candidate.get("key") not in DAMAGE_TAKEN_KEYS
            and float(candidate.get("priority_score") or 0) > 0
        ),
        key=lambda candidate: float(candidate.get("priority_score") or 0),
        reverse=True,
    )
    for candidate in benchmark_references:
        add_improvement(_priority_item(candidate, "v8.2_benchmark_reference", "reference_only"))

    strengths: list[dict[str, Any]] = []

    def add_strength(item: dict[str, Any]) -> bool:
        family = item["metric_family"]
        if len(strengths) >= MAX_STRENGTHS or family in selected_families:
            return False
        strengths.append(item)
        selected_families.add(family)
        return True

    # Reliable Playstyle strengths. Medium confidence remains explicitly tentative.
    if style_level in {"high", "medium"}:
        for item in playstyle_output.get("strengths") or []:
            if item.get("key") not in DAMAGE_TAKEN_KEYS and _reliable(item, playstyle_output):
                add_strength(_playstyle_item(item, style_confidence, "strength"))

    # Statistically valid benchmark advantages from v8.2, without causal language.
    benchmark_strengths = sorted(
        (
            candidate for candidate in priority_candidates
            if candidate.get("ranking_group") == "role_specific_rf_fdr"
            and candidate.get("statistically_valid") is True
            and candidate.get("standalone_allowed") is True
            and candidate.get("context_sensitive") is not True
            and candidate.get("key") not in DAMAGE_TAKEN_KEYS
            and float(candidate.get("direction") or 0) * float(candidate.get("gap_z") or 0) > 0
        ),
        key=lambda candidate: abs(float(candidate.get("gap_z") or 0)),
        reverse=True,
    )
    for candidate in benchmark_strengths:
        add_strength(_priority_item(candidate, "v8.2_benchmark_association", "high"))

    return {
        "strengths": strengths[:MAX_STRENGTHS],
        "improvements": improvements[:MAX_IMPROVEMENTS],
        "primary_goal": primary_goal,
        "recent_trend": _sanitize_trend(playstyle_output),
        "combat_review": safe_reviews,
        "selection_metadata": {
            "priority_source": (
                "v8.2_role_specific_rf_fdr" if eligible_priority
                else "v8.2_benchmark_reference" if benchmark_references
                else "none"
            ),
            "priority_mode": priority_output.get("priority_mode"),
            "score_scales_compared": False,
            "selection_tiers": [
                "role_specific_rf_fdr",
                "high_confidence_playstyle_core_weakness",
                "combat_review",
                "benchmark_only_reference",
            ],
            "metric_families_selected": sorted(selected_families),
            "playstyle_confidence": style_level,
            "playstyle_status": style_state,
            "ambiguous_games": (playstyle_output.get("play_style") or {}).get("ambiguous_games"),
            "style_confidence_reasons": deepcopy(style_confidence.get("reasons") or []),
            "deduplicated": True,
            "damage_taken_standalone_blocked": True,
            "combat_causal_claim_allowed": False,
        },
    }


__all__ = ["METRIC_FAMILIES", "assemble_final_candidates", "metric_family"]
