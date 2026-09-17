from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from .answer_generation import AnswerGenerationError, AnswerGenerator
from .config import normalize_match_count
from .official_search import (
    OfficialSearchClient,
    OfficialSearchError,
    model_context,
    safe_citations,
)
from .personal_analysis import (
    build_final_item_statistics,
    build_item_catalog,
    build_item_purchase_statistics,
    build_match_records,
    build_personal_evidence,
)
from .riot_client import RiotApiError, RiotClient
from .routing import (
    champion_candidate,
    classify_question,
    classify_question_reason,
    needs_improvement_comparison,
    needs_item_timeline,
    needs_opponent_comparison,
    needs_period_comparison,
    needs_timeline,
    requested_recent_count,
)


@dataclass
class AgentDependencies:
    entra_credential: Any
    riot_api_key: str | None = None
    riot_trust_env: bool = True
    openai_max_output_tokens: int = 700
    item_cache: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlayerContext:
    """One safely reusable Riot snapshot for multiple questions in one request."""

    target_puuid: str
    rank_entry: dict[str, Any] | None
    rows: list[dict[str, Any]]
    internals: dict[str, dict[str, Any]]
    timelines: dict[str, dict[str, Any]]
    riot_calls: int
    timeline_errors: dict[str, str] = field(default_factory=dict)


def collect_player_context(
    riot_id: str,
    tag_line: str,
    match_count: int,
    dependencies: AgentDependencies,
    *,
    include_latest_timeline: bool = False,
) -> PlayerContext:
    """Collect a single Riot snapshot without exposing account identifiers."""

    if not dependencies.riot_api_key:
        raise ValueError("riot_api_key is required")
    selected_count = normalize_match_count(match_count)
    riot = RiotClient(
        dependencies.riot_api_key.strip(),
        trust_env=dependencies.riot_trust_env,
    )
    try:
        account = riot.account_by_riot_id(riot_id.strip(), tag_line.strip())
        target_puuid = str(account.get("puuid") or "")
        if not target_puuid:
            raise RiotApiError("RIOT_ACCOUNT_NOT_FOUND", "account_v1")
        summoner = riot.summoner_by_puuid(target_puuid)
        summoner_id = str(summoner.get("id") or "")
        rank_entry = riot.solo_rank(summoner_id) if summoner_id else None
        match_ids = riot.recent_solo_match_ids(target_puuid, selected_count)
        match_payloads = [riot.match(match_id) for match_id in match_ids]
        rows, internals = build_match_records(match_payloads, target_puuid)
        timelines: dict[str, dict[str, Any]] = {}
        if include_latest_timeline:
            valid_rows = [row for row in rows if not row["is_remake"]]
            if valid_rows:
                latest_alias = valid_rows[0]["match_id"]
                raw_match_id = internals[latest_alias].get("raw_match_id")
                if raw_match_id:
                    timelines[latest_alias] = riot.timeline(str(raw_match_id))
        return PlayerContext(
            target_puuid=target_puuid,
            rank_entry=rank_entry,
            rows=rows,
            internals=internals,
            timelines=timelines,
            riot_calls=riot.usage.calls,
            timeline_errors={},
        )
    finally:
        riot.close()


def _base_result(route: str, route_reason: str | None = None) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "route": route,
        "route_reason": route_reason,
        "answer": "",
        "statistics": {},
        "citations": [],
        "official_documents": [],
        "usage": {
            "riot_calls": 0,
            "search_calls": 0,
            "openai_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
    }


def _most_played_champions(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    champion_counts = (evidence.get("statistics") or {}).get("champion_counts") or {}
    ranked = sorted(
        champion_counts.items(),
        key=lambda item: (-int(item[1]), str(item[0]).casefold()),
    )
    return [
        {"champion_name": str(champion_name), "match_count": int(count)}
        for champion_name, count in ranked[:5]
    ]


def _top_champion_from_context(context: PlayerContext | None) -> str | None:
    if context is None:
        return None
    counts: dict[str, int] = {}
    for row in context.rows:
        if row.get("is_remake"):
            continue
        name = str(row.get("champion_name") or "")
        if name:
            counts[name] = counts.get(name, 0) + 1
    if not counts:
        return None
    return sorted(counts, key=lambda name: (-counts[name], name.casefold()))[0]


def _needs_stat_derived_champion_context(question: str) -> bool:
    """Return whether a mixed question needs the player's top champion document."""

    return bool(
        "챔피언" in question
        and re.search(r"가장|제일|많이|자주|주로|모스트", question)
        and re.search(r"특징|스킬|역할|공식|플레이\s*방법", question)
        and not re.search(r"추천|아이템|룬|소환사\s*주문|패치", question)
    )


def _document_metadata(document: dict[str, Any]) -> dict[str, Any]:
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _clean_official_text(value: Any, limit: int = 420) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -\n\t")
    if not text:
        return ""
    if len(text) > limit:
        shortened = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
        text = f"{shortened}."
    elif text[-1] not in ".!?。":
        text += "."
    return text


def _metadata_text(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return _clean_official_text(value)
    return ""


def _deterministic_official_information(documents: list[dict[str, Any]]) -> str:
    if not documents:
        return ""
    document = documents[0]
    document_type = str(document.get("document_type") or "")
    title = str(document.get("title") or "공식 문서 대상")
    metadata = _document_metadata(document)
    parts: list[str] = []

    tags = metadata.get("tags")
    if isinstance(tags, list):
        safe_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
        if safe_tags:
            label = "공식 역할·태그" if document_type == "champion" else "공식 태그"
            parts.append(f"{title}의 {label}는 {', '.join(safe_tags)}입니다.")

    if document_type == "champion":
        introduction = _metadata_text(metadata, "blurb", "introduction", "description", "lore")
        if introduction:
            parts.append(f"공식 소개: {introduction}")
        passive = metadata.get("passive")
        if isinstance(passive, dict):
            passive_name = str(passive.get("name") or "기본 지속 효과")
            passive_description = _clean_official_text(passive.get("description"))
            if passive_description:
                parts.append(f"기본 지속 효과 {passive_name}: {passive_description}")
        spells = metadata.get("spells")
        if isinstance(spells, list):
            spell_names = [
                str(spell.get("name") or "").strip()
                for spell in spells
                if isinstance(spell, dict) and str(spell.get("name") or "").strip()
            ]
            if spell_names:
                parts.append(f"공식 스킬 이름은 {', '.join(spell_names[:4])}입니다.")
    elif document_type == "item":
        stats = metadata.get("stats")
        if isinstance(stats, dict) and stats:
            rendered_stats = [f"{key} {value}" for key, value in stats.items()]
            parts.append(f"{title}의 공식 능력치는 {', '.join(rendered_stats[:6])}입니다.")
        effect = _metadata_text(metadata, "description", "plaintext", "effect")
        if effect:
            parts.append(f"공식 효과: {effect}")
        gold = metadata.get("gold")
        if isinstance(gold, dict) and gold.get("total") is not None:
            parts.append(f"공식 총 가격은 {gold['total']}입니다.")

    content = _clean_official_text(document.get("content"))
    if content:
        parts.append(f"{title} 공식 문서 설명: {content}")
    return " ".join(parts)


def _clean_generated_section(value: Any) -> str:
    text = str(value or "").strip()
    text = re.split(r"(?i)\bcitations?\s*:", text, maxsplit=1)[0]
    text = re.split(r'\{\s*["\']document_id["\']\s*:', text, maxsplit=1)[0]
    text = re.sub(r"[\s\[\]{},]+$", "", text).strip()
    if text and text[-1] not in ".!?。":
        text += "."
    return text


def _deterministic_personal_analysis(evidence: dict[str, Any]) -> str:
    statistics = evidence.get("statistics") or {}
    top = (evidence.get("most_played_champions") or [{}])[0]
    champion_name = top.get("champion_name")
    champion_matches = top.get("match_count")
    match_count = statistics.get("match_count")
    win_rate = statistics.get("win_rate")
    parts = []
    if champion_name and champion_matches is not None:
        parts.append(
            f"가장 많이 플레이한 챔피언은 {champion_name}이며 "
            f"{champion_matches}경기입니다."
        )
    if match_count is not None and win_rate is not None:
        win_rate_percent = round(float(win_rate) * 100, 2)
        parts.append(f"유효 {match_count}경기의 승률은 {win_rate_percent}%입니다.")
    return " ".join(parts) or "개인 경기 통계가 계산되었습니다."


def _compose_mixed_answer(
    generated: dict[str, Any],
    evidence: dict[str, Any],
    official_documents: list[dict[str, Any]],
) -> tuple[str, dict[str, str], str]:
    personal = _clean_generated_section(generated.get("personal_analysis"))
    if not personal:
        personal = _deterministic_personal_analysis(evidence)
    official = _deterministic_official_information(official_documents)
    combined = _clean_generated_section(generated.get("combined_advice"))
    if not combined:
        combined = "개인 경기 통계와 위 공식정보를 함께 참고해 다음 경기를 준비할 수 있습니다."
    sections = {
        "personal_analysis": personal,
        "official_information": official,
        "combined_advice": combined,
    }
    heading = (
        "공식 챔피언 특징"
        if official_documents[0].get("document_type") == "champion"
        else "공식 아이템 정보"
    )
    answer = (
        f"개인 경기 분석\n{personal}\n\n{heading}\n{official}\n\n종합\n{combined}"
    )
    return answer, sections, "model_with_deterministic_official_fallback"


def _mixed_answer_is_valid(
    answer: str,
    sections: dict[str, str],
    citations: list[dict[str, Any]],
    context_ids: set[str],
) -> bool:
    return bool(
        all(sections.values())
        and citations
        and all(str(citation.get("document_id")) in context_ids for citation in citations)
        and not re.search(r"(?i)citations?\s*:|[\[{]\s*$|document_id|source_url", answer)
        and answer.rstrip().endswith((".", "!", "?", "。"))
    )


def _is_vision_summary_question(question: str) -> bool:
    return bool(re.search(r"시야\s*점수|시야.*(?:어땠|평균)|와드.*(?:설치|제거)", question))


def _format_number(value: Any) -> str:
    if value is None:
        return "데이터 없음"
    if isinstance(value, float):
        return str(round(value, 4)).rstrip("0").rstrip(".")
    return str(value)


def _vision_answer(evidence: dict[str, Any]) -> str:
    overall = evidence.get("statistics") or {}
    recent = evidence.get("recent_5_statistics") or {}
    previous = evidence.get("previous_statistics") or {}
    lines = [
        "최근 유효 경기의 시야 통계입니다.",
        f"- 평균 시야 점수: {_format_number(overall.get('avg_vision_score'))}",
        f"- 분당 시야 점수: {_format_number(overall.get('avg_vision_per_min'))}",
        f"- 평균 와드 설치 수: {_format_number(overall.get('avg_wards_placed'))}",
        f"- 평균 와드 제거 수: {_format_number(overall.get('avg_wards_killed'))}",
    ]
    if int(recent.get("match_count") or 0) and int(previous.get("match_count") or 0):
        lines.extend(
            [
                "- 최근/이전 구간 비교:",
                "  - 최근 "
                f"{recent['match_count']}경기 시야 점수 "
                f"{_format_number(recent.get('avg_vision_score'))}, "
                f"분당 {_format_number(recent.get('avg_vision_per_min'))}",
                "  - 이전 "
                f"{previous['match_count']}경기 시야 점수 "
                f"{_format_number(previous.get('avg_vision_score'))}, "
                f"분당 {_format_number(previous.get('avg_vision_per_min'))}",
            ]
        )
    return "\n".join(lines)


def _valid_context_rows(context: PlayerContext, selected_count: int) -> list[dict[str, Any]]:
    return sorted(
        (row for row in context.rows[:selected_count] if not row.get("is_remake")),
        key=lambda row: int(row.get("game_start_timestamp") or 0),
        reverse=True,
    )


def _load_item_timelines(
    context: PlayerContext,
    dependencies: AgentDependencies,
    selected_count: int,
) -> int:
    if not dependencies.riot_api_key:
        raise ValueError("riot_api_key is required")
    riot = RiotClient(
        dependencies.riot_api_key.strip(),
        trust_env=dependencies.riot_trust_env,
    )
    try:
        for row in _valid_context_rows(context, selected_count):
            alias = str(row["match_id"])
            if alias in context.timelines or alias in context.timeline_errors:
                continue
            raw_match_id = context.internals.get(alias, {}).get("raw_match_id")
            if not raw_match_id:
                context.timeline_errors[alias] = "RIOT_MATCH_ID_UNAVAILABLE"
                continue
            try:
                context.timelines[alias] = riot.timeline(str(raw_match_id))
            except RiotApiError as exc:
                context.timeline_errors[alias] = exc.code
                if exc.code == "RIOT_RATE_LIMITED":
                    break
        context.riot_calls += riot.usage.calls
        return riot.usage.calls
    finally:
        riot.close()


def _item_name_map(documents: list[dict[str, Any]]) -> dict[str, str]:
    return {
        str(document.get("source_id") or ""): str(document.get("title") or "")
        for document in documents
        if str(document.get("source_id") or "")
    }


def _item_purchase_frequency_answer(statistics: dict[str, Any]) -> str:
    name = statistics.get("item_name") or f"아이템 {statistics.get('item_id')}"
    analyzed = int(statistics.get("matches_analyzed") or 0)
    purchased = int(statistics.get("matches_purchased") or 0)
    rate = float(statistics.get("purchase_match_rate") or 0) * 100
    finished = int(statistics.get("matches_finished_with_item") or 0)
    events = int(statistics.get("total_purchase_events") or 0)
    unavailable = int(statistics.get("timeline_matches_unavailable") or 0)
    return (
        f"최근 유효 {analyzed}경기 중 {purchased}경기에서 {name}을(를) 구매했습니다"
        f"({rate:.1f}%). 총 구매 이벤트는 {events}회이며, "
        f"최종 인벤토리에 남아 있던 경기는 {finished}경기입니다. "
        f"Timeline을 확인하지 못한 경기는 {unavailable}경기입니다."
    )


def _item_purchase_timing_answer(statistics: dict[str, Any]) -> str:
    name = statistics.get("item_name") or f"아이템 {statistics.get('item_id')}"
    purchased_rows = [
        row for row in statistics.get("per_match_purchase_times") or [] if row.get("purchased")
    ]
    if not purchased_rows:
        return (
            f"최근 유효 {statistics.get('matches_analyzed', 0)}경기의 Timeline에서 "
            f"{name} 구매 기록은 0회였습니다."
        )
    lines = [
        f"최근 유효 {statistics.get('matches_analyzed', 0)}경기에서 확인한 "
        f"{name} 첫 구매 시점입니다."
    ]
    for row in purchased_rows:
        first = (row.get("purchase_times_display") or ["데이터 없음"])[0]
        lines.append(f"- {row['match_id']} — {first}")
    if statistics.get("first_purchase_average_display"):
        lines.append(f"- 평균 첫 구매 시점: {statistics['first_purchase_average_display']}")
    return "\n".join(lines)


def _ranked_item_line(label: str, ranking: list[dict[str, Any]]) -> str:
    if not ranking:
        return f"{label}: 해당 없음"
    top = ranking[0]
    name = top.get("item_name") or f"아이템 {top.get('item_id')}"
    rate = float(top.get("purchase_match_rate") or 0) * 100
    return (
        f"{label}: {name} — {top.get('matches_purchased', 0)}경기 "
        f"({rate:.1f}%), 총 {top.get('total_purchase_events', 0)}회"
    )


def _most_common_item_answer(statistics: dict[str, Any]) -> str:
    if not statistics.get("most_common_purchased_items"):
        return "최근 유효 경기의 Timeline에서 아이템 구매 기록을 확인하지 못했습니다."
    lines = [f"최근 유효 {statistics.get('matches_analyzed', 0)}경기 구매 순위입니다."]
    for label, key in (
        ("전체 구매 1위", "most_common_purchased_items"),
        ("완성 장비 1위", "most_common_completed_equipment"),
        ("조합 재료 1위", "most_common_components"),
        ("소모품 1위", "most_common_consumables"),
        ("장신구 1위", "most_common_trinkets"),
        ("신발 1위", "most_common_boots"),
    ):
        lines.append(f"- {_ranked_item_line(label, statistics.get(key) or [])}")
    return "\n".join(lines)


def _category_item_answer(statistics: dict[str, Any], key: str, label: str) -> str:
    return (
        f"최근 유효 {statistics.get('matches_analyzed', 0)}경기 기준입니다.\n"
        f"- {_ranked_item_line(label, statistics.get(key) or [])}"
    )


def _final_build_answer(statistics: dict[str, Any]) -> str:
    lines = [f"최근 유효 {statistics.get('matches_analyzed', 0)}경기의 최종 아이템입니다."]
    for row in statistics.get("per_match_final_items") or []:
        names = []
        for item in row.get("items") or []:
            name = item.get("item_name") or f"아이템 {item.get('item_id')}"
            category = str(item.get("category") or "unknown")
            if item.get("is_boots"):
                category = f"{category}/boots"
            names.append(f"{name} [{category}]")
        lines.append(f"- {row['match_id']}: {', '.join(names) if names else '빈 인벤토리'}")
    return "\n".join(lines)


def _is_item_purchase_timing_question(question: str) -> bool:
    return bool(re.search(r"몇\s*분|언제.*(?:샀|구매)|(?:샀|구매).*언제", question))


def _is_item_purchase_frequency_question(question: str) -> bool:
    return bool(
        re.search(
            r"(?:자주|얼마나|몇\s*경기).*(?:샀|구매)|(?:샀|구매).*(?:자주|몇\s*경기)", question
        )
    )


def _is_most_common_purchased_item_question(question: str) -> bool:
    return bool(re.search(r"(?:가장|제일)\s*자주\s*(?:산|구매한)\s*아이템", question))


def _purchase_category_key(question: str) -> tuple[str, str] | None:
    if re.search(r"장신구", question):
        return "most_common_trinkets", "장신구 1위"
    if re.search(r"소모품", question):
        return "most_common_consumables", "소모품 1위"
    if re.search(r"조합\s*재료|재료", question):
        return "most_common_components", "조합 재료 1위"
    if re.search(r"장비", question):
        return "most_common_completed_equipment", "완성 장비 1위"
    return None


def _is_final_item_build_question(question: str) -> bool:
    return bool(re.search(r"최종\s*아이템|아이템\s*빌드", question))


def answer_question(
    question: str,
    riot_id: str | None = None,
    tag_line: str | None = None,
    match_count: int = 20,
    dependencies: AgentDependencies | None = None,
    player_context: PlayerContext | None = None,
) -> dict[str, Any]:
    """Answer one free-form question using deterministic stats and grounded RAG.

    FastAPI callers can construct one :class:`AgentDependencies` object per request
    or application lifecycle and pass it without changing the orchestration contract.
    """

    question = (question or "").strip()
    if not question:
        return {
            **_base_result("official_information"),
            "status": "NO_RESULT",
            "answer": "질문을 입력해 주세요.",
        }
    if dependencies is None:
        raise ValueError("dependencies are required")
    route = classify_question(question, riot_id=riot_id, tag_line=tag_line)
    route_reason = classify_question_reason(question, riot_id=riot_id, tag_line=tag_line)
    result = _base_result(route, route_reason)
    selected_count = requested_recent_count(question, normalize_match_count(match_count))
    if needs_period_comparison(question):
        selected_count = max(selected_count, 10)
    search = OfficialSearchClient(
        dependencies.entra_credential,
        item_cache=dependencies.item_cache,
    )
    generator = AnswerGenerator(
        dependencies.entra_credential,
        max_output_tokens=dependencies.openai_max_output_tokens,
    )
    official_documents: list[dict[str, Any]] = []
    target_item_id: str | None = None
    target_item_name: str | None = None
    champion_name: str | None = None
    evidence: dict[str, Any] | None = None
    started = perf_counter()

    try:
        item_search_result = search.resolve_item_entity(question)
        if item_search_result is not None:
            result["search_query"] = item_search_result.query
            result["search_filter"] = item_search_result.search_filter
            if not item_search_result.documents:
                result.update(status="NO_DATA", answer="공식 아이템 문서를 찾을 수 없습니다.")
                return result
            official_documents = item_search_result.documents
            target_item_id = str(official_documents[0].get("source_id") or "") or None
            target_item_name = str(official_documents[0].get("title") or "") or None

        if route in {"official_information", "mixed"}:
            search_question = question
            search_result = None
            top_champion = _top_champion_from_context(player_context)
            if item_search_result is not None:
                search_result = item_search_result
            elif route == "official_information":
                search_result = None
            if search_result is not None:
                official_documents = search_result.documents
                result["search_query"] = search_result.query
                result["search_filter"] = search_result.search_filter
            elif route == "mixed" and needs_item_timeline(question):
                pass
            elif route == "mixed" and "아이템" in question:
                official_documents = search.search_by_type(
                    "체력 방어 마법 저항 보호막 생존",
                    "item",
                ).documents
            elif route == "mixed" and _needs_stat_derived_champion_context(question):
                if top_champion:
                    search_result = search.resolve_champion_by_source_id(top_champion)
                    official_documents = search_result.documents
                    result["search_query"] = search_result.query
                    result["search_filter"] = search_result.search_filter
            else:
                if (
                    route == "mixed"
                    and top_champion
                    and top_champion.casefold() not in question.casefold()
                ):
                    search_question = f"{top_champion} {question}"
                search_result = search.search(search_question)
                official_documents = search_result.documents
                result["search_query"] = search_result.query
                result["search_filter"] = search_result.search_filter
            if route == "official_information" and not official_documents:
                result.update(status="NO_RESULT", answer="공식정보 검색 결과가 없습니다.")
                return result

        candidate = champion_candidate(question) if route in {"personal_match", "mixed"} else None
        if candidate:
            champion_name, champion_documents = search.resolve_champion_name(candidate)
            if route == "mixed" and not official_documents:
                official_documents = champion_documents
            if champion_name is None:
                result.update(
                    status="NO_MATCH_DATA",
                    answer="질문에서 지정한 챔피언을 공식 문서에서 확인할 수 없습니다.",
                )
                return result

        if route in {"personal_match", "mixed"}:
            if not (riot_id or "").strip() or not (tag_line or "").strip():
                result.update(
                    status="NO_MATCH_DATA",
                    answer="개인 경기 질문에는 Riot ID와 태그라인이 필요합니다.",
                )
                return result
            if not dependencies.riot_api_key:
                result.update(
                    status="RIOT_SECRET_REQUIRED",
                    answer="Riot API Secret이 준비되지 않아 개인 경기 조회를 실행하지 않았습니다.",
                )
                return result

            context = player_context
            if context is None:
                context = collect_player_context(
                    riot_id=riot_id,
                    tag_line=tag_line,
                    match_count=selected_count,
                    dependencies=dependencies,
                    include_latest_timeline=needs_timeline(question),
                )
                result["usage"]["riot_calls"] = context.riot_calls
            rows = (
                context.rows if needs_period_comparison(question) else context.rows[:selected_count]
            )
            internals = context.internals
            target_puuid = context.target_puuid
            rank_entry = context.rank_entry
            if needs_item_timeline(question):
                result["usage"]["riot_calls"] += _load_item_timelines(
                    context,
                    dependencies,
                    selected_count,
                )
            timeline_payloads = (
                context.timelines
                if needs_timeline(question) or needs_item_timeline(question)
                else {}
            )

            evidence = build_personal_evidence(
                rows=rows,
                internals=internals,
                target_puuid=target_puuid,
                champion_name=champion_name,
                rank_entry=rank_entry,
                include_opponent=needs_opponent_comparison(question),
                include_improvements=needs_improvement_comparison(question),
                timelines=timeline_payloads,
            )
            evidence["most_played_champions"] = _most_played_champions(evidence)
            if evidence.get("status") == "NO_MATCH_DATA":
                result.update(
                    status="NO_MATCH_DATA",
                    answer=str(evidence.get("message") or "최근 경기 데이터가 없습니다."),
                    statistics=evidence,
                )
                return result

            if route == "mixed" and _needs_stat_derived_champion_context(question):
                ranked_champions = evidence["most_played_champions"]
                if ranked_champions and not official_documents:
                    statistics_champion = str(ranked_champions[0]["champion_name"])
                    search_result = search.resolve_champion_by_source_id(statistics_champion)
                    official_documents = search_result.documents
                    result["search_query"] = search_result.query
                    result["search_filter"] = search_result.search_filter

            if not evidence.get("statistics"):
                result.update(
                    status="FAIL",
                    answer="개인 경기 통계가 비어 있어 답변을 생성하지 않았습니다.",
                    statistics=evidence,
                )
                return result

            item_question = bool(
                target_item_id
                or needs_item_timeline(question)
                or _is_final_item_build_question(question)
            )
            if item_question:
                item_names = _item_name_map(official_documents)
                preliminary_purchase = None
                if needs_item_timeline(question):
                    preliminary_purchase = build_item_purchase_statistics(
                        rows,
                        context.timelines,
                        context.timeline_errors,
                        item_names,
                        target_item_id,
                        build_item_catalog(official_documents),
                    )
                ids_to_resolve: list[str] = []
                if target_item_id:
                    ids_to_resolve.append(target_item_id)
                if _is_final_item_build_question(question):
                    ids_to_resolve.extend(
                        str(item_id)
                        for row in rows
                        if not row.get("is_remake")
                        for item_id in row.get("final_item_ids") or []
                    )
                if preliminary_purchase is not None and not target_item_id:
                    ids_to_resolve.extend(
                        str(item.get("item_id") or "")
                        for item in preliminary_purchase.get("most_common_purchased_items") or []
                    )
                missing_ids = [
                    item_id
                    for item_id in dict.fromkeys(ids_to_resolve)
                    if item_id and item_id not in item_names
                ]
                if missing_ids:
                    resolved_items = search.resolve_items_by_ids(missing_ids)
                    result["search_query"] = resolved_items.query
                    result["search_filter"] = resolved_items.search_filter
                    item_names.update(_item_name_map(resolved_items.documents))
                    existing_document_ids = {
                        str(document.get("document_id") or "") for document in official_documents
                    }
                    official_documents.extend(
                        document
                        for document in resolved_items.documents
                        if str(document.get("document_id") or "") not in existing_document_ids
                    )

                final_statistics = build_final_item_statistics(
                    rows,
                    item_names,
                    target_item_id,
                    build_item_catalog(official_documents),
                )
                evidence["final_item_statistics"] = final_statistics
                purchase_statistics = None
                if needs_item_timeline(question):
                    purchase_statistics = build_item_purchase_statistics(
                        rows,
                        context.timelines,
                        context.timeline_errors,
                        item_names,
                        target_item_id,
                        build_item_catalog(official_documents),
                    )
                    evidence["item_purchase_statistics"] = purchase_statistics

                if route == "mixed" and not target_item_id and purchase_statistics:
                    ranking = purchase_statistics.get("most_common_purchased_items") or []
                    if ranking:
                        target_item_id = str(ranking[0].get("item_id") or "") or None
                        target_item_name = ranking[0].get("item_name")
                        official_documents = [
                            document
                            for document in official_documents
                            if str(document.get("source_id") or "") == target_item_id
                        ]
                        evidence["interpreted_item"] = {
                            "item_id": target_item_id,
                            "item_name": target_item_name,
                            "limitation": (
                                "실제 구매 의도는 데이터로 확인할 수 없으며, 경기 기록과 "
                                "공식 아이템 효과를 바탕으로만 해석합니다."
                            ),
                        }

                deterministic_answer = None
                if route == "personal_match" and target_item_id:
                    if _is_item_purchase_timing_question(question) and purchase_statistics:
                        deterministic_answer = _item_purchase_timing_answer(purchase_statistics)
                    elif _is_item_purchase_frequency_question(question) and purchase_statistics:
                        deterministic_answer = _item_purchase_frequency_answer(purchase_statistics)
                if route == "personal_match" and _is_most_common_purchased_item_question(question):
                    deterministic_answer = _most_common_item_answer(purchase_statistics or {})
                category_request = _purchase_category_key(question)
                if route == "personal_match" and category_request and purchase_statistics:
                    key, label = category_request
                    deterministic_answer = _category_item_answer(purchase_statistics, key, label)
                if route == "personal_match" and _is_final_item_build_question(question):
                    deterministic_answer = _final_build_answer(final_statistics)
                if deterministic_answer is not None:
                    result.update(
                        status="PASS",
                        answer=deterministic_answer,
                        statistics=evidence,
                        citations=safe_citations(official_documents),
                        official_documents=safe_citations(official_documents),
                    )
                    return result

            if route == "personal_match" and _is_vision_summary_question(question):
                result.update(
                    status="PASS",
                    answer=_vision_answer(evidence),
                    statistics=evidence,
                )
                return result

        if route == "mixed" and not official_documents:
            result.update(
                status="PARTIAL",
                answer="개인 경기 통계는 확인했지만 요청한 공식정보 문서를 찾지 못했습니다.",
                statistics=evidence or {},
            )
            return result

        generated = generator.generate(
            question=question,
            route=route,
            statistics=evidence,
            official_context=model_context(official_documents),
        )
        context_ids = {str(doc.get("document_id")) for doc in official_documents}
        grounded_citations = [
            citation
            for citation in generated.get("citations", [])
            if str(citation.get("document_id")) in context_ids
        ]
        if route == "mixed" and not grounded_citations:
            grounded_citations = safe_citations(official_documents)
        if route == "mixed":
            answer, sections, generation_mode = _compose_mixed_answer(
                generated,
                evidence or {},
                official_documents,
            )
            if evidence is not None:
                evidence["generation_metadata"] = {"generation_mode": generation_mode}
            status = (
                "PASS"
                if _mixed_answer_is_valid(answer, sections, grounded_citations, context_ids)
                else "PARTIAL"
            )
            result.update(
                status=status,
                answer=answer,
                statistics=evidence or {},
                citations=grounded_citations,
                official_documents=safe_citations(official_documents),
            )
            return result
        result.update(
            status="PASS",
            answer=str(generated.get("answer") or ""),
            statistics=evidence or {},
            citations=grounded_citations,
            official_documents=safe_citations(official_documents),
        )
        return result
    except RiotApiError as exc:
        result.update(
            status="FAIL",
            answer=f"Riot API 요청을 완료하지 못했습니다: {exc.code}",
            error_code=exc.code,
            stage=exc.stage,
            http_status=exc.http_status,
            exception_class=exc.exception_class,
            inner_exception_class=exc.inner_exception_class,
        )
        return result
    except OfficialSearchError:
        result.update(
            status="AUTH_BLOCKED", answer="Azure Entra ID 인증 또는 서비스 호출이 차단되었습니다."
        )
        return result
    except AnswerGenerationError as exc:
        result.update(
            status="FAIL",
            answer="Azure OpenAI 답변 생성을 완료하지 못했습니다.",
            statistics=evidence or {},
            official_documents=safe_citations(official_documents),
            error_code=str(exc),
        )
        return result
    except Exception:
        result.update(
            status="FAIL", answer="요청 처리 중 안전하게 보고할 수 있는 오류가 발생했습니다."
        )
        return result
    finally:
        result["usage"].update(
            {
                "search_calls": search.calls,
                "openai_calls": generator.calls,
                "input_tokens": generator.input_tokens,
                "output_tokens": generator.output_tokens,
                "elapsed_ms": round((perf_counter() - started) * 1000, 1),
            }
        )
