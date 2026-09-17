from __future__ import annotations

from lol_rag.official_search import SearchResult
from lol_rag.orchestrator import AgentDependencies, PlayerContext, _load_item_timelines
from lol_rag.personal_analysis import (
    build_final_item_statistics,
    build_item_catalog,
    build_item_purchase_statistics,
    build_match_records,
    classify_item_document,
    item_purchase_events,
)


def _participant(**overrides):
    value = {
        "puuid": "internal-target",
        "participantId": 3,
        "teamId": 100,
        "championName": "Seraphine",
        "teamPosition": "UTILITY",
        "win": True,
        "kills": 1,
        "deaths": 2,
        "assists": 8,
        "item0": 3070,
        "item1": 6655,
        "item2": 0,
        "item3": 0,
        "item4": 0,
        "item5": 0,
        "item6": 3364,
    }
    value.update(overrides)
    return value


def _match(alias_seed: int = 1, **participant_overrides):
    target = _participant(**participant_overrides)
    return {
        "metadata": {"matchId": f"RAW_MATCH_{alias_seed}"},
        "info": {
            "gameDuration": 1800,
            "gameStartTimestamp": 1000 - alias_seed,
            "participants": [target],
        },
    }


def _timeline(events):
    return {"info": {"frames": [{"timestamp": 0, "events": events}]}}


def test_match_items_extract_slots_and_ignore_zero_without_exposing_raw_ids():
    rows, internals = build_match_records([_match()], "internal-target")

    assert rows[0]["final_item_ids"] == ["3070", "6655", "3364"]
    assert rows[0]["final_item_slots"][-1]["is_trinket"] is True
    assert "RAW_MATCH_1" not in str(rows)
    assert "internal-target" not in str(rows)
    assert internals["MATCH_001"]["raw_match_id"] == "RAW_MATCH_1"


def test_final_item_frequency_counts_matches_not_duplicate_slots():
    rows, _ = build_match_records(
        [_match(1), _match(2, item0=3070, item1=3070, item6=0)],
        "internal-target",
    )
    stats = build_final_item_statistics(rows, {"3070": "여신의 눈물"}, "3070")

    assert stats["matches_analyzed"] == 2
    assert stats["matches_finished_with_item"] == 2
    assert stats["final_inventory_rate"] == 1.0


def test_purchase_history_keeps_sold_and_destroyed_but_removes_undo():
    parsed = item_purchase_events(
        _timeline(
            [
                {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 3070, "timestamp": 120000},
                {"type": "ITEM_SOLD", "participantId": 3, "itemId": 3070, "timestamp": 180000},
                {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 1027, "timestamp": 240000},
                {"type": "ITEM_DESTROYED", "participantId": 3, "itemId": 1027, "timestamp": 300000},
                {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 1001, "timestamp": 360000},
                {
                    "type": "ITEM_UNDO",
                    "participantId": 3,
                    "beforeId": 1001,
                    "afterId": 0,
                    "timestamp": 370000,
                },
            ]
        ),
        3,
    )

    assert [purchase["item_id"] for purchase in parsed["purchases"]] == ["3070", "1027"]
    assert parsed["undone_purchase_count"] == 1
    assert parsed["event_counts"]["ITEM_SOLD"] == 1
    assert parsed["event_counts"]["ITEM_DESTROYED"] == 1


def test_uncertain_undo_is_reported_without_guessing():
    parsed = item_purchase_events(
        _timeline(
            [
                {
                    "type": "ITEM_UNDO",
                    "participantId": 3,
                    "beforeId": 0,
                    "afterId": 3070,
                    "timestamp": 500000,
                }
            ]
        ),
        3,
    )

    assert parsed["purchases"] == []
    assert parsed["uncertain_undos"][0]["status"] == "uncertain"


def _catalog_document(item_id, title, tags, into_items=None, consumed=None):
    metadata = {"tags": tags, "into_items": into_items or [], "purchasable": True}
    if consumed is not None:
        metadata["consumed"] = consumed
    return {
        "source_id": item_id,
        "title": title,
        "metadata": __import__("json").dumps(metadata, ensure_ascii=False),
    }


def test_official_item_category_priority_and_boots_overlay():
    assert classify_item_document(_catalog_document("2055", "제어 와드", ["Consumable"])) == {
        "category": "consumable",
        "is_boots": False,
    }
    assert classify_item_document(
        _catalog_document("3364", "예언자의 렌즈", ["Active", "Trinket", "Vision"])
    ) == {"category": "trinket", "is_boots": False}
    assert classify_item_document(
        _catalog_document("3158", "명석함의 아이오니아 장화", ["Boots"], ["3171"])
    ) == {"category": "completed_equipment", "is_boots": True}
    assert classify_item_document(
        _catalog_document("1052", "증폭의 고서", ["SpellDamage"], ["6655"])
    )["category"] == "component"
    assert classify_item_document(
        _catalog_document("3116", "라일라이의 수정홀", ["SpellDamage"])
    )["category"] == "completed_equipment"
    assert classify_item_document(None)["category"] == "unknown"


def test_category_rankings_keep_match_count_and_event_count_separate():
    rows, _ = build_match_records([_match()], "internal-target")
    documents = [
        _catalog_document("2055", "제어 와드", ["Consumable"], consumed=True),
        _catalog_document("3364", "예언자의 렌즈", ["Trinket"]),
        _catalog_document("1052", "증폭의 고서", ["SpellDamage"], ["6655"]),
        _catalog_document("6655", "루덴의 메아리", ["SpellDamage"]),
        _catalog_document("3158", "명석함의 아이오니아 장화", ["Boots"], ["3171"]),
    ]
    catalog = build_item_catalog(documents)
    events = [
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 2055, "timestamp": 1000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 2055, "timestamp": 2000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 3364, "timestamp": 3000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 1052, "timestamp": 4000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 6655, "timestamp": 5000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 3158, "timestamp": 6000},
        {"type": "ITEM_PURCHASED", "participantId": 3, "itemId": 9999, "timestamp": 7000},
    ]
    names = {key: value["item_name"] for key, value in catalog.items()}
    stats = build_item_purchase_statistics(
        rows,
        {"MATCH_001": _timeline(events)},
        item_names=names,
        item_catalog=catalog,
    )

    control_ward = stats["most_common_consumables"][0]
    assert control_ward["matches_purchased"] == 1
    assert control_ward["total_purchase_events"] == 2
    assert stats["most_common_trinkets"][0]["item_id"] == "3364"
    assert stats["most_common_components"][0]["item_id"] == "1052"
    assert {item["item_id"] for item in stats["most_common_completed_equipment"]} == {
        "6655",
        "3158",
    }
    assert stats["most_common_boots"][0]["item_id"] == "3158"
    assert stats["unknown_items"][0]["item_id"] == "9999"


def test_purchase_statistics_use_aliases_and_actual_timestamps():
    rows, _ = build_match_records([_match()], "internal-target")
    stats = build_item_purchase_statistics(
        rows,
        {
            "MATCH_001": _timeline(
                [
                    {
                        "type": "ITEM_PURCHASED",
                        "participantId": 3,
                        "itemId": 3070,
                        "timestamp": 272000,
                    }
                ]
            )
        },
        item_names={"3070": "여신의 눈물"},
        target_item_id="3070",
    )

    assert stats["matches_purchased"] == 1
    assert stats["purchase_match_rate"] == 1.0
    assert stats["first_purchase_average_seconds"] == 272.0
    assert stats["first_purchase_average_display"] == "4분 32초"
    assert stats["per_match_purchase_times"][0]["match_id"] == "MATCH_001"
    assert "RAW_MATCH" not in str(stats)
    assert "internal-target" not in str(stats)


def test_timeline_loader_reuses_context_cache(monkeypatch):
    import lol_rag.orchestrator as module

    rows, internals = build_match_records([_match(1), _match(2)], "internal-target")
    calls = []

    class _Usage:
        calls = 0

    class _Riot:
        def __init__(self, *args, **kwargs):
            self.usage = _Usage()

        def timeline(self, match_id):
            calls.append(match_id)
            self.usage.calls += 1
            return _timeline([])

        def close(self):
            return None

    monkeypatch.setattr(module, "RiotClient", _Riot)
    context = PlayerContext(
        "internal-target",
        None,
        rows,
        internals,
        {"MATCH_001": _timeline([])},
        0,
    )
    dependencies = AgentDependencies(object(), "secret-present")

    assert _load_item_timelines(context, dependencies, 15) == 1
    assert _load_item_timelines(context, dependencies, 15) == 0
    assert calls == ["RAW_MATCH_2"]


class _Search:
    def __init__(self, credential, **kwargs):
        self.calls = 0

    @staticmethod
    def _document(item_id="3070", title="여신의 눈물"):
        return {
            "document_id": f"ddragon:item:{item_id}",
            "document_type": "item",
            "source_id": item_id,
            "title": title,
            "content": "공식 아이템 설명",
            "source_url": "https://example.invalid/item",
        }

    def resolve_item_entity(self, question):
        if "여신의 눈물" not in question:
            return None
        self.calls += 2
        return SearchResult(
            "*",
            "document_type eq 'item' and source_id eq '3070'",
            [self._document()],
        )

    def resolve_items_by_ids(self, source_ids):
        self.calls += 1
        documents = [
            self._document(
                item_id, {"3070": "여신의 눈물", "6655": "루덴의 메아리"}.get(item_id, item_id)
            )
            for item_id in source_ids
        ]
        return SearchResult("*", "document_type eq 'item'", documents)


class _Generator:
    def __init__(self, credential, **kwargs):
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0

    def generate(self, **kwargs):
        self.calls += 1
        return {"answer": "공식 문서 기반 답변", "citations": []}


def test_personal_purchase_frequency_is_deterministic_and_uses_cached_timeline(monkeypatch):
    import lol_rag.orchestrator as module

    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)
    rows, internals = build_match_records([_match()], "internal-target")
    context = PlayerContext(
        "internal-target",
        None,
        rows,
        internals,
        {
            "MATCH_001": _timeline(
                [
                    {
                        "type": "ITEM_PURCHASED",
                        "participantId": 3,
                        "itemId": 3070,
                        "timestamp": 272000,
                    }
                ]
            )
        },
        0,
    )

    result = module.answer_question(
        "내가 최근 경기에서 여신의 눈물을 자주 샀어?",
        riot_id="player",
        tag_line="TEST",
        match_count=15,
        dependencies=AgentDependencies(object(), "secret-present"),
        player_context=context,
    )

    assert result["route"] == "personal_match"
    assert result["status"] == "PASS"
    assert result["usage"]["riot_calls"] == 0
    assert result["usage"]["openai_calls"] == 0
    assert result["statistics"]["item_purchase_statistics"]["matches_purchased"] == 1
    assert "1경기에서 여신의 눈물" in result["answer"]


def test_official_item_question_never_calls_riot(monkeypatch):
    import lol_rag.orchestrator as module

    monkeypatch.setattr(module, "OfficialSearchClient", _Search)
    monkeypatch.setattr(module, "AnswerGenerator", _Generator)
    monkeypatch.setattr(
        module,
        "RiotClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("Riot called")),
    )

    result = module.answer_question(
        "여신의 눈물은 무슨 아이템이야?",
        riot_id="player",
        tag_line="TEST",
        dependencies=AgentDependencies(object(), "secret-present"),
    )

    assert result["route"] == "official_information"
    assert result["usage"]["riot_calls"] == 0
    assert result["status"] == "PASS"
