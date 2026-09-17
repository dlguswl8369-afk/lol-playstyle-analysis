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
