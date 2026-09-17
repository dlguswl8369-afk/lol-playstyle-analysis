from lol_rag.official_search import OfficialSearchClient


class _Credential:
    pass


def _item(source_id: str, title: str) -> dict:
    return {
        "document_id": f"ddragon:item:{source_id}",
        "document_type": "item",
        "source_id": source_id,
        "title": title,
    }


def test_item_name_resolver_discovers_title_then_uses_exact_source_id(monkeypatch):
    client = OfficialSearchClient(_Credential())
    bodies = []

    def fake_post(body):
        bodies.append(body)
        if len(bodies) == 1:
            return {"value": [_item("6655", "루덴의 메아리")]}
        return {"value": [_item("6655", "루덴의 메아리")]}

    monkeypatch.setattr(client, "_post", fake_post)
    result = client.resolve_item_entity("루덴의 메아리는 어떤 챔피언에게 어울려?")
    assert result is not None
    assert result.query == "*"
    assert result.search_filter == "document_type eq 'item' and source_id eq '6655'"
    assert result.documents[0]["document_id"] == "ddragon:item:6655"
    assert bodies[0]["filter"] == "document_type eq 'item'"


def test_item_resolver_is_generic_and_ignores_spacing(monkeypatch):
    client = OfficialSearchClient(_Credential())
    responses = iter(
        [
            {"value": [_item("3031", "무한의 대검")]},
            {"value": [_item("3031", "무한의 대검")]},
        ]
    )
    monkeypatch.setattr(client, "_post", lambda body: next(responses))
    result = client.resolve_item_entity("무한의   대검이 뭐야?")
    assert result is not None
    assert result.search_filter == "document_type eq 'item' and source_id eq '3031'"


def test_numeric_item_id_keeps_existing_search_path():
    assert OfficialSearchClient.item_name_candidate("아이템 6655가 뭐야?") is None


def test_unknown_item_name_does_not_bind_to_another_result(monkeypatch):
    client = OfficialSearchClient(_Credential())
    monkeypatch.setattr(
        client,
        "_post",
        lambda body: {"value": [_item("6655", "루덴의 메아리")]},
    )
    result = client.resolve_item_entity("존재하지 않는 검이 뭐야?")
    assert result is not None
    assert result.documents == []


def test_generic_item_question_forms_extract_names():
    assert (
        OfficialSearchClient.item_name_candidate("여신의 눈물은 무슨 아이템이야?") == "여신의 눈물"
    )
    assert OfficialSearchClient.item_name_candidate("여신의 눈물 왜 써?") == "여신의 눈물"
    assert OfficialSearchClient.item_name_candidate("무한의 대검 효과를 알려줘.") == "무한의 대검"
    assert (
        OfficialSearchClient.item_name_candidate("내가 여신의 눈물을 몇 분에 샀어?")
        == "여신의 눈물"
    )


def test_item_title_cache_reuses_resolved_document(monkeypatch):
    shared_cache = {}
    client = OfficialSearchClient(_Credential(), item_cache=shared_cache)
    bodies = []

    def fake_post(body):
        bodies.append(body)
        return {"value": [_item("3070", "여신의 눈물")]}

    monkeypatch.setattr(client, "_post", fake_post)
    first = client.resolve_item_entity("여신의 눈물은 무슨 아이템이야?")
    second = client.resolve_item_entity("여신의 눈물 왜 써?")

    assert first.documents[0]["source_id"] == "3070"
    assert second.documents[0]["source_id"] == "3070"
    assert len(bodies) == 2


def test_resolve_items_by_ids_uses_one_exact_batch_call(monkeypatch):
    client = OfficialSearchClient(_Credential())
    bodies = []

    def fake_post(body):
        bodies.append(body)
        return {"value": [_item("3070", "여신의 눈물"), _item("3031", "무한의 대검")]}

    monkeypatch.setattr(client, "_post", fake_post)
    result = client.resolve_items_by_ids(["3070", "3031", "3070"])

    assert [document["source_id"] for document in result.documents] == ["3070", "3031"]
    assert len(bodies) == 1
    assert "source_id eq '3070'" in result.search_filter
    assert "source_id eq '3031'" in result.search_filter
