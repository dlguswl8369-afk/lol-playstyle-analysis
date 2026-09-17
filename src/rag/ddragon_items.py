from __future__ import annotations

import html
import json
import re
from typing import Any


def _plain_text(value: str | None) -> str:
    text = re.sub(r"<br\s*/?>", "\n", value or "", flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return "\n".join(line.strip() for line in html.unescape(text).splitlines() if line.strip())


def should_export_item(
    item_id: str,
    item: dict[str, Any],
    observed_match_item_ids: set[str] | None = None,
) -> bool:
    """Keep map-11 store items plus official transforms observed in real matches."""

    maps = item.get("maps") or {}
    if maps.get("11") is not True:
        return False
    gold = item.get("gold") or {}
    if gold.get("purchasable") is True and item.get("inStore") is not False:
        return True
    observed = str(item_id) in (observed_match_item_ids or set())
    official_transform = bool(item.get("specialRecipe")) or item.get("inStore") is False
    return observed and official_transform


def build_item_document(item_id: str, item: dict[str, Any], version: str) -> dict[str, Any]:
    gold = item.get("gold") or {}
    tags = list(item.get("tags") or [])
    maps = dict(item.get("maps") or {})
    from_items = [str(value) for value in item.get("from") or []]
    into_items = [str(value) for value in item.get("into") or []]
    description = _plain_text(item.get("description"))
    plaintext = _plain_text(item.get("plaintext"))
    content_lines = [
        f"아이템 ID: {item_id}",
        f"이름: {item.get('name') or ''}",
        f"설명: {description}",
        f"간단 설명: {plaintext}",
        f"가격: {json.dumps(gold, ensure_ascii=False, separators=(',', ':'))}",
        f"구매 가능: {str(bool(gold.get('purchasable'))).lower()}",
        f"하위 아이템: {json.dumps(from_items, ensure_ascii=False, separators=(',', ':'))}",
        f"상위 아이템: {json.dumps(into_items, ensure_ascii=False, separators=(',', ':'))}",
        f"태그: {json.dumps(tags, ensure_ascii=False, separators=(',', ':'))}",
        f"능력치: {json.dumps(item.get('stats') or {}, ensure_ascii=False, separators=(',', ':'))}",
        f"사용 가능 맵: {json.dumps(maps, ensure_ascii=False, separators=(',', ':'))}",
    ]
    if item.get("specialRecipe") is not None:
        content_lines.append(f"특수 조합 원본: {item['specialRecipe']}")
    metadata = {
        "item_id": str(item_id),
        "gold_total": gold.get("total"),
        "gold_base": gold.get("base"),
        "purchasable": gold.get("purchasable"),
        "tags": tags,
        "maps": maps,
        "from_items": from_items,
        "into_items": into_items,
        "source_file": "item.json",
    }
    for source_key, metadata_key in (
        ("consumed", "consumed"),
        ("consumeOnFull", "consume_on_full"),
        ("inStore", "in_store"),
        ("specialRecipe", "special_recipe"),
    ):
        if source_key in item:
            metadata[metadata_key] = item.get(source_key)
    image = item.get("image") or {}
    if image.get("full"):
        metadata["image"] = image["full"]
    return {
        "document_id": f"ddragon:item:{item_id}",
        "document_type": "item",
        "title": str(item.get("name") or ""),
        "content": "\n".join(content_lines),
        "source_url": (
            f"https://ddragon.leagueoflegends.com/cdn/{version}/data/ko_KR/item.json"
        ),
        "locale": "ko_KR",
        "version": version,
        "patch_version": None,
        "category": None,
        "target_name": None,
        "applicable_modes": [],
        "source_id": str(item_id),
        "metadata": metadata,
    }


def export_item_documents(
    payload: dict[str, Any],
    version: str,
    observed_match_item_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    return [
        build_item_document(str(item_id), item, version)
        for item_id, item in (payload.get("data") or {}).items()
        if should_export_item(str(item_id), item, observed_match_item_ids)
    ]
