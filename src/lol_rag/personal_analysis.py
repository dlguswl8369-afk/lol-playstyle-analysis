from __future__ import annotations

from collections import Counter
from statistics import fmean
from typing import Any

from .config import REMAKE_SECONDS, STANDARD_POSITIONS


def _duration_seconds(info: dict[str, Any]) -> float:
    value = float(info.get("gameDuration") or 0)
    return value / 1000.0 if value > 10000 else value


def _rate(value: int | float | None, duration_seconds: float) -> float | None:
    if value is None or duration_seconds <= 0:
        return None
    return round(float(value) / (duration_seconds / 60.0), 4)


def _average(rows: list[dict[str, Any]], field: str) -> float | None:
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return round(fmean(values), 4) if values else None


def _target_participant(match: dict[str, Any], target_puuid: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in match.get("info", {}).get("participants", [])
            if item.get("puuid") == target_puuid
        ),
        None,
    )


def _safe_participant(
    participant: dict[str, Any],
    duration_seconds: float,
    match_alias: str,
    team_kills: int,
) -> dict[str, Any]:
    kills = int(participant.get("kills") or 0)
    deaths = int(participant.get("deaths") or 0)
    assists = int(participant.get("assists") or 0)
    cs = int(participant.get("totalMinionsKilled") or 0) + int(
        participant.get("neutralMinionsKilled") or 0
    )
    damage = int(participant.get("totalDamageDealtToChampions") or 0)
    damage_taken = int(participant.get("totalDamageTaken") or 0)
    vision = int(participant.get("visionScore") or 0)
    objective_damage = int(participant.get("damageDealtToObjectives") or 0)
    gold = int(participant.get("goldEarned") or 0)
    return {
        "match_id": match_alias,
        "participant_id": int(participant.get("participantId") or 0),
        "team_id": int(participant.get("teamId") or 0),
        "champion_name": str(participant.get("championName") or ""),
        "team_position": str(participant.get("teamPosition") or ""),
        "win": bool(participant.get("win")),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": round((kills + assists) / max(deaths, 1), 4),
        "kill_participation": round((kills + assists) / team_kills, 4) if team_kills else None,
        "cs": cs,
        "cs_per_min": _rate(cs, duration_seconds),
        "gold": gold,
        "gold_per_min": _rate(gold, duration_seconds),
        "champion_damage": damage,
        "damage_per_min": _rate(damage, duration_seconds),
        "damage_taken": damage_taken,
        "damage_taken_per_min": _rate(damage_taken, duration_seconds),
        "vision_score": vision,
        "vision_per_min": _rate(vision, duration_seconds),
        "wards_placed": int(participant.get("wardsPlaced") or 0),
        "wards_killed": int(participant.get("wardsKilled") or 0),
        "objective_damage": objective_damage,
        "objective_damage_per_min": _rate(objective_damage, duration_seconds),
        "game_duration_seconds": round(duration_seconds, 1),
    }


def build_match_records(
    match_payloads: list[dict[str, Any]], target_puuid: str
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    internals: dict[str, dict[str, Any]] = {}
    for index, match in enumerate(match_payloads, start=1):
        info = match.get("info", {})
        target = _target_participant(match, target_puuid)
        if target is None:
            continue
        alias = f"MATCH_{index:03d}"
        duration = _duration_seconds(info)
        target_team_id = int(target.get("teamId") or 0)
        team_kills = sum(
            int(item.get("kills") or 0)
            for item in info.get("participants", [])
            if int(item.get("teamId") or 0) == target_team_id
        )
        safe = _safe_participant(target, duration, alias, team_kills)
        safe["is_remake"] = duration < REMAKE_SECONDS
        safe["game_start_timestamp"] = int(
            info.get("gameStartTimestamp") or info.get("gameCreation") or 0
        )
        rows.append(safe)
        internals[alias] = {
            "raw_match_id": match.get("metadata", {}).get("matchId"),
            "match": match,
        }
    return rows, internals


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"match_count": 0}
    total_kills = sum(row["kills"] for row in rows)
    total_deaths = sum(row["deaths"] for row in rows)
    total_assists = sum(row["assists"] for row in rows)
    fields = (
        "kills",
        "deaths",
        "assists",
        "kill_participation",
        "cs",
        "cs_per_min",
        "gold",
        "gold_per_min",
        "champion_damage",
        "damage_per_min",
        "damage_taken",
        "damage_taken_per_min",
        "vision_score",
        "vision_per_min",
        "wards_placed",
        "wards_killed",
        "objective_damage",
        "objective_damage_per_min",
        "game_duration_seconds",
    )
    result = {"match_count": len(rows)}
    result.update({f"avg_{field}": _average(rows, field) for field in fields})
    result.update(
        {
            "wins": sum(bool(row["win"]) for row in rows),
            "losses": sum(not bool(row["win"]) for row in rows),
            "win_rate": round(sum(bool(row["win"]) for row in rows) / len(rows), 4),
            "kda": round((total_kills + total_assists) / max(total_deaths, 1), 4),
            "champion_counts": dict(Counter(row["champion_name"] for row in rows)),
            "position_counts": dict(Counter(row["team_position"] for row in rows)),
        }
    )
    return result


def identify_position_opponent(
    match: dict[str, Any], target_puuid: str, match_alias: str
) -> dict[str, Any]:
    info = match.get("info", {})
    target = _target_participant(match, target_puuid)
    if target is None:
        return {"status": "unavailable", "reason": "대상 참가자를 찾을 수 없음"}
    position = str(target.get("teamPosition") or "")
    if position not in STANDARD_POSITIONS:
        return {"status": "unavailable", "reason": "동일 포지션 상대를 정확히 식별할 수 없음"}
    candidates = [
        item
        for item in info.get("participants", [])
        if int(item.get("teamId") or 0) != int(target.get("teamId") or 0)
        and item.get("teamPosition") == position
    ]
    if len(candidates) != 1:
        return {"status": "unavailable", "reason": "동일 포지션 상대를 정확히 식별할 수 없음"}
    opponent = candidates[0]
    duration = _duration_seconds(info)
    target_team_kills = sum(
        int(item.get("kills") or 0)
        for item in info.get("participants", [])
        if int(item.get("teamId") or 0) == int(target.get("teamId") or 0)
    )
    opponent_team_kills = sum(
        int(item.get("kills") or 0)
        for item in info.get("participants", [])
        if int(item.get("teamId") or 0) == int(opponent.get("teamId") or 0)
    )
    target_safe = _safe_participant(target, duration, match_alias, target_team_kills)
    opponent_safe = _safe_participant(opponent, duration, match_alias, opponent_team_kills)
    metric_names = (
        "kda",
        "deaths",
        "assists",
        "kill_participation",
        "cs_per_min",
        "gold_per_min",
        "damage_per_min",
        "damage_taken_per_min",
        "vision_per_min",
        "wards_placed",
        "wards_killed",
        "objective_damage_per_min",
    )
    comparisons = []
    for metric in metric_names:
        left = target_safe.get(metric)
        right = opponent_safe.get(metric)
        comparisons.append(
            {
                "metric": metric,
                "target": left,
                "opponent": right,
                "delta": round(float(left) - float(right), 4)
                if isinstance(left, (int, float)) and isinstance(right, (int, float))
                else None,
            }
        )
    return {
        "status": "matched",
        "match_id": match_alias,
        "position": position,
        "target_champion": target_safe["champion_name"],
        "opponent_champion": opponent_safe["champion_name"],
        "metrics": comparisons,
        "limitation": "수치 차이는 승패 원인을 확정하지 않습니다.",
        "_target_participant_id": target_safe["participant_id"],
        "_opponent_participant_id": opponent_safe["participant_id"],
    }


def _frame_at_or_before(timeline: dict[str, Any], minute: int) -> dict[str, Any] | None:
    target_ms = minute * 60 * 1000
    frames = [
        frame
        for frame in timeline.get("info", {}).get("frames", [])
        if int(frame.get("timestamp") or 0) <= target_ms
    ]
    return max(frames, key=lambda frame: int(frame.get("timestamp") or 0)) if frames else None


def timeline_differences(
    timeline: dict[str, Any], target_participant_id: int, opponent_participant_id: int
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for minute in (10, 15):
        frame = _frame_at_or_before(timeline, minute)
        if frame is None:
            output[f"minute_{minute}"] = {"status": "unavailable"}
            continue
        participants = frame.get("participantFrames", {})
        target = participants.get(str(target_participant_id))
        opponent = participants.get(str(opponent_participant_id))
        if not target or not opponent:
            output[f"minute_{minute}"] = {"status": "unavailable"}
            continue
        target_cs = int(target.get("minionsKilled") or 0) + int(
            target.get("jungleMinionsKilled") or 0
        )
        opponent_cs = int(opponent.get("minionsKilled") or 0) + int(
            opponent.get("jungleMinionsKilled") or 0
        )
        output[f"minute_{minute}"] = {
            "status": "available",
            "selected_timestamp_ms": int(frame.get("timestamp") or 0),
            "gold_delta": int(target.get("totalGold") or 0) - int(opponent.get("totalGold") or 0),
            "cs_delta": target_cs - opponent_cs,
        }
    return output


def improvement_evidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) < 6:
        return []
    recent = aggregate(rows[:5])
    previous = aggregate(rows[5:10])
    definitions = (
        ("평균 데스", "avg_deaths", False),
        ("분당 CS", "avg_cs_per_min", True),
        ("분당 골드", "avg_gold_per_min", True),
        ("분당 챔피언 피해량", "avg_damage_per_min", True),
        ("킬 관여율", "avg_kill_participation", True),
        ("분당 시야 점수", "avg_vision_per_min", True),
    )
    candidates = []
    for label, field, higher_is_better in definitions:
        current = recent.get(field)
        baseline = previous.get(field)
        if current is None or baseline is None:
            continue
        delta = round(float(current) - float(baseline), 4)
        deterioration = -delta if higher_is_better else delta
        if deterioration > 0:
            candidates.append(
                {
                    "metric": label,
                    "recent_5": current,
                    "previous_period": baseline,
                    "delta": delta,
                    "higher_is_better": higher_is_better,
                    "deterioration": deterioration,
                }
            )
    candidates.sort(key=lambda item: item["deterioration"], reverse=True)
    return candidates[:3]


def match_highlights(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return deterministic per-match evidence without exposing Riot identifiers."""

    if not rows:
        return {}
    best = max(rows, key=lambda row: (float(row["kda"]), bool(row["win"])))
    worst = min(rows, key=lambda row: (float(row["kda"]), not bool(row["win"])))
    most_deaths = max(rows, key=lambda row: int(row["deaths"]))

    def summary(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_id": row["match_id"],
            "champion_name": row["champion_name"],
            "win": row["win"],
            "kills": row["kills"],
            "deaths": row["deaths"],
            "assists": row["assists"],
            "kda": row["kda"],
            "vision_score": row["vision_score"],
        }

    return {
        "best_match_by_kda": summary(best),
        "worst_match_by_kda": summary(worst),
        "most_deaths_match": summary(most_deaths),
        "selection_basis": "KDA 기준의 결정적 비교이며 승패 원인을 의미하지 않음",
    }


def build_personal_evidence(
    rows: list[dict[str, Any]],
    internals: dict[str, dict[str, Any]],
    target_puuid: str,
    champion_name: str | None,
    rank_entry: dict[str, Any] | None,
    include_opponent: bool,
    include_improvements: bool,
    timelines: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    valid = sorted(
        (row for row in rows if not row["is_remake"]),
        key=lambda row: int(row.get("game_start_timestamp") or 0),
        reverse=True,
    )
    selected = [
        row
        for row in valid
        if champion_name is None or row["champion_name"].casefold() == champion_name.casefold()
    ]
    if champion_name and not selected:
        return {
            "status": "NO_MATCH_DATA",
            "message": "최근 조회 경기에서 해당 챔피언 경기를 찾지 못했습니다.",
            "requested_champion": champion_name,
            "match_count": 0,
        }
    evidence: dict[str, Any] = {
        "status": "PASS" if selected else "NO_MATCH_DATA",
        "match_count": len(selected),
        "excluded_remakes": len(rows) - len(valid),
        "statistics": aggregate(selected),
        "match_ids": [row["match_id"] for row in selected],
        "rank": None,
        "match_highlights": match_highlights(selected),
        "recent_5_statistics": aggregate(selected[:5]),
        "previous_statistics": aggregate(selected[5:10]),
    }
    if rank_entry:
        evidence["rank"] = {
            "tier": rank_entry.get("tier"),
            "rank": rank_entry.get("rank"),
            "league_points": rank_entry.get("leaguePoints"),
            "wins": rank_entry.get("wins"),
            "losses": rank_entry.get("losses"),
        }
    if include_improvements:
        evidence["improvement_evidence"] = improvement_evidence(selected)
        evidence["improvement_basis"] = (
            f"최근 5경기와 그 이전 {min(max(len(selected) - 5, 0), 5)}경기의 평균 비교"
        )
    if include_opponent and selected:
        latest_alias = selected[0]["match_id"]
        internal = internals[latest_alias]
        comparison = identify_position_opponent(internal["match"], target_puuid, latest_alias)
        if comparison.get("status") == "matched" and timelines and latest_alias in timelines:
            comparison["timeline"] = timeline_differences(
                timelines[latest_alias],
                comparison.pop("_target_participant_id"),
                comparison.pop("_opponent_participant_id"),
            )
        else:
            comparison.pop("_target_participant_id", None)
            comparison.pop("_opponent_participant_id", None)
        evidence["opponent_comparison"] = comparison
    return evidence
