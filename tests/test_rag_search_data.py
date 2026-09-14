import json
import zipfile
from pathlib import Path

from rag.prepare_documents import SOURCE_FILES, prepare_rag_documents

COUNTS = [173, 254, 62, 34, 63, 14, 99, 17, 22, 3]


def test_prepare_rag_search_data_has_741_unique_documents(tmp_path: Path) -> None:
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(
            "root/PACKAGE_MANIFEST.json",
            json.dumps({"created_at_utc": "2026-09-11T00:00:00Z"}),
        )
        counter = 0
        for (folder, filename, document_type), count in zip(SOURCE_FILES, COUNTS, strict=True):
            rows = []
            for _index in range(count):
                counter += 1
                rows.append(
                    json.dumps(
                        {
                            "document_id": f"doc-{counter}",
                            "document_type": document_type,
                            "title": f"title-{counter}",
                            "content": f"content-{counter}",
                            "source_url": None,
                            "locale": "ko_KR",
                            "version": "16.18.1" if folder == "ddragon" else None,
                            "patch_version": "26.18" if folder == "patch_notes" else None,
                            "source_id": str(counter),
                            "metadata": {},
                        },
                        ensure_ascii=False,
                    )
                )
            archive.writestr(
                f"root/01_rag_knowledge/{folder}/{filename}", "\n".join(rows) + "\n"
            )

    output = tmp_path / "out"
    manifest = prepare_rag_documents(package, output)
    first = json.loads((output / "rag_documents.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert manifest["document_count"] == 741
    assert manifest["duplicate_document_ids"] == 0
    assert first["content"] == "content-1"
    assert first["version"] == "16.18.1"
    assert json.loads((output / "validation_report.json").read_text(encoding="utf-8"))["valid"]


def test_prepare_rag_search_data_is_deterministic(tmp_path: Path) -> None:
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("root/PACKAGE_MANIFEST.json", "{}")
        counter = 0
        for (folder, filename, document_type), count in zip(SOURCE_FILES, COUNTS, strict=True):
            rows = []
            for _ in range(count):
                counter += 1
                rows.append(
                    json.dumps(
                        {
                            "document_id": f"id-{counter}",
                            "document_type": document_type,
                            "title": "title",
                            "content": "body",
                            "source_url": None,
                            "metadata": {},
                        }
                    )
                )
            archive.writestr(
                f"root/01_rag_knowledge/{folder}/{filename}", "\n".join(rows) + "\n"
            )
    output = tmp_path / "out"
    prepare_rag_documents(package, output)
    before = (output / "rag_documents.jsonl").read_bytes()
    prepare_rag_documents(package, output)
    assert (output / "rag_documents.jsonl").read_bytes() == before
