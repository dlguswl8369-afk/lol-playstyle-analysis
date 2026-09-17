from __future__ import annotations

import re
from typing import Literal

Route = Literal["official_information", "personal_match", "mixed"]

EXPLICIT_PERSONAL_PATTERNS = (
    r"(?:^|\s)(?:내가|나의|내)(?=\s|$|가|게|를|의)",
    r"(?:최근|이번).*(?:경기|판|플레이|사용)",
    r"(?:경기|전적|티어|승률|kda|데스|킬|cs|골드|피해량|시야|플레이스타일)",
    r"(?:많이|자주|주로|모스트).*(?:한|하는|플레이|사용|챔피언)",
    r"상대\s*(?:라이너|정글러|서포터|탑|미드|원딜)",
    r"개선(?:점|해야)|고칠\s*점",
)
EXPLICIT_OFFICIAL_PATTERNS = (
    r"어떤\s*챔피언",
    r"(?:챔피언|아이템|룬|스킬|궁극기|패시브|소환사\s*주문).*(?:공식\s*)?(?:특징|효과|설명|정보)",
    r"(?:공식\s*)?(?:특징|효과|설명|정보).*(?:챔피언|아이템|룬|스킬|궁극기|패시브|소환사\s*주문)",
    r"공식\s*(?:정보|특징|효과|설명|문서|스킬)",
    r"패치(?:노트)?",
    r"(?:map|queue)\s*(?:id\s*)?\d+",
    r"(?:맵|게임\s*모드|게임\s*타입).*(?:뭐|무엇|설명|정보)",
    r"(?:점멸|순간이동|텔레포트|아레나|cherry|jade).*(?:효과|차이|설명|정보)",
    r"아이템\s*\d+",
    r"(?:사용할\s*만한|어울리는|추천).*(?:챔피언|아이템|룬)",
    r"(?:챔피언|아이템|룬).*(?:추천|어울리|사용할\s*만한)",
    r"다음\s*경기.*(?:실천|개선).*방법",
    r"(?:역할|특징).*(?:알려|설명)|(?:역할과|주요\s*특징)",
)
GENERIC_ENTITY_QUESTION_PATTERN = (
    r"^(?!(?:내|나|최근|이번|플레이|승률|경기|전적|kda|시야|와드|개선))"
    r"[가-힣A-Za-z0-9' .\-]{2,30}(?:은|는|이|가)?\s*(?:뭐야|무엇(?:이야|인가))"
)


def _route_decision(
    question: str,
    riot_id: str | None,
    tag_line: str | None,
) -> tuple[Route, str]:
    text = question.strip().casefold()
    if not text:
        raise ValueError("question must not be empty")
    has_player_context = bool((riot_id or "").strip() and (tag_line or "").strip())
    explicitly_personal = any(
        re.search(pattern, text, re.IGNORECASE) for pattern in EXPLICIT_PERSONAL_PATTERNS
    )
    explicitly_official = any(
        re.search(pattern, text, re.IGNORECASE) for pattern in EXPLICIT_OFFICIAL_PATTERNS
    )
    generic_entity_question = bool(re.search(GENERIC_ENTITY_QUESTION_PATTERN, text, re.IGNORECASE))

    if has_player_context and explicitly_personal and explicitly_official:
        return "mixed", "explicit_personal_and_official"
    if not has_player_context:
        return "official_information", "no_player_context_official"
    if explicitly_personal and not explicitly_official:
        return "personal_match", "player_context_default_personal"
    if explicitly_official or generic_entity_question:
        return "official_information", "explicit_official_information"
    return "personal_match", "player_context_default_personal"


def classify_question(
    question: str,
    riot_id: str | None = None,
    tag_line: str | None = None,
) -> Route:
    return _route_decision(question, riot_id, tag_line)[0]


def classify_question_reason(
    question: str,
    riot_id: str | None = None,
    tag_line: str | None = None,
) -> str:
    return _route_decision(question, riot_id, tag_line)[1]


def requested_recent_count(question: str, default: int) -> int:
    match = re.search(r"최근\s*(\d{1,2})\s*경기", question)
    if not match:
        return default
    return max(1, min(20, int(match.group(1))))


def champion_candidate(question: str) -> str | None:
    patterns = (
        r"내\s+(?:이번\s+)?(?P<name>[가-힣A-Za-z'\.\-]{2,20})\s*(?:경기|플레이)",
        r"(?P<name>[가-힣A-Za-z'\.\-]{2,20})\s*(?:경기|플레이).*(?:어땠|분석|비교)",
    )
    for pattern in patterns:
        match = re.search(pattern, question, re.IGNORECASE)
        if match:
            candidate = match.group("name").strip()
            if candidate not in {"최근", "이번", "이전", "솔로랭크"}:
                return candidate
    return None


def needs_opponent_comparison(question: str) -> bool:
    return bool(
        re.search(
            r"상대\s*(?:라이너|포지션)|적팀.*포지션|같은\s*포지션|"
            r"보다\s*(?:내가|못|부족)",
            question,
        )
    )


def needs_improvement_comparison(question: str) -> bool:
    return bool(re.search(r"개선|고칠|부족|문제", question))


def needs_period_comparison(question: str) -> bool:
    return bool(
        re.search(
            r"최근\s*5\s*경기.*(?:이전|그\s*전).*5\s*경기|"
            r"최근\s*5\s*경기.*(?:이전|그\s*전)\s*경기",
            question,
        )
    )


def needs_timeline(question: str) -> bool:
    return needs_opponent_comparison(question) or bool(
        re.search(r"(?:10|15)\s*분|초반|라인전|cs\s*차이|골드\s*차이", question, re.IGNORECASE)
    )
