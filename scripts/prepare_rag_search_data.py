from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rag.prepare_documents import prepare_rag_documents  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare the local 741-document RAG corpus.")
    parser.add_argument("--input-zip", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=PROJECT_ROOT / "data" / "processed" / "rag_search"
    )
    args = parser.parse_args()
    manifest = prepare_rag_documents(args.input_zip.resolve(), args.output_dir.resolve())
    print(
        json.dumps(
            {
                "status": "ok",
                "document_count": manifest["document_count"],
                "document_counts_by_type": manifest["document_counts_by_type"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
