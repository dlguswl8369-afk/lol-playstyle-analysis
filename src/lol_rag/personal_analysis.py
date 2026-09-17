from __future__ import annotations

import json
from collections import Counter
from statistics import fmean
from typing import Any

from .config import REMAKE_SECONDS, STANDARD_POSITIONS

ITEM_CATEGORIES = {
    "completed_equipment",
    "component",
    "consumable",
    "trinket",
    "unknown",
}


def _item_metadata(document: dict[str, Any]) -> dict[str, Any] | None:
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def classify_item_document(document: dict[str, Any] | None) -> dict[str, Any]:
    """Classify one item from official Data Dragon metadata without name guessing."""

    metadata = _item_metadata(document or {})
    if not metadata:
        return {"category": "unknown", "is_boots": False}
    tags = {str(tag) for tag in metadata.get("tags") or []}
    consumed = metadata.get("consumed") is True
    if "Trinket" in tags:
        return {"category": "trinket", "is_boots": False}
    if consumed or "Consumable" in tags:
        return {"category": "consumable", "is_boots": False}
    is_boots = "Boots" in tags
    if is_boots:
        return {"category": "completed_equipment", "is_boots": True}
    if metadata.get("into_items") or metadata.get("into"):
        return {"category": "component", "is_boots": False}
    return {"category": "completed_equipment", "is_boots": False}


def build_item_catalog(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    for document in documents:
        item_id = str(document.get("source_id") or "")
        if not item_id:
            continue
        classification = classify_item_document(document)
        catalog[item_id] = {
            "item_id": item_id,
            "item_name": str(document.get("title") or "") or None,
            **classification,
        }
    return catalog


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
    final_item_slots = []
    for slot in range(7):
        item_id = int(participant.get(f"item{slot}") or 0)
        if item_id == 0:
            continue
        final_item_slots.append(
            {
                "slot": slot,
                "item_id": str(item_id),
                "is_trinket": slot == 6,
                "is_consumable": None,
            }
        )
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
        "final_item_slots": final_item_slots,
        "final_item_ids": [item["item_id"] for item in final_item_slots],
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


def build_final_item_statistics(
    rows: list[dict[str, Any]],
    item_names: dict[str, str] | None = None,
    target_item_id: str | None = None,
    item_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item_names = item_names or {}
    item_catalog = item_catalog or {}
    valid = sorted(
        (row for row in rows if not row.get("is_remake")),
        key=lambda row: int(row.get("game_start_timestamp") or 0),
        reverse=True,
    )
    match_counts: Counter[str] = Counter()
    per_match: list[dict[str, Any]] = []
    for row in valid:
        slots = []
        unique_ids: set[str] = set()
        for item in row.get("final_item_slots") or []:
            item_id = str(item.get("item_id") or "")
            if not item_id:
                continue
            unique_ids.add(item_id)
            slots.append(
                {
                    "slot": int(item.get("slot") or 0),
                    "item_id": item_id,
                    "item_name": item_names.get(item_id),
                    "category": item_catalog.get(item_id, {}).get("category", "unknown"),
                    "is_boots": bool(item_catalog.get(item_id, {}).get("is_boots")),
                    "is_trinket": (
                        item_catalog.get(item_id, {}).get("category") == "trinket"
                        or bool(item.get("is_trinket"))
                    ),
                    "is_consumable": (
                        item_catalog.get(item_id, {}).get("category") == "consumable"
                    ),
                }
            )
        match_counts.update(unique_ids)
        per_match.append({"match_id": row["match_id"], "items": slots})

    matches_analyzed = len(valid)
    most_common = [
        {
            "item_id": item_id,
            "item_name": item_names.get(item_id),
            "category": item_catalog.get(item_id, {}).get("category", "unknown"),
            "is_boots": bool(item_catalog.get(item_id, {}).get("is_boots")),
            "matches_finished_with_item": count,
            "final_inventory_rate": round(count / matches_analyzed, 4) if matches_analyzed else 0.0,
        }
        for item_id, count in sorted(
            match_counts.items(),
            key=lambda pair: (-pair[1], item_names.get(pair[0]) or pair[0]),
        )
    ]
    target_count = match_counts.get(str(target_item_id), 0) if target_item_id else None
    return {
        "matches_analyzed": matches_analyzed,
        "item_id": str(target_item_id) if target_item_id else None,
        "item_name": item_names.get(str(target_item_id)) if target_item_id else None,
        "matches_finished_with_item": target_count,
        "final_inventory_rate": round(target_count / matches_analyzed, 4)
        if target_item_id and matches_analyzed
        else (0.0 if target_item_id else None),
        "per_match_final_items": per_match,
        "most_common_final_items": most_common,
    }


def _timeline_events(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    events = [
        event
        for frame in timeline.get("info", {}).get("frames", [])
        for event in frame.get("events", [])
        if event.get("type") in {"ITEM_PURCHASED", "ITEM_SOLD", "ITEM_DESTROYED", "ITEM_UNDO"}
    ]
    return sorted(events, key=lambda event: int(event.get("timestamp") or 0))


def _display_seconds(seconds: int | float | None) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}분 {total % 60}초"


def item_purchase_events(timeline: dict[str, Any], participant_id: int) -> dict[str, Any]:
    purchases: list[dict[str, Any]] = []
    uncertain_undos: list[dict[str, Any]] = []
    event_counts: Counter[str] = Counter()
    for event in _timeline_events(timeline):
        if int(event.get("participantId") or 0) != int(participant_id):
            continue
        event_type = str(event.get("type") or "")
        event_counts[event_type] += 1
        timestamp_ms = int(event.get("timestamp") or 0)
        if event_type == "ITEM_PURCHASED":
            item_id = str(int(event.get("itemId") or 0))
            if item_id != "0":
                purchases.append(
                    {
                        "item_id": item_id,
                        "timestamp_seconds": timestamp_ms // 1000,
                        "undone": False,
                    }
                )
            continue
        if event_type != "ITEM_UNDO":
            continue
        before_id = str(int(event.get("beforeId") or 0))
        after_id = str(int(event.get("afterId") or 0))
        if before_id != "0" and after_id == "0":
            matched = next(
                (
                    purchase
                    for purchase in reversed(purchases)
                    if purchase["item_id"] == before_id and not purchase["undone"]
                ),
                None,
            )
            if matched is not None:
                matched["undone"] = True
                continue
        uncertain_undos.append(
            {
                "timestamp_seconds": timestamp_ms // 1000,
                "before_id": before_id,
                "after_id": after_id,
                "status": "uncertain",
            }
        )
    valid_purchases = [purchase for purchase in purchases if not purchase["undone"]]
    return {
        "purchases": valid_purchases,
        "undone_purchase_count": len(purchases) - len(valid_purchases),
        "uncertain_undos": uncertain_undos,
        "event_counts": dict(event_counts),
    }


def build_item_purchase_statistics(
    rows: list[dict[str, Any]],
    timelines: dict[str, dict[str, Any]],
    timeline_errors: dict[str, str] | None = None,
    item_names: dict[str, str] | None = None,
    target_item_id: str | None = None,
    item_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    timeline_errors = timeline_errors or {}
    item_names = item_names or {}
    item_catalog = item_catalog or {}
    valid = sorted(
        (row for row in rows if not row.get("is_remake")),
        key=lambda row: int(row.get("game_start_timestamp") or 0),
        reverse=True,
    )
    item_match_counts: Counter[str] = Counter()
    item_event_counts: Counter[str] = Counter()
    first_times: dict[str, list[tuple[str, int]]] = {}
    per_match_all: list[dict[str, Any]] = []
    uncertain_undos: list[dict[str, Any]] = []

    for row in valid:
        alias = str(row["match_id"])
        if alias not in timelines:
            per_match_all.append(
                {
                    "match_id": alias,
                    "timeline_status": timeline_errors.get(alias, "unavailable"),
                    "purchases": [],
                }
            )
            continue
        parsed = item_purchase_events(timelines[alias], int(row.get("participant_id") or 0))
        purchases = parsed["purchases"]
        grouped: dict[str, list[int]] = {}
        for purchase in purchases:
            grouped.setdefault(purchase["item_id"], []).append(purchase["timestamp_seconds"])
        for item_id, timestamps in grouped.items():
            item_match_counts[item_id] += 1
            item_event_counts[item_id] += len(timestamps)
            first_times.setdefault(item_id, []).append((alias, min(timestamps)))
        uncertain_undos.extend({"match_id": alias, **undo} for undo in parsed["uncertain_undos"])
        per_match_all.append(
            {
                "match_id": alias,
                "timeline_status": "loaded",
                "purchases": [
                    {
                        "item_id": item_id,
                        "item_name": item_names.get(item_id),
                        "category": item_catalog.get(item_id, {}).get("category", "unknown"),
                        "is_boots": bool(item_catalog.get(item_id, {}).get("is_boots")),
                        "times_seconds": timestamps,
                        "times_display": [_display_seconds(value) for value in timestamps],
                    }
                    for item_id, timestamps in sorted(grouped.items())
                ],
            }
        )

    matches_analyzed = len(valid)
    loaded = sum(alias in timelines for alias in (row["match_id"] for row in valid))
    final_counts = Counter(
        item_id
        for row in valid
        for item_id in set(str(value) for value in row.get("final_item_ids") or [])
    )
    ranking = [
        {
            "item_id": item_id,
            "item_name": item_names.get(item_id),
            "category": item_catalog.get(item_id, {}).get("category", "unknown"),
            "is_boots": bool(item_catalog.get(item_id, {}).get("is_boots")),
            "matches_purchased": count,
            "purchase_match_rate": round(count / matches_analyzed, 4) if matches_analyzed else 0.0,
            "total_purchase_events": item_event_counts[item_id],
            "matches_finished_with_item": final_counts.get(item_id, 0),
        }
        for item_id, count in sorted(
            item_match_counts.items(),
            key=lambda pair: (
                -pair[1],
                -item_event_counts[pair[0]],
                item_names.get(pair[0]) or pair[0],
            ),
        )
    ]
    target = str(target_item_id) if target_item_id else None
    target_first_times = first_times.get(target, []) if target else []
    per_match_target = []
    if target:
        for row in per_match_all:
            matching = next(
                (item for item in row["purchases"] if item["item_id"] == target),
                None,
            )
            per_match_target.append(
                {
                    "match_id": row["match_id"],
                    "timeline_status": row["timeline_status"],
                    "purchased": matching is not None,
                    "purchase_times_seconds": matching["times_seconds"] if matching else [],
                    "purchase_times_display": matching["times_display"] if matching else [],
                }
            )
    average_seconds = (
        round(fmean(value for _, value in target_first_times), 1) if target_first_times else None
    )
    by_category = {
        category: [item for item in ranking if item["category"] == category]
        for category in ITEM_CATEGORIES
    }
    boots = [item for item in ranking if item["is_boots"]]
    return {
        "matches_analyzed": matches_analyzed,
        "item_id": target,
        "item_name": item_names.get(target) if target else None,
        "matches_purchased": item_match_counts.get(target, 0) if target else None,
        "purchase_match_rate": round(item_match_counts.get(target, 0) / matches_analyzed, 4)
        if target and matches_analyzed
        else (0.0 if target else None),
        "total_purchase_events": item_event_counts.get(target, 0) if target else None,
        "first_purchase_average_seconds": average_seconds,
        "first_purchase_average_display": _display_seconds(average_seconds),
        "earliest_purchase": (
            {
                "match_id": min(target_first_times, key=lambda pair: pair[1])[0],
                "seconds": min(value for _, value in target_first_times),
                "display": _display_seconds(min(value for _, value in target_first_times)),
            }
            if target_first_times
            else None
        ),
        "latest_purchase": (
            {
                "match_id": max(target_first_times, key=lambda pair: pair[1])[0],
                "seconds": max(value for _, value in target_first_times),
                "display": _display_seconds(max(value for _, value in target_first_times)),
            }
            if target_first_times
            else None
        ),
        "per_match_purchase_times": per_match_target if target else per_match_all,
        "matches_finished_with_item": final_counts.get(target, 0) if target else None,
        "timeline_matches_loaded": loaded,
        "timeline_matches_unavailable": matches_analyzed - loaded,
        "most_common_purchased_items": ranking,
        "most_common_completed_equipment": by_category["completed_equipment"],
        "most_common_components": by_category["component"],
        "most_common_consumables": by_category["consumable"],
        "most_common_trinkets": by_category["trinket"],
        "most_common_boots": boots,
        "unknown_items": by_category["unknown"],
        "uncertain_undos": uncertain_undos,
    }


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
