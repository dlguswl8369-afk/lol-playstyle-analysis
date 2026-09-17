from __future__ import annotations

import re
from dataclasses import dataclass
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
from .personal_analysis import build_match_records, build_personal_evidence
from .riot_client import RiotApiError, RiotClient
from .routing import (
    champion_candidate,
    classify_question,
    classify_question_reason,
    needs_improvement_comparison,
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


@dataclass
class PlayerContext:
    """One safely reusable Riot snapshot for multiple questions in one request."""

    target_puuid: str
    rank_entry: dict[str, Any] | None
    rows: list[dict[str, Any]]
    internals: dict[str, dict[str, Any]]
    timelines: dict[str, dict[str, Any]]
    riot_calls: int


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
    search = OfficialSearchClient(dependencies.entra_credential)
    generator = AnswerGenerator(
        dependencies.entra_credential,
        max_output_tokens=dependencies.openai_max_output_tokens,
    )
    official_documents: list[dict[str, Any]] = []
    champion_name: str | None = None
    evidence: dict[str, Any] | None = None
    started = perf_counter()

    try:
        if route in {"official_information", "mixed"}:
            search_question = question
            search_result = None
            top_champion = _top_champion_from_context(player_context)
            recommendation_target = bool(re.search(r"아이템|룬|주문|패치", question))
            if route == "official_information":
                search_result = search.resolve_item_entity(question)
            if search_result is not None:
                official_documents = search_result.documents
                result["search_query"] = search_result.query
                result["search_filter"] = search_result.search_filter
            elif route == "mixed" and "아이템" in question:
                official_documents = search.search_by_type(
                    "체력 방어 마법 저항 보호막 생존",
                    "item",
                ).documents
            elif route == "mixed" and top_champion and not recommendation_target:
                resolved_name, documents = search.resolve_champion_name(top_champion)
                official_documents = sorted(
                    documents,
                    key=lambda document: (
                        str(document.get("source_id") or "").casefold()
                        != str(resolved_name or "").casefold()
                    ),
                )
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
            timeline_payloads = context.timelines if needs_timeline(question) else {}

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

            if not evidence.get("statistics"):
                result.update(
                    status="FAIL",
                    answer="개인 경기 통계가 비어 있어 답변을 생성하지 않았습니다.",
                    statistics=evidence,
                )
                return result

            if route == "personal_match" and _is_vision_summary_question(question):
                result.update(
                    status="PASS",
                    answer=_vision_answer(evidence),
                    statistics=evidence,
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
