from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from personal_qa.query_router import classify_query

OfficialDocumentType = Literal[
    "champion",
    "item",
    "rune",
    "summoner_spell",
    "patch_note",
    "season",
    "queue",
    "map",
    "game_mode",
    "game_type",
]
ResolutionStatus = Literal["resolved", "needs_clarification", "unsupported", "no_result"]
SearchMode = Literal["keyword", "filter_only", "none"]


@dataclass(frozen=True)
class OfficialSearchPlan:
    original_question: str
    document_type: OfficialDocumentType | None
    normalized_query: str
    search_filter: str | None
    search_mode: SearchMode
    status: ResolutionStatus
    is_ambiguous: bool = False
    ambiguity_reason: str | None = None
    clarification_question: str | None = None
    preferred_mode: str | None = None
    preferred_source_id: str | None = None

    def to_tool_input(self, *, top_k: int = 5) -> dict[str, object]:
        """Return input compatible with the existing search_game_knowledge contract."""
        if self.document_type is None:
            return {"query": self.normalized_query, "document_types": [], "top_k": top_k}
        return {
            "query": self.normalized_query,
            "document_types": [self.document_type],
            "top_k": top_k,
        }


_TYPE_PATTERNS: tuple[tuple[OfficialDocumentType, tuple[str, ...]], ...] = (
    ("game_type", (r"게임\s*타입", r"game\s*type")),
    ("game_mode", (r"게임\s*모드", r"game\s*mode")),
    ("queue", (r"queue", r"큐\s*(?:id)?\s*\d+", r"대기열")),
    ("map", (r"map", r"맵\s*(?:id)?\s*\d+")),
    ("season", (r"시즌",)),
    ("patch_note", (r"패치", r"\b\d{2}\.\d{1,2}\b")),
    ("summoner_spell", (r"소환사\s*주문", r"점멸", r"텔레포트")),
    ("rune", (r"룬", r"착취의\s*손아귀")),
    ("item", (r"아이템",)),
    ("champion", (r"챔피언", r"말파이트")),
)

_ID_PATTERNS: dict[OfficialDocumentType, re.Pattern[str]] = {
    "item": re.compile(r"아이템\s*(?:id\s*)?(\d+)", re.IGNORECASE),
    "queue": re.compile(r"(?:queue|큐)\s*(?:id\s*)?(\d+)", re.IGNORECASE),
    "map": re.compile(r"(?:map|맵)\s*(?:id\s*)?(\d+)", re.IGNORECASE),
    "season": re.compile(r"시즌\s*(?:코드\s*)?(\d+)", re.IGNORECASE),
}
_RIFT_ALIASES = ("소환사의 협곡", "협곡")
_SUMMONER_MODE_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("CHERRY", ("cherry", "아레나")),
    ("JADE", ("jade",)),
)
_FLASH_SOURCE_IDS = {
    "CLASSIC": "SummonerFlash",
    "CHERRY": "SummonerCherryFlash",
    "JADE": "SummonerFlash_Jade",
}


def classify_official_document_type(question: str) -> OfficialDocumentType | None:
    text = question.strip().casefold()
    for document_type, patterns in _TYPE_PATTERNS:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns):
            return document_type
    return None


def _escape_odata(value: str) -> str:
    return value.replace("'", "''")


def _filter(document_type: OfficialDocumentType, source_id: str | None = None) -> str:
    result = f"document_type eq '{_escape_odata(document_type)}'"
    if source_id is not None:
        result += f" and source_id eq '{_escape_odata(source_id)}'"
    return result


def _preferred_summoner_mode(question: str) -> str:
    text = question.casefold()
    for mode, aliases in _SUMMONER_MODE_ALIASES:
        if any(alias in text for alias in aliases):
            return mode
    return "CLASSIC"


def build_official_search_plan(question: str) -> OfficialSearchPlan:
    original = question.strip()
    if not original:
        raise ValueError("question must not be empty")
    if classify_query(original) != "knowledge_query":
        return OfficialSearchPlan(original, None, "", None, "none", "unsupported")

    document_type = classify_official_document_type(original)
    if document_type is None:
        return OfficialSearchPlan(original, None, "", None, "none", "unsupported")

    if document_type == "game_type" and any(alias in original for alias in _RIFT_ALIASES):
        return OfficialSearchPlan(
            original_question=original,
            document_type=document_type,
            normalized_query="*",
            search_filter=_filter(document_type),
            search_mode="filter_only",
            status="needs_clarification",
            is_ambiguous=True,
            ambiguity_reason=(
                "Map identifies the battlefield, but does not determine whether the match is "
                "MATCHED_GAME, CUSTOM_GAME, or TUTORIAL_GAME."
            ),
            clarification_question=(
                "일반 매칭, 사용자 설정 게임, 튜토리얼 중 어느 유형을 묻는지 알려주세요."
            ),
        )

    normalized_query = original
    source_id = None
    preferred_mode = None
    preferred_source_id = None
    id_pattern = _ID_PATTERNS.get(document_type)
    if id_pattern and (match := id_pattern.search(original)):
        source_id = match.group(1)
        normalized_query = source_id
    elif document_type == "game_mode" and any(alias in original for alias in _RIFT_ALIASES):
        # Internal retrieval alias only. It is not stored or presented as an official translation.
        normalized_query = "Summoner's Rift"
    elif document_type == "summoner_spell":
        preferred_mode = _preferred_summoner_mode(original)
        if "점멸" in original and "텔레포트" not in original:
            # The indexed applicable_modes arrays are empty for these Data Dragon records.
            # Use the verified official source IDs instead of a filter that would match nothing.
            preferred_source_id = _FLASH_SOURCE_IDS[preferred_mode]
            source_id = preferred_source_id
            normalized_query = "점멸"

    return OfficialSearchPlan(
        original_question=original,
        document_type=document_type,
        normalized_query=normalized_query,
        search_filter=_filter(document_type, source_id),
        search_mode="keyword",
        status="resolved",
        preferred_mode=preferred_mode,
        preferred_source_id=preferred_source_id,
    )


def finalize_search_status(plan: OfficialSearchPlan, result_count: int) -> OfficialSearchPlan:
    if result_count < 0:
        raise ValueError("result_count must not be negative")
    if plan.status == "resolved" and result_count == 0:
        return replace(plan, status="no_result")
    return plan
