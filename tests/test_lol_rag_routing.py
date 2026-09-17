import pytest

from lol_rag.routing import champion_candidate, classify_question, classify_question_reason

RIOT_ID = "test-player"
TAG_LINE = "KR1"


@pytest.mark.parametrize(
    "question",
    [
        "제일 많이 한 챔피언은?",
        "가장 많이 플레이한 챔피언은?",
        "모스트 챔피언은?",
        "플레이 승률은?",
        "승률 알려줘",
        "최근에 뭘 많이 했어?",
        "KDA 어때?",
        "내가 많이 죽어?",
        "플레이스타일 분석해줘",
        "개선할 점은?",
        "상대 라이너보다 뭘 못했어?",
    ],
)
def test_player_context_defaults_non_official_questions_to_personal(question: str) -> None:
    assert classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE) == "personal_match"
    assert (
        classify_question_reason(question, riot_id=RIOT_ID, tag_line=TAG_LINE)
        == "player_context_default_personal"
    )


@pytest.mark.parametrize(
    "question",
    [
        "아이템 6655가 뭐야?",
        "착취의 손아귀 룬 효과는?",
        "26.18 패치 내용을 알려줘",
        "말파이트는 어떤 챔피언이야?",
        "점멸 소환사 주문 효과는?",
        "Queue ID 420은 뭐야?",
        "Map ID 11은 뭐야?",
    ],
)
def test_explicit_official_questions_stay_official_with_player_context(
    question: str,
) -> None:
    assert classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE) == "official_information"
    assert (
        classify_question_reason(question, riot_id=RIOT_ID, tag_line=TAG_LINE)
        == "explicit_official_information"
    )


@pytest.mark.parametrize(
    "question",
    [
        "내가 많이 한 챔피언의 공식 특징도 알려줘",
        "내 말파이트 경기와 말파이트 공식 특징을 비교해줘",
        "내 점멸 사용과 점멸 공식 효과를 같이 설명해줘",
    ],
)
def test_mixed_requires_explicit_personal_and_official_requests(question: str) -> None:
    assert classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE) == "mixed"
    assert (
        classify_question_reason(question, riot_id=RIOT_ID, tag_line=TAG_LINE)
        == "explicit_personal_and_official"
    )


@pytest.mark.parametrize(
    "question",
    [
        "제일 많이 한 챔피언은?",
        "플레이 승률은?",
        "내 말파이트 경기와 말파이트 공식 특징을 비교해줘",
    ],
)
def test_missing_player_context_routes_to_official(question: str) -> None:
    assert classify_question(question) == "official_information"
    assert classify_question_reason(question) == "no_player_context_official"


def test_champion_word_alone_does_not_force_official_or_mixed() -> None:
    assert (
        classify_question("모스트 챔피언은?", riot_id=RIOT_ID, tag_line=TAG_LINE)
        == "personal_match"
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("내가 최근에 제일 많이 플레이한 챔피언은?", "personal_match"),
        ("최근 15경기 승률은 몇 퍼센트야?", "personal_match"),
        ("최근 경기에서 내 KDA는 어땠어?", "personal_match"),
        ("최근 15경기의 평균 킬, 데스, 어시스트를 알려줘.", "personal_match"),
        ("내가 가장 잘한 경기는 어떤 경기야?", "personal_match"),
        ("내가 가장 못한 경기는 어떤 경기야?", "personal_match"),
        ("최근 경기에서 가장 많이 죽은 판을 알려줘.", "personal_match"),
        ("최근 5경기와 그 이전 경기의 성적을 비교해줘.", "personal_match"),
        ("내 플레이스타일의 장점과 단점을 알려줘.", "personal_match"),
        ("내가 개선해야 할 점 3가지를 알려줘.", "personal_match"),
        ("최근 경기에서 시야 점수는 어땠어?", "personal_match"),
        ("적팀 같은 포지션 선수와 비교했을 때 내가 부족했던 점은 뭐야?", "personal_match"),
        ("말파이트는 어떤 챔피언이야?", "official_information"),
        ("세라핀의 역할과 주요 특징을 알려줘.", "official_information"),
        ("아이템 6655가 뭐야?", "official_information"),
        ("루덴의 메아리는 어떤 챔피언에게 어울려?", "official_information"),
        ("착취의 손아귀 룬 효과를 알려줘.", "official_information"),
        ("서포터가 사용할 만한 룬을 알려줘.", "official_information"),
        ("점멸 소환사 주문의 효과를 알려줘.", "official_information"),
        ("점멸과 순간이동의 차이를 비교해줘.", "official_information"),
        ("26.18 패치에서 상향된 챔피언은 누구야?", "official_information"),
        ("26.18 패치에서 세라핀은 변경됐어?", "official_information"),
        ("Queue ID 420은 어떤 게임이야?", "official_information"),
        ("Map ID 11은 어떤 맵이야?", "official_information"),
        ("소환사의 협곡에서 사용하는 게임 모드는 뭐야?", "official_information"),
        ("내가 가장 많이 한 챔피언의 특징과 플레이 방법을 알려줘.", "mixed"),
        ("최근 경기 기록을 바탕으로 나에게 잘 맞는 챔피언을 추천해줘.", "mixed"),
        ("내 최근 플레이에서 많이 죽는 이유를 분석하고 도움이 될 아이템을 추천해줘.", "mixed"),
        ("내가 최근에 사용한 챔피언과 잘 어울리는 룬을 공식정보를 근거로 알려줘.", "mixed"),
        ("내 플레이스타일을 분석하고 다음 경기에서 실천할 개선 방법을 알려줘.", "mixed"),
    ],
)
def test_batch_qa_route_matrix(question: str, expected: str) -> None:
    assert classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE) == expected


def test_previous_period_is_not_treated_as_a_champion_name() -> None:
    assert champion_candidate("최근 5경기와 그 이전 경기의 성적을 비교해줘.") is None
