from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import (
    SEARCH_API_VERSION,
    SEARCH_CONTEXT_LIMIT,
    SEARCH_ENDPOINT,
    SEARCH_INDEX,
    SEARCH_MAX_CALLS,
)

try:
    from rag.official_query import build_official_search_plan
except ImportError:  # Allows isolated static testing of this package.
    build_official_search_plan = None


class OfficialSearchError(RuntimeError):
    pass


@dataclass
class SearchResult:
    query: str
    search_filter: str | None
    documents: list[dict[str, Any]]


class OfficialSearchClient:
    def __init__(self, credential: Any, timeout_seconds: int = 30) -> None:
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self.calls = 0

    @property
    def url(self) -> str:
        return (
            f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version={SEARCH_API_VERSION}"
        )

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        if self.calls >= SEARCH_MAX_CALLS:
            raise OfficialSearchError("search_call_limit_exceeded")
        token = self._credential.get_token("https://search.azure.com/.default").token
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        self.calls += 1
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise OfficialSearchError("official_search_failed") from exc
        finally:
            token = None

    @staticmethod
    def _select() -> str:
        return ",".join(
            [
                "document_id",
                "document_type",
                "title",
                "content",
                "source_url",
                "version",
                "patch_version",
                "applicable_modes",
                "source_id",
            ]
        )

    @staticmethod
    def _special_spell_plan(question: str) -> tuple[str, str | None] | None:
        folded = question.casefold()
        if "점멸" in folded and ("순간이동" in folded or "텔레포트" in folded):
            return (
                "*",
                "document_type eq 'summoner_spell' and "
                "(source_id eq 'SummonerFlash' or source_id eq 'SummonerTeleport')",
            )
        return None

    def search(self, question: str) -> SearchResult:
        special = self._special_spell_plan(question)
        if special:
            query, search_filter = special
        elif build_official_search_plan is not None:
            plan = build_official_search_plan(question)
            query = plan.normalized_query or question
            search_filter = plan.search_filter
            if plan.status == "unsupported":
                query = question
                search_filter = None
        else:
            query, search_filter = question, None
        body = {
            "search": query,
            "top": SEARCH_CONTEXT_LIMIT,
            "select": self._select(),
            "queryType": "simple",
            "searchMode": "any",
        }
        if search_filter:
            body["filter"] = search_filter
        response = self._post(body)
        documents = list(response.get("value", []))[:SEARCH_CONTEXT_LIMIT]
        if special:
            by_source = {str(doc.get("source_id")): doc for doc in documents}
            documents = [
                by_source[source_id]
                for source_id in ("SummonerFlash", "SummonerTeleport")
                if source_id in by_source
            ]
        return SearchResult(query=query, search_filter=search_filter, documents=documents)

    def search_by_type(self, query: str, document_type: str) -> SearchResult:
        if document_type not in {"champion", "item", "rune", "summoner_spell"}:
            raise ValueError("unsupported_document_type")
        search_filter = f"document_type eq '{document_type}'"
        response = self._post(
            {
                "search": query,
                "filter": search_filter,
                "top": SEARCH_CONTEXT_LIMIT,
                "select": self._select(),
                "queryType": "simple",
                "searchMode": "any",
            }
        )
        documents = list(response.get("value", []))[:SEARCH_CONTEXT_LIMIT]
        return SearchResult(
            query=query,
            search_filter=search_filter,
            documents=documents,
        )

    @staticmethod
    def item_name_candidate(question: str) -> str | None:
        if re.search(r"아이템\s*\d+", question, re.IGNORECASE):
            return None
        if re.search(
            r"(?:룬|소환사\s*주문|패치|queue|map|게임\s*모드|어떤\s*챔피언이야)",
            question,
            re.IGNORECASE,
        ):
            return None
        patterns = (
            r"^(?:아이템\s*)?(?P<name>.+?)(?:은|는)\s*어떤\s*챔피언에게\s*어울",
            r"^(?:아이템\s*)?(?P<name>.+?)(?:은|는|이|가)?\s*(?:뭐야|무엇(?:이야|인가))",
        )
        for pattern in patterns:
            match = re.search(pattern, question.strip(), re.IGNORECASE)
            if match:
                candidate = match.group("name").strip(" ?!.,")
                return candidate or None
        return None

    @staticmethod
    def _normalized_entity(value: str) -> str:
        return re.sub(r"[^0-9A-Za-z가-힣]", "", value).casefold()

    def resolve_item_entity(self, question: str) -> SearchResult | None:
        candidate = self.item_name_candidate(question)
        if candidate is None:
            return None
        discovery_filter = "document_type eq 'item'"
        discovery = self._post(
            {
                "search": candidate,
                "filter": discovery_filter,
                "top": SEARCH_CONTEXT_LIMIT,
                "select": self._select(),
                "queryType": "simple",
                "searchMode": "any",
            }
        )
        question_normalized = self._normalized_entity(question)
        matches = [
            document
            for document in discovery.get("value", [])
            if self._normalized_entity(str(document.get("title") or ""))
            and self._normalized_entity(str(document.get("title") or "")) in question_normalized
        ]
        if not matches:
            return SearchResult(candidate, discovery_filter, [])
        exact = max(
            matches,
            key=lambda document: len(self._normalized_entity(str(document.get("title") or ""))),
        )
        source_id = str(exact.get("source_id") or "")
        if not source_id:
            return SearchResult(candidate, discovery_filter, [])
        escaped_source_id = source_id.replace("'", "''")
        exact_filter = f"document_type eq 'item' and source_id eq '{escaped_source_id}'"
        response = self._post(
            {
                "search": "*",
                "filter": exact_filter,
                "top": SEARCH_CONTEXT_LIMIT,
                "select": self._select(),
                "queryType": "simple",
                "searchMode": "any",
            }
        )
        documents = list(response.get("value", []))[:SEARCH_CONTEXT_LIMIT]
        return SearchResult("*", exact_filter, documents)

    def resolve_champion_name(self, candidate: str) -> tuple[str | None, list[dict[str, Any]]]:
        candidate = candidate.strip()
        response = self._post(
            {
                "search": candidate,
                "filter": "document_type eq 'champion'",
                "top": SEARCH_CONTEXT_LIMIT,
                "select": self._select(),
                "queryType": "simple",
                "searchMode": "any",
            }
        )
        documents = list(response.get("value", []))[:SEARCH_CONTEXT_LIMIT]
        normalized = re.sub(r"\s+", "", candidate).casefold()
        exact = next(
            (
                doc
                for doc in documents
                if re.sub(r"\s+", "", str(doc.get("title") or "")).casefold() == normalized
                or str(doc.get("source_id") or "").casefold() == candidate.casefold()
            ),
            None,
        )
        return (str(exact.get("source_id")) if exact else None), documents


def safe_citations(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(doc.get("document_id") or ""),
            "title": str(doc.get("title") or ""),
            "source_url": doc.get("source_url"),
        }
        for doc in documents[:SEARCH_CONTEXT_LIMIT]
    ]


def model_context(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "document_id": str(doc.get("document_id") or ""),
            "source_id": str(doc.get("source_id") or ""),
            "title": str(doc.get("title") or ""),
            "document_type": str(doc.get("document_type") or ""),
            "content": str(doc.get("content") or "")[:2800],
            "source_url": doc.get("source_url"),
            "patch_version": doc.get("patch_version"),
            "applicable_modes": doc.get("applicable_modes") or [],
        }
        for doc in documents[:SEARCH_CONTEXT_LIMIT]
    ]
