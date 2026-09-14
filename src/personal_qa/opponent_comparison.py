from __future__ import annotations

from typing import Any

POSITIONS = {"TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"}
COMPARISON_METRICS = (
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
HIGHER_IS_BETTER = {
    "kda",
    "kills",
    "assists",
    "cs_per_min",
    "gold_per_min",
    "damage_per_min",
    "vision_per_min",
    "objective_damage_per_min",
}
LOWER_IS_BETTER = {"deaths"}


def compare_with_position_opponent(
    participants: list[dict[str, Any]], match_id: str, player_id: str
) -> dict[str, Any]:
    match_rows = [row for row in participants if row.get("match_id") == match_id]
    targets = [row for row in match_rows if row.get("player_id") == player_id]
    if len(targets) != 1:
        return _unavailable(match_id, "target_not_unique", "unavailable")
    target = targets[0]
    position = target.get("team_position")
    if position not in POSITIONS:
        return _unavailable(match_id, "target_position_unavailable", "unavailable")
    opponents = [
        row
        for row in match_rows
        if row.get("team_id") != target.get("team_id") and row.get("team_position") == position
    ]
    if len(opponents) != 1:
        status = "unavailable" if not opponents else "ambiguous"
        return _unavailable(match_id, f"opponent_count_{len(opponents)}", status)
    opponent = opponents[0]
    comparisons = []
    strengths = []
    weaknesses = []
    for metric in COMPARISON_METRICS:
        target_value = target.get(metric)
        opponent_value = opponent.get(metric)
        delta = None
        if isinstance(target_value, (int, float)) and isinstance(opponent_value, (int, float)):
            delta = round(float(target_value) - float(opponent_value), 4)
            favourable = (metric in HIGHER_IS_BETTER and delta > 0) or (
                metric in LOWER_IS_BETTER and delta < 0
            )
            unfavourable = (metric in HIGHER_IS_BETTER and delta < 0) or (
                metric in LOWER_IS_BETTER and delta > 0
            )
            evidence = {"metric": metric, "delta": delta}
            if favourable:
                strengths.append(evidence)
            elif unfavourable:
                weaknesses.append(evidence)
        comparisons.append(
            {
                "metric": metric,
                "target": target_value,
                "opponent": opponent_value,
                "delta": delta,
            }
        )
    return {
        "match_id": match_id,
        "opponent_match_status": "matched",
        "reason": None,
        "target": {
            "player_id": player_id,
            "champion_name": target.get("champion_name"),
            "team_position": position,
        },
        "opponent": {
            "player_id": opponent.get("player_id"),
            "champion_name": opponent.get("champion_name"),
        },
        "metric_comparisons": comparisons,
        "strengths": strengths,
        "weaknesses": weaknesses,
        "limitations": ["수치 차이는 승패의 확정적 원인을 의미하지 않습니다."],
    }


def _unavailable(match_id: str, reason: str, status: str) -> dict[str, Any]:
    return {
        "match_id": match_id,
        "opponent_match_status": status,
        "reason": reason,
        "target": None,
        "opponent": None,
        "metric_comparisons": [],
        "strengths": [],
        "weaknesses": [],
        "limitations": ["동일 포지션 상대를 정확히 한 명으로 결정할 수 없습니다."],
    }
