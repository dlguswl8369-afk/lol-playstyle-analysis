"""Pure-Python tactical-role inference shared by real-time serving and tests."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

TACTICAL_ROLES = (
    "FRONTLINE",
    "BRUISER",
    "BURST_CARRY",
    "DPS_CARRY",
    "UTILITY",
)
UNKNOWN_ROLE = "UNKNOWN"


def normalize_role(role: Any) -> str:
    value = str(role or "").strip().upper()
    return value if value in TACTICAL_ROLES else UNKNOWN_ROLE


def infer_tactical_role(
    item_ids: Iterable[Any],
    item_role_map: Mapping[int, str],
    default_role: Any = None,
) -> dict[str, Any]:
    """Infer a role by item-role majority vote without inventing a carry role.

    A valid champion default resolves a vote tie and is the fallback when no
    item supplies a valid role. If neither source supplies a role, UNKNOWN is
    returned and role-specific RF artifacts must not be used by callers.
    """
    default = normalize_role(default_role)
    votes: list[str] = []

    for item in item_ids:
        if item in (None, ""):
            continue
        try:
            mapped = item_role_map.get(int(item))
        except (TypeError, ValueError):
            continue
        if not mapped:
            continue
        for raw_role in str(mapped).split(","):
            role = normalize_role(raw_role)
            if role != UNKNOWN_ROLE:
                votes.append(role)

    if not votes:
        role = default
        source = "champion_profile" if role != UNKNOWN_ROLE else "unknown"
        return {
            "tactical_role": role,
            "role_source": source,
            "role_fallback": role == UNKNOWN_ROLE,
            "role_specific_rf_allowed": role != UNKNOWN_ROLE,
        }

    counts = Counter(votes)
    max_count = max(counts.values())
    tied = {role for role, count in counts.items() if count == max_count}
    if len(tied) == 1:
        role = next(iter(tied))
        source = "items"
    elif default in tied:
        role = default
        source = "items_tie_champion_profile"
    else:
        role = UNKNOWN_ROLE
        source = "items_tie_unknown"

    return {
        "tactical_role": role,
        "role_source": source,
        "role_fallback": role == UNKNOWN_ROLE,
        "role_specific_rf_allowed": role != UNKNOWN_ROLE,
    }


def role_analysis_policy(role_result: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the safe downstream policy and its data-quality disclosure."""
    role = normalize_role(role_result.get("tactical_role"))
    unknown = role == UNKNOWN_ROLE
    return {
        "priority_mode": "benchmark_only" if unknown else "role_specific_rf_fdr",
        "role_specific_rf_allowed": not unknown,
        "data_quality": {
            "tactical_role": role,
            "role_source": role_result.get("role_source", "unknown"),
            "role_fallback": unknown,
            "role_fallback_reason": ("no_valid_item_role_or_champion_default" if unknown else None),
        },
    }
