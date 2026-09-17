from __future__ import annotations

import inspect
import json

from lol_rag.answer_generation import AnswerGenerator
from lol_rag.official_search import OfficialSearchClient, SearchResult
from lol_rag.orchestrator import (
    AgentDependencies,
    PlayerContext,
    _deterministic_official_information,
    answer_question,
)


def _row(champion_name: str = "RenataGlasc") -> dict:
    return {
        "match_id": "MATCH_ALIAS",
        "champion_name": champion_name,
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


def _context() -> PlayerContext:
    return PlayerContext("internal-only", None, [_row()], {}, {}, 0)


class _Search:
    return_document = True
    resolved_source_ids: list[str] = []

    def __init__(self, credential, **kwargs):
        self.calls = 0

    def resolve_item_entity(self, question):
        return None

    def resolve_champion_by_source_id(self, source_id):
        self.calls += 1
        self.resolved_source_ids.append(source_id)
        documents = []
        if self.return_document:
            documents = [
                {
                    "document_id": f"ddragon:champion:{source_id}",
                    "document_type": "champion",
                    "source_id": source_id,
                    "title": "레나타 글라스크",
                    "content": "공식 역할과 특징",
                    "source_url": "https://example.invalid/champion",
                    "metadata": {
                        "tags": ["Support", "Mage"],
                        "blurb": "아군을 강화하고 적을 방해하는 공식 소개입니다.",
                    },
                }
            ]
        return SearchResult(
            "*",
            f"document_type eq 'champion' and source_id eq '{source_id}'",
            documents,
        )


class _Generator:
    last_statistics = None
    last_official_context = None

    def __init__(self, credential, **kwargs):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def generate(self, **kwargs):
        self.calls += 1
        self.__class__.last_statistics = kwargs["statistics"]
        self.__class__.last_official_context = kwargs["official_context"]
        return {
            "personal_analysis": "개인 통계만 설명했습니다",
            "official_information": "",
            "combined_advice": (
                "통계와 공식정보를 함께 봅니다\n"
                'citations: [{"document_id":"not-in-answer"}]{'
            ),
            "citations": [],
        }


def test_stat_derived_champion_uses_exact_search_and_grounded_citation(monkeypatch):
    import lol_rag.orchestrator as module

    _Search.return_document = True
    _Search.resolved_source_ids = []
    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)

    result = answer_question(
        "내가 가장 많이 한 챔피언의 특징도 알려줘.",
        riot_id="player",
        tag_line="TEST",
        match_count=5,
        dependencies=AgentDependencies(object(), "secret-present"),
        player_context=_context(),
    )

    assert result["route"] == "mixed"
    assert result["status"] == "PASS"
    assert _Search.resolved_source_ids == ["RenataGlasc"]
    assert result["search_query"] == "*"
    assert result["search_filter"] == (
        "document_type eq 'champion' and source_id eq 'RenataGlasc'"
    )
    assert result["citations"][0]["document_id"] == "ddragon:champion:RenataGlasc"
    assert "개인 경기 분석" in result["answer"]
    assert "공식 챔피언 특징" in result["answer"]
    assert "Support, Mage" in result["answer"]
    assert "공식 소개" in result["answer"]
    assert "citations:" not in result["answer"]
    assert "document_id" not in result["answer"]
    assert not result["answer"].rstrip().endswith(("{", "["))
    assert result["statistics"]["generation_metadata"]["generation_mode"] == (
        "model_with_deterministic_official_fallback"
    )
    assert result["usage"]["openai_calls"] == 1
    assert _Generator.last_statistics["most_played_champions"][0]["champion_name"] == (
        "RenataGlasc"
    )
    assert _Generator.last_official_context[0]["source_id"] == "RenataGlasc"


def test_mixed_question_without_official_document_is_not_pass(monkeypatch):
    import lol_rag.orchestrator as module

    _Search.return_document = False
    _Search.resolved_source_ids = []
    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)

    result = answer_question(
        "내가 가장 많이 한 챔피언의 특징도 알려줘.",
        riot_id="player",
        tag_line="TEST",
        dependencies=AgentDependencies(object(), "secret-present"),
        player_context=_context(),
    )

    assert result["status"] == "PARTIAL"
    assert result["citations"] == []
    assert result["usage"]["openai_calls"] == 0


def test_exact_champion_filter_escapes_source_id(monkeypatch):
    client = OfficialSearchClient(object())
    bodies = []
    monkeypatch.setattr(client, "_post", lambda body: bodies.append(body) or {"value": []})

    result = client.resolve_champion_by_source_id("Kai'Sa")

    assert result.query == "*"
    assert result.search_filter == (
        "document_type eq 'champion' and source_id eq 'Kai''Sa'"
    )
    assert bodies[0]["filter"] == result.search_filter


def test_implementation_does_not_hardcode_runtime_champion() -> None:
    import lol_rag.orchestrator as module

    assert "Seraphine" not in inspect.getsource(module)


def test_item_official_fallback_uses_only_document_fields() -> None:
    text = _deterministic_official_information(
        [
            {
                "document_type": "item",
                "title": "테스트 마법봉",
                "content": "검색 본문",
                "metadata": {
                    "tags": ["SpellDamage", "Mana"],
                    "stats": {"FlatMagicDamageMod": 80},
                    "description": "스킬 적중 시 추가 마법 피해를 줍니다.",
                    "gold": {"total": 2800},
                },
            }
        ]
    )

    assert "테스트 마법봉" in text
    assert "FlatMagicDamageMod 80" in text
    assert "추가 마법 피해" in text
    assert "2800" in text


class _Token:
    token = "test-token"


class _Credential:
    def get_token(self, scope):
        return _Token()


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_mixed_invalid_json_returns_fallback_signal_without_retry(monkeypatch):
    monkeypatch.setattr("lol_rag.answer_generation.OPENAI_ENDPOINT", "https://example.invalid")
    payload = {
        "status": "completed",
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": '{"personal_analysis":'}],
            }
        ],
    }
    monkeypatch.setattr(
        "lol_rag.answer_generation.urllib.request.urlopen",
        lambda request, timeout: _Response(payload),
    )
    generator = AnswerGenerator(_Credential())

    result = generator.generate("질문", "mixed", {"statistics": {}}, [{}])

    assert result["generation_error"] == "openai_response_invalid_json"
    assert generator.calls == 1
