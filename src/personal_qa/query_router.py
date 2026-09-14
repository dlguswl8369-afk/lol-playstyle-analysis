from __future__ import annotations

import re
from typing import Literal

QueryType = Literal["knowledge_query", "personal_match_query", "hybrid_query"]

PERSONAL_PATTERNS = (
    r"\b내\b",
    r"내가",
    r"나의",
    r"이번 경기",
    r"최근\s*\d+경기",
    r"최근\s*경기",
    r"이전\s*\d+경기",
    r"상대",
    r"내 경기",
)
KNOWLEDGE_PATTERNS = (
    r"공식",
    r"어떤 챔피언",
    r"아이템(?:\s*\d+)?",
    r"(?:룬|소환사 주문)",
    r"패치",
    r"\b\d{2}\.\d{1,2}\b",
    r"queue\s*(?:id\s*)?\d+",
    r"map\s*(?:id\s*)?\d+",
    r"게임 모드",
)


def classify_query(question: str) -> QueryType:
    text = question.strip().casefold()
    if not text:
        raise ValueError("question must not be empty")
    personal = any(re.search(pattern, text, re.IGNORECASE) for pattern in PERSONAL_PATTERNS)
    knowledge = any(re.search(pattern, text, re.IGNORECASE) for pattern in KNOWLEDGE_PATTERNS)
    if personal and knowledge:
        return "hybrid_query"
    if personal:
        return "personal_match_query"
    return "knowledge_query"
