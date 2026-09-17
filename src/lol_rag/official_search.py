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
    def __init__(
        self,
        credential: Any,
        timeout_seconds: int = 30,
        *,
        item_cache: dict[str, Any] | None = None,
    ) -> None:
        self._credential = credential
        self._timeout_seconds = timeout_seconds
        self._item_cache = item_cache if item_cache is not None else {}
        self._item_cache.setdefault("titles", {})
        self._item_cache.setdefault("ids", {})
        self._item_cache.setdefault("misses", set())
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
                "metadata",
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
        self._cache_item_documents(
            [document for document in documents if document.get("document_type") == "item"]
        )
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
            r"(?:가장|제일)?\s*자주\s*(?:산|구매한|사용한)\s*"
            r"(?:아이템|장비|소모품|장신구|조합\s*재료)|"
            r"자주\s*구매한\s*아이템|최종\s*아이템|아이템\s*빌드|이\s*아이템",
            question,
            re.IGNORECASE,
        ):
            return None
        if re.search(
            r"(?:룬|소환사\s*주문|패치|queue|map|게임\s*모드|어떤\s*챔피언이야)",
            question,
            re.IGNORECASE,
        ):
            return None
        patterns = (
            r"^(?:내가|내)\s*(?:최근\s*경기에서\s*)?(?P<name>.+?)(?:을|를)\s*(?:자주\s*)?(?:샀|구매|몇\s*분)",
            r"^(?:아이템\s*)?(?P<name>.+?)(?:은|는)\s*어떤\s*챔피언에게\s*어울",
            r"^(?:아이템\s*)?(?P<name>.+?)(?:은|는|이|가)?\s*(?:무슨\s*아이템|왜\s*(?:써|사용))",
            r"^(?:아이템\s*)?(?P<name>.+?)(?:은|는|이|가)?\s*효과(?:를)?\s*(?:알려|설명)",
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

    def _cache_item_documents(self, documents: list[dict[str, Any]]) -> None:
        for document in documents:
            source_id = str(document.get("source_id") or "")
            normalized_title = self._normalized_entity(str(document.get("title") or ""))
            if source_id:
                self._item_cache["ids"][source_id] = document
            if normalized_title:
                self._item_cache["titles"][normalized_title] = document

    @staticmethod
    def _item_filter(source_ids: list[str]) -> str:
        clauses = [
            f"source_id eq '{source_id.replace(chr(39), chr(39) * 2)}'" for source_id in source_ids
        ]
        if len(clauses) == 1:
            return "document_type eq 'item' and " + clauses[0]
        return "document_type eq 'item' and (" + " or ".join(clauses) + ")"

    def resolve_item_entity(self, question: str) -> SearchResult | None:
        candidate = self.item_name_candidate(question)
        if candidate is None:
            return None
        candidate_normalized = self._normalized_entity(candidate)
        cached = self._item_cache["titles"].get(candidate_normalized)
        if cached:
            source_id = str(cached.get("source_id") or "")
            return SearchResult("*", self._item_filter([source_id]), [cached])
        if candidate_normalized in self._item_cache["misses"]:
            return SearchResult(candidate, "document_type eq 'item'", [])
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
        discovered_documents = list(discovery.get("value", []))
        self._cache_item_documents(discovered_documents)
        question_normalized = self._normalized_entity(question)
        matches = [
            document
            for document in discovered_documents
            if self._normalized_entity(str(document.get("title") or ""))
            and self._normalized_entity(str(document.get("title") or "")) in question_normalized
        ]
        if not matches:
            self._item_cache["misses"].add(candidate_normalized)
            return SearchResult(candidate, discovery_filter, [])
        exact = max(
            matches,
            key=lambda document: len(self._normalized_entity(str(document.get("title") or ""))),
        )
        source_id = str(exact.get("source_id") or "")
        if not source_id:
            return SearchResult(candidate, discovery_filter, [])
        exact_filter = self._item_filter([source_id])
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
        self._cache_item_documents(documents)
        return SearchResult("*", exact_filter, documents)

    def resolve_items_by_ids(self, source_ids: list[str]) -> SearchResult:
        ordered_ids = list(dict.fromkeys(str(value) for value in source_ids if str(value)))
        cached_documents = {
            source_id: self._item_cache["ids"][source_id]
            for source_id in ordered_ids
            if source_id in self._item_cache["ids"]
        }
        missing_ids = [source_id for source_id in ordered_ids if source_id not in cached_documents]
        search_filter = self._item_filter(ordered_ids) if ordered_ids else "document_type eq 'item'"
        if missing_ids:
            response = self._post(
                {
                    "search": "*",
                    "filter": self._item_filter(missing_ids),
                    "top": len(missing_ids),
                    "select": self._select(),
                    "queryType": "simple",
                    "searchMode": "any",
                }
            )
            fetched = list(response.get("value", []))
            self._cache_item_documents(fetched)
            cached_documents.update(
                {
                    str(document.get("source_id") or ""): document
                    for document in fetched
                    if str(document.get("source_id") or "")
                }
            )
        documents = [
            cached_documents[source_id]
            for source_id in ordered_ids
            if source_id in cached_documents
        ]
        return SearchResult("*", search_filter, documents)

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
