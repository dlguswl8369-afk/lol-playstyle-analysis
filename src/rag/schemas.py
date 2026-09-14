from __future__ import annotations

from typing import Any

RAG_FIELDS = (
    "document_id",
    "document_type",
    "title",
    "content",
    "source_url",
    "locale",
    "version",
    "patch_version",
    "category",
    "target_name",
    "applicable_modes",
    "source_id",
    "metadata",
)

DOCUMENT_TYPES = {
    "champion",
    "item",
    "rune",
    "summoner_spell",
    "patch_note",
    "season",
    "queue",
    "map",
    "game_mode",
    "game_type",
}


def validate_rag_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if tuple(document) != RAG_FIELDS:
        errors.append("field_order_mismatch")
    if not isinstance(document.get("document_id"), str) or not document["document_id"]:
        errors.append("document_id_missing")
    if document.get("document_type") not in DOCUMENT_TYPES:
        errors.append("document_type_invalid")
    if not isinstance(document.get("title"), str) or not document["title"].strip():
        errors.append("title_missing")
    if not isinstance(document.get("content"), str) or not document["content"].strip():
        errors.append("content_missing")
    if document.get("locale") != "ko_KR":
        errors.append("locale_invalid")
    if not isinstance(document.get("applicable_modes"), list):
        errors.append("applicable_modes_not_array")
    if not isinstance(document.get("metadata"), dict):
        errors.append("metadata_not_object")
    return errors
