from lol_rag.orchestrator import AgentDependencies, PlayerContext, answer_question


class _Search:
    def __init__(self, credential, **kwargs):
        self.calls = 0

    def resolve_item_entity(self, question):
        return None


class _Generator:
    def __init__(self, credential, **kwargs):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def generate(self, **kwargs):
        self.calls += 1
        return {"answer": "캐시된 경기 통계 답변", "citations": []}


def _row():
    return {
        "match_id": "MATCH_001",
        "champion_name": "Seraphine",
        "team_position": "UTILITY",
        "win": True,
        "kills": 2,
        "deaths": 1,
        "assists": 10,
        "kda": 12.0,
        "kill_participation": 0.6,
        "cs": 30,
        "cs_per_min": 1.0,
        "gold": 9000,
        "gold_per_min": 300.0,
        "champion_damage": 10000,
        "damage_per_min": 333.3,
        "damage_taken": 5000,
        "damage_taken_per_min": 166.7,
        "vision_score": 50,
        "vision_per_min": 1.67,
        "wards_placed": 20,
        "wards_killed": 5,
        "objective_damage": 1000,
        "objective_damage_per_min": 33.3,
        "game_duration_seconds": 1800.0,
        "is_remake": False,
        "game_start_timestamp": 1,
    }


def test_answer_question_reuses_player_context_without_riot_calls(monkeypatch):
    import lol_rag.orchestrator as module

    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)
    monkeypatch.setattr(
        module,
        "RiotClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Riot called")),
    )
    context = PlayerContext(
        target_puuid="internal-only",
        rank_entry=None,
        rows=[_row()],
        internals={},
        timelines={},
        riot_calls=19,
    )
    result = answer_question(
        question="최근 15경기 승률은 몇 퍼센트야?",
        riot_id="player",
        tag_line="TEST",
        match_count=15,
        dependencies=AgentDependencies(entra_credential=object(), riot_api_key="set"),
        player_context=context,
    )

    assert result["status"] == "PASS"
    assert result["statistics"]["statistics"]["win_rate"] == 1.0
    assert result["usage"]["riot_calls"] == 0
    assert result["usage"]["search_calls"] == 0


def test_vision_summary_is_deterministic_and_skips_openai(monkeypatch):
    import lol_rag.orchestrator as module

    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)
    rows = []
    for index in range(10):
        row = _row()
        row["match_id"] = f"MATCH_{index + 1:03d}"
        row["game_start_timestamp"] = 100 - index
        rows.append(row)
    context = PlayerContext("internal-only", None, rows, {}, {}, 19)
    result = answer_question(
        "최근 경기에서 시야 점수는 어땠어?",
        riot_id="player",
        tag_line="TEST",
        match_count=15,
        dependencies=AgentDependencies(object(), "set"),
        player_context=context,
    )
    assert result["status"] == "PASS"
    assert result["usage"]["openai_calls"] == 0
    assert "평균 시야 점수: 50" in result["answer"]
    assert "최근 5경기" in result["answer"]


def test_period_comparison_filters_remakes_before_five_plus_five(monkeypatch):
    import lol_rag.orchestrator as module

    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)
    rows = []
    for index in range(12):
        row = _row()
        row["match_id"] = f"MATCH_{index + 1:03d}"
        row["game_start_timestamp"] = 100 - index
        row["is_remake"] = index in {1, 3}
        rows.append(row)
    context = PlayerContext("internal-only", None, rows, {}, {}, 19)
    result = answer_question(
        "최근 5경기와 그 이전 5경기의 성적을 비교해줘.",
        riot_id="player",
        tag_line="TEST",
        match_count=15,
        dependencies=AgentDependencies(object(), "set"),
        player_context=context,
    )
    assert result["statistics"]["recent_5_statistics"]["match_count"] == 5
    assert result["statistics"]["previous_statistics"]["match_count"] == 5
