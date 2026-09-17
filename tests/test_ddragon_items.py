from rag.ddragon_items import build_item_document, should_export_item


def _item(**overrides):
    value = {
        "name": "테스트 장비",
        "description": "<mainText><stats>체력 <attention>200</attention></stats></mainText>",
        "plaintext": "",
        "gold": {"base": 1000, "purchasable": True, "total": 1000, "sell": 700},
        "tags": ["Health"],
        "maps": {"11": True},
        "stats": {"FlatHPPoolMod": 200},
    }
    value.update(overrides)
    return value


def test_normal_map11_store_item_is_exported():
    assert should_export_item("1000", _item()) is True


def test_observed_official_transform_is_not_lost_by_purchasable_filter():
    transformed = _item(
        gold={"base": 2250, "purchasable": False, "total": 2250, "sell": 1575},
        inStore=False,
        specialRecipe=2526,
    )
    assert should_export_item("2530", transformed, {"2530"}) is True
    assert should_export_item("2530", transformed, set()) is False


def test_hidden_unobserved_or_non_map11_item_is_not_broadly_exported():
    hidden = _item(
        gold={"purchasable": False},
        inStore=False,
        maps={"11": False, "30": True},
        specialRecipe=9999,
    )
    assert should_export_item("9999", hidden, {"9999"}) is False


def test_document_uses_official_fields_without_inventing_values():
    source = _item(
        name="악곡의 왕관",
        gold={"base": 2250, "purchasable": False, "total": 2250, "sell": 1575},
        inStore=False,
        specialRecipe=2526,
        image={"full": "2530.png"},
    )
    document = build_item_document("2530", source, "16.18.1")
    assert document["document_id"] == "ddragon:item:2530"
    assert document["title"] == "악곡의 왕관"
    assert document["metadata"]["purchasable"] is False
    assert document["metadata"]["special_recipe"] == 2526
    assert document["source_url"].endswith("/16.18.1/data/ko_KR/item.json")
