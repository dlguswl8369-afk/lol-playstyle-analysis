import pytest

from personal_qa.query_router import classify_query
from personal_qa.tool_contracts import TOOL_CONTRACTS


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("말파이트는 어떤 챔피언이야?", "knowledge_query"),
        ("아이템 6655가 뭐야?", "knowledge_query"),
        ("Queue ID 420이 뭐야?", "knowledge_query"),
        ("최근 패치에서 말파이트가 변경됐어?", "knowledge_query"),
        ("내 이번 말파이트 경기 어땠어?", "personal_match_query"),
        ("상대 탑보다 뭘 못했어?", "personal_match_query"),
        ("내가 이번 경기에서 잘한 점은 뭐야?", "personal_match_query"),
        ("최근 5경기와 이전 15경기가 어떻게 달라졌어?", "personal_match_query"),
        ("내 경기 결과를 바탕으로 말파이트 관련 공식 정보를 알려줘.", "hybrid_query"),
        ("최근 패치 챔피언 중 내가 플레이한 챔피언이 있어?", "hybrid_query"),
        ("내 최근 5경기 변화와 관련된 26.18 공식 변경을 찾아줘.", "hybrid_query"),
    ],
)
def test_classify_required_questions(question: str, expected: str) -> None:
    assert classify_query(question) == expected


def test_tool_contracts_define_five_tools() -> None:
    assert set(TOOL_CONTRACTS) == {
        "search_game_knowledge",
        "get_player_matches",
        "get_match_summary",
        "compare_with_position_opponent",
        "compare_recent_periods",
    }
    assert all("input_schema" in contract for contract in TOOL_CONTRACTS.values())
