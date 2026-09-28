from __future__ import annotations

import sys
from pathlib import Path

BACKEND_SRC = Path(__file__).resolve().parents[1] / "lol-coach-Backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

from lol_rag.orchestrator import _clean_personal_answer  # noqa: E402
from lol_rag.routing import classify_question, classify_question_reason  # noqa: E402


def test_official_mode_ignores_personal_wording() -> None:
    assert (
        classify_question(
            "최근 경기를 알려줘",
            riot_id="몬도치",
            tag_line="KR3",
            mode="official",
        )
        == "official_information"
    )
    assert (
        classify_question_reason("아리의 스킬을 알려줘", mode="official")
        == "official_mode_selected"
    )


def test_personal_mode_routes_personal_statistics() -> None:
    assert (
        classify_question(
            "최근에 어떤 챔피언을 가장 많이 했어?",
            riot_id="몬도치",
            tag_line="KR3",
            mode="personal",
        )
        == "personal_match"
    )


def test_personal_mode_keeps_mixed_question_internal() -> None:
    question = "최근 가장 많이 사용한 챔피언의 현재 추천 아이템을 알려줘"
    assert (
        classify_question(
            question,
            riot_id="몬도치",
            tag_line="KR3",
            mode="personal",
        )
        == "mixed"
    )


def test_legacy_routing_remains_compatible() -> None:
    assert classify_question("순간이동의 효과를 알려줘") == "official_information"
    assert (
        classify_question(
            "최근 승률을 알려줘",
            riot_id="몬도치",
            tag_line="KR3",
        )
        == "personal_match"
    )


def test_personal_answer_does_not_report_missing_official_context() -> None:
    answer = (
        "핵심 평가: Seraphine을 가장 많이 플레이했습니다.\n"
        "개선점: OFFICIAL_CONTEXT가 제공되지 않아 공식 정보 활용이 불가합니다."
    )
    cleaned = _clean_personal_answer(answer, {"statistics": {"match_count": 10}})

    assert cleaned == "핵심 평가: Seraphine을 가장 많이 플레이했습니다."
