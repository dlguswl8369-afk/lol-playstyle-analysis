import pytest

from rag.official_query import build_official_search_plan, finalize_search_status


@pytest.mark.parametrize(
    ("question", "document_type", "query", "search_filter"),
    [
        ("Queue ID 420이 뭐야?", "queue", "420", "document_type eq 'queue' and source_id eq '420'"),
        ("Map ID 11은 어떤 맵이야?", "map", "11", "document_type eq 'map' and source_id eq '11'"),
        ("아이템 6655가 뭐야?", "item", "6655", "document_type eq 'item' and source_id eq '6655'"),
    ],
)
def test_explicit_ids_create_exact_filters(
    question: str, document_type: str, query: str, search_filter: str
) -> None:
    plan = build_official_search_plan(question)
    assert plan.status == "resolved"
    assert plan.document_type == document_type
    assert plan.normalized_query == query
    assert plan.search_filter == search_filter


def test_rift_game_mode_uses_verified_english_retrieval_alias() -> None:
    plan = build_official_search_plan("소환사의 협곡 게임 모드")
    assert plan.status == "resolved"
    assert plan.document_type == "game_mode"
    assert plan.normalized_query == "Summoner's Rift"
    assert plan.search_filter == "document_type eq 'game_mode'"


def test_rift_game_type_requires_clarification() -> None:
    plan = build_official_search_plan("협곡 게임 타입")
    assert plan.status == "needs_clarification"
    assert plan.document_type == "game_type"
    assert plan.normalized_query == "*"
    assert plan.search_mode == "filter_only"
    assert plan.is_ambiguous is True
    assert plan.clarification_question


def test_champion_query_is_classified() -> None:
    plan = build_official_search_plan("말파이트는 어떤 챔피언이야?")
    assert plan.document_type == "champion"
    assert plan.status == "resolved"


@pytest.mark.parametrize(
    ("question", "mode", "source_id"),
    [
        ("점멸 소환사 주문 효과", "CLASSIC", "SummonerFlash"),
        ("CHERRY 모드 점멸", "CHERRY", "SummonerCherryFlash"),
        ("아레나 점멸", "CHERRY", "SummonerCherryFlash"),
        ("JADE 모드 점멸", "JADE", "SummonerFlash_Jade"),
    ],
)
def test_flash_queries_prefer_the_verified_mode_variant(
    question: str, mode: str, source_id: str
) -> None:
    plan = build_official_search_plan(question)
    assert plan.status == "resolved"
    assert plan.document_type == "summoner_spell"
    assert plan.normalized_query == "점멸"
    assert plan.preferred_mode == mode
    assert plan.preferred_source_id == source_id
    assert plan.search_filter == (
        f"document_type eq 'summoner_spell' and source_id eq '{source_id}'"
    )


def test_unspecified_summoner_spell_scope_defaults_to_classic() -> None:
    plan = build_official_search_plan("소환사 주문 효과")
    assert plan.document_type == "summoner_spell"
    assert plan.preferred_mode == "CLASSIC"
    assert plan.preferred_source_id is None
    assert plan.search_filter == "document_type eq 'summoner_spell'"


def test_missing_classic_flash_is_reported_as_no_result() -> None:
    plan = build_official_search_plan("점멸 소환사 주문 효과")
    assert finalize_search_status(plan, 0).status == "no_result"


def test_multi_spell_comparison_is_not_narrowed_to_one_flash_variant() -> None:
    plan = build_official_search_plan("텔레포트와 점멸 효과를 비교해줘")
    assert plan.document_type == "summoner_spell"
    assert plan.preferred_mode == "CLASSIC"
    assert plan.preferred_source_id is None
    assert plan.search_filter == "document_type eq 'summoner_spell'"


@pytest.mark.parametrize(
    ("question", "document_type"),
    [
        ("말파이트는 어떤 챔피언이야?", "champion"),
        ("착취의 손아귀 룬 효과", "rune"),
        ("아이템 6655가 뭐야?", "item"),
        ("26.18 상향 챔피언", "patch_note"),
    ],
)
def test_summoner_mode_priority_does_not_affect_other_official_queries(
    question: str, document_type: str
) -> None:
    plan = build_official_search_plan(question)
    assert plan.document_type == document_type
    assert plan.preferred_mode is None
    assert plan.preferred_source_id is None


@pytest.mark.parametrize(
    ("question", "document_type", "status"),
    [
        ("말파이트는 어떤 챔피언이야?", "champion", "resolved"),
        ("아이템 6655가 뭐야?", "item", "resolved"),
        ("Queue ID 420이 뭐야?", "queue", "resolved"),
        ("26.18 상향 챔피언", "patch_note", "resolved"),
        ("점멸 소환사 주문 효과", "summoner_spell", "resolved"),
        ("착취의 손아귀 룬 효과", "rune", "resolved"),
        ("Map ID 11은 어떤 맵이야?", "map", "resolved"),
        ("26.17 아이템 변경 사항", "patch_note", "resolved"),
        ("소환사의 협곡 게임 모드", "game_mode", "resolved"),
        ("협곡 게임 타입", "game_type", "needs_clarification"),
    ],
)
def test_existing_ten_question_keyword_evaluation_regression(
    question: str, document_type: str, status: str
) -> None:
    plan = build_official_search_plan(question)
    assert plan.document_type == document_type
    assert plan.status == status


def test_patch_query_takes_priority_over_champion_word() -> None:
    plan = build_official_search_plan("26.18 상향 챔피언")
    assert plan.document_type == "patch_note"
    assert plan.search_filter == "document_type eq 'patch_note'"


def test_personal_match_question_is_not_official_search() -> None:
    plan = build_official_search_plan("내 이번 말파이트 경기 어땠어?")
    assert plan.status == "unsupported"
    assert plan.document_type is None
    assert plan.search_mode == "none"


def test_no_result_is_distinct_from_unsupported() -> None:
    plan = build_official_search_plan("말파이트는 어떤 챔피언이야?")
    assert finalize_search_status(plan, 0).status == "no_result"


def test_existing_tool_contract_shape_is_preserved() -> None:
    plan = build_official_search_plan("Queue ID 420이 뭐야?")
    assert plan.to_tool_input(top_k=3) == {
        "query": "420",
        "document_types": ["queue"],
        "top_k": 3,
    }
