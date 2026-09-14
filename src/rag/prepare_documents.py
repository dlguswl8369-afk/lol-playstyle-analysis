from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from .schemas import RAG_FIELDS, validate_rag_document

SOURCE_FILES = (
    ("ddragon", "champions.jsonl", "champion"),
    ("ddragon", "items.jsonl", "item"),
    ("ddragon", "runes.jsonl", "rune"),
    ("ddragon", "summoner_spells.jsonl", "summoner_spell"),
    ("patch_notes", "patch_notes.jsonl", "patch_note"),
    ("game_constants/rag", "seasons.jsonl", "season"),
    ("game_constants/rag", "queues.jsonl", "queue"),
    ("game_constants/rag", "maps.jsonl", "map"),
    ("game_constants/rag", "game_modes.jsonl", "game_mode"),
    ("game_constants/rag", "game_types.jsonl", "game_type"),
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    for member in members:
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
            raise ValueError(f"unsafe ZIP member: {member.filename}")
    return members


def _find_member(members: Iterable[zipfile.ZipInfo], suffix: str) -> zipfile.ZipInfo:
    matches = [item for item in members if item.filename.replace("\\", "/").endswith(suffix)]
    if len(matches) != 1:
        raise ValueError(f"expected one ZIP member ending with {suffix!r}, found {len(matches)}")
    return matches[0]


def _package_bytes(input_bytes: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(input_bytes)) as archive:
        members = _safe_members(archive)
        if archive.testzip() is not None:
            raise ValueError("input ZIP failed CRC validation")
        has_manifest = any(
            item.filename.replace("\\", "/").endswith("/PACKAGE_MANIFEST.json")
            for item in members
        )
        if has_manifest:
            return input_bytes
        nested = [
            item
            for item in members
            if PurePosixPath(item.filename.replace("\\", "/")).name
            == "LoL_Insight_Coach_Data_20260911.zip"
        ]
        if len(nested) != 1:
            raise ValueError(f"expected one nested knowledge ZIP, found {len(nested)}")
        return archive.read(nested[0])


def _normalise(source: dict[str, Any], document_type: str) -> dict[str, Any]:
    known = set(RAG_FIELDS)
    metadata = dict(source.get("metadata") or {})
    for key, value in source.items():
        if key not in known and key != "document_type":
            metadata.setdefault(key, value)
    return {
        "document_id": source.get("document_id"),
        "document_type": document_type,
        "title": source.get("title"),
        "content": source.get("content"),
        "source_url": source.get("source_url"),
        "locale": source.get("locale") or "ko_KR",
        "version": source.get("version"),
        "patch_version": source.get("patch_version"),
        "category": source.get("category"),
        "target_name": source.get("target_name"),
        "applicable_modes": list(source.get("applicable_modes") or []),
        "source_id": source.get("source_id"),
        "metadata": metadata,
    }


def prepare_rag_documents(package_zip: Path, output_dir: Path) -> dict[str, Any]:
    input_bytes = package_zip.read_bytes()
    package_bytes = _package_bytes(input_bytes)
    documents: list[dict[str, Any]] = []
    source_rows: dict[str, int] = {}
    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        members = _safe_members(archive)
        if archive.testzip() is not None:
            raise ValueError("package ZIP failed CRC validation")
        root_manifest = _find_member(members, "/PACKAGE_MANIFEST.json")
        package_manifest = json.loads(archive.read(root_manifest).decode("utf-8"))
        for folder, filename, document_type in SOURCE_FILES:
            suffix = f"/01_rag_knowledge/{folder}/{filename}"
            member = _find_member(members, suffix)
            rows = []
            for line_number, raw_line in enumerate(
                archive.read(member).decode("utf-8-sig").splitlines(), start=1
            ):
                if not raw_line.strip():
                    continue
                try:
                    rows.append(json.loads(raw_line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL: {filename}:{line_number}") from exc
            source_rows[f"{folder}/{filename}"] = len(rows)
            documents.extend(_normalise(row, document_type) for row in rows)

    errors = [
        {"index": index, "errors": row_errors}
        for index, document in enumerate(documents, start=1)
        if (row_errors := validate_rag_document(document))
    ]
    ids = [document["document_id"] for document in documents]
    duplicate_count = len(ids) - len(set(ids))
    counts = Counter(document["document_type"] for document in documents)
    expected = {
        "champion": 173,
        "item": 254,
        "rune": 62,
        "summoner_spell": 34,
        "patch_note": 63,
        "season": 14,
        "queue": 99,
        "map": 17,
        "game_mode": 22,
        "game_type": 3,
    }
    if len(documents) != 741 or dict(counts) != expected or duplicate_count or errors:
        raise ValueError(
            f"RAG validation failed: total={len(documents)}, duplicates={duplicate_count}, "
            f"counts={dict(counts)}, errors={len(errors)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "rag_documents.jsonl"
    payload = "".join(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
        for document in documents
    ).encode("utf-8")
    output_path.write_bytes(payload)
    manifest = {
        "schema_version": "1.0",
        "input_archive_sha256": _sha256(input_bytes),
        "source_package_sha256": _sha256(package_bytes),
        "source_package_created_at_utc": package_manifest.get("created_at_utc"),
        "document_count": len(documents),
        "document_counts_by_type": expected,
        "source_rows": source_rows,
        "output": {
            "file": output_path.name,
            "size_bytes": len(payload),
            "sha256": _sha256(payload),
        },
        "field_order": list(RAG_FIELDS),
        "duplicate_document_ids": duplicate_count,
        "empty_titles": 0,
        "empty_contents": 0,
    }
    validation = {
        "valid": True,
        "utf8_parse": True,
        "jsonl_parse": True,
        "document_count": len(documents),
        "document_counts_by_type": expected,
        "duplicate_document_ids": duplicate_count,
        "schema_errors": errors,
        "version_policy": {
            "data_dragon": "16.18.1",
            "patch_notes": "26.18/26.17/26.16 retained independently",
            "versions_merged": False,
        },
    }
    (output_dir / "rag_documents_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "validation_report.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest
