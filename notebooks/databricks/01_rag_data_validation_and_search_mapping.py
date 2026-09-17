# Databricks notebook source
# GitHub backup copy: configure YOUR_* placeholders before use; execution outputs are not included.
# MAGIC %md
# MAGIC # 01 · RAG 데이터 검증 및 검색 매핑
# MAGIC
# MAGIC **LoL Insight Coach**의 현지 개인 RAG 개발용 Notebook입니다.
# MAGIC
# MAGIC - Riot 공식 게임 정보 741개의 무결성과 품질을 검증합니다.
# MAGIC - Azure AI Search 입력 필드 설계와 검색 평가 준비만 수행합니다.
# MAGIC - 개인 경기 데이터는 포함하거나 참조하지 않습니다.
# MAGIC - 입력 JSONL과 Manifest는 읽기 전용으로 사용합니다.
# MAGIC - 실제 검색 인덱스, 외부 API, 임베딩 모델 또는 LLM을 호출하지 않습니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 안전 수칙
# MAGIC
# MAGIC - 입력 JSONL과 Manifest를 수정·이동·삭제하지 않습니다.
# MAGIC - 실제 Riot ID, PUUID, Summoner ID, Account ID, Match ID를 다루지 않습니다.
# MAGIC - API Key, 토큰, Secret 또는 환경변수를 조회하거나 출력하지 않습니다.
# MAGIC - 다른 사용자의 Workspace 폴더를 참조하지 않습니다.
# MAGIC - Unity Catalog, Compute, Warehouse, Job 또는 Pipeline을 변경하지 않습니다.
# MAGIC - 네트워크와 외부 API를 호출하지 않습니다.
# MAGIC - 출력은 문서 수, 길이 통계, 오류 개수와 통과 여부로 제한합니다.
# MAGIC - 문서 본문이나 전체 JSON 객체를 출력하지 않습니다.

# COMMAND ----------

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlparse

# COMMAND ----------

JSONL_PATH = Path(
    "/Workspace/Users/YOUR_DATABRICKS_USER/"
    "rag_hyunji_dev/input/rag_search/rag_documents.jsonl"
)
MANIFEST_PATH = Path(
    "/Workspace/Users/YOUR_DATABRICKS_USER/"
    "rag_hyunji_dev/input/rag_search/rag_documents_manifest.json"
)

EXPECTED_TOTAL = 741
EXPECTED_COUNTS: dict[str, int] = {
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
EXPECTED_FIELDS: tuple[str, ...] = (
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
EXPECTED_DATA_DRAGON_VERSION = "16.18.1"
EXPECTED_PATCH_VERSIONS = frozenset({"26.18", "26.17", "26.16"})

# COMMAND ----------

EXPECTED_JSONL_SHA256 = (
    "bf743fad9aa9a4f4596be48e24449165bd4723a32aa94384e2aea349568528a5"
)
EXPECTED_MANIFEST_SHA256 = (
    "ac57948c0ef2cbdf61476adf219d5549d3376595cb8b28d1c10580433df05681"
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """읽기 전용 스트리밍으로 파일 SHA-256을 계산한다."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_input_file(path: Path, expected_sha256: str) -> dict[str, Any]:
    """파일 존재·크기·해시를 확인하고 불일치 시 원문 없이 중단한다."""
    if not path.is_file():
        raise FileNotFoundError(f"필수 입력 파일이 없습니다: {path.name}")
    size_bytes = path.stat().st_size
    actual_sha256 = sha256_file(path)
    sha256_matches = actual_sha256 == expected_sha256
    result = {
        "file_name": path.name,
        "exists": True,
        "size_bytes": size_bytes,
        "sha256_matches": sha256_matches,
    }
    print(result)
    if not sha256_matches:
        raise ValueError(f"입력 파일 SHA-256 불일치: {path.name}")
    return {**result, "sha256": actual_sha256}


file_checks = {
    "jsonl": verify_input_file(JSONL_PATH, EXPECTED_JSONL_SHA256),
    "manifest": verify_input_file(MANIFEST_PATH, EXPECTED_MANIFEST_SHA256),
}

# COMMAND ----------

def load_manifest(path: Path) -> dict[str, Any]:
    """Manifest를 UTF-8 strict 모드로 읽고 JSON 객체인지 확인한다."""
    try:
        with path.open("r", encoding="utf-8", errors="strict") as stream:
            value = json.load(stream)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Manifest 파싱 실패: {type(exc).__name__}") from None
    if not isinstance(value, dict):
        raise TypeError("Manifest 최상위 값은 JSON 객체여야 합니다.")
    return value


def validate_manifest(
    manifest: Mapping[str, Any], jsonl_check: Mapping[str, Any]
) -> dict[str, Any]:
    """Manifest의 계약과 실제 JSONL 파일 정보를 비교한다."""
    output = manifest.get("output")
    counts = manifest.get("document_counts_by_type")
    field_order = manifest.get("field_order")
    checks = {
        "document_count": manifest.get("document_count") == EXPECTED_TOTAL,
        "category_counts": counts == EXPECTED_COUNTS,
        "file_name": isinstance(output, dict)
        and output.get("file") == JSONL_PATH.name,
        "file_size": isinstance(output, dict)
        and output.get("size_bytes") == jsonl_check["size_bytes"],
        "file_sha256": isinstance(output, dict)
        and output.get("sha256") == jsonl_check["sha256"],
        "field_order": field_order == list(EXPECTED_FIELDS),
    }
    result = {
        "checks": checks,
        "all_core_checks_passed": all(checks.values()),
        # 현재 Manifest는 원본 버전을 별도 키로 선언하지 않으므로 JSONL에서 교차 검증한다.
        "source_versions_declared_in_manifest": bool(
            manifest.get("source_versions") or manifest.get("version_policy")
        ),
        "expected_data_dragon_version": EXPECTED_DATA_DRAGON_VERSION,
        "expected_patch_version_count": len(EXPECTED_PATCH_VERSIONS),
    }
    print(result)
    if not result["all_core_checks_passed"]:
        raise ValueError("Manifest 핵심 계약 검증에 실패했습니다.")
    return result


manifest = load_manifest(MANIFEST_PATH)
manifest_validation = validate_manifest(manifest, file_checks["jsonl"])

# COMMAND ----------

def load_jsonl_safely(path: Path) -> list[dict[str, Any]]:
    """JSONL을 UTF-8 strict, 빈 행 금지 조건으로 한 줄씩 읽는다."""
    documents: list[dict[str, Any]] = []
    try:
        stream = path.open("r", encoding="utf-8", errors="strict")
        with stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    raise ValueError(f"빈 JSONL 행 발견: line={line_number}")
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"JSONL 파싱 실패: line={line_number}, error={type(exc).__name__}"
                    ) from None
                if not isinstance(value, dict):
                    raise TypeError(f"JSON 객체가 아닌 행: line={line_number}")
                documents.append(value)
    except UnicodeDecodeError as exc:
        raise ValueError(
            f"UTF-8 디코딩 실패: error={type(exc).__name__}"
        ) from None
    print({"jsonl_loaded": True, "document_count": len(documents)})
    return documents


documents = load_jsonl_safely(JSONL_PATH)
if len(documents) != EXPECTED_TOTAL:
    raise ValueError(
        f"문서 수 불일치: expected={EXPECTED_TOTAL}, actual={len(documents)}"
    )

# COMMAND ----------

def validate_document_schema(
    docs: Sequence[Mapping[str, Any]], expected_fields: Sequence[str]
) -> dict[str, int]:
    """13개 공통 필드의 이름·순서·자료형을 모든 문서에서 검증한다."""
    errors: Counter[str] = Counter()
    expected = list(expected_fields)
    nullable_string_fields = (
        "source_url",
        "version",
        "patch_version",
        "category",
        "target_name",
    )

    for document in docs:
        actual_fields = list(document.keys())
        if len(actual_fields) != len(expected):
            errors["field_count"] += 1
        if set(actual_fields) != set(expected):
            errors["field_names"] += 1
        if actual_fields != expected:
            errors["field_order"] += 1
        if any(field not in document for field in expected):
            errors["missing_required_field"] += 1
        if not isinstance(document.get("document_id"), str):
            errors["document_id_type"] += 1
        if not isinstance(document.get("document_type"), str):
            errors["document_type_type"] += 1
        if not isinstance(document.get("title"), str):
            errors["title_type"] += 1
        if not isinstance(document.get("content"), str):
            errors["content_type"] += 1
        if not isinstance(document.get("locale"), str):
            errors["locale_type"] += 1
        for field in nullable_string_fields:
            if document.get(field) is not None and not isinstance(document.get(field), str):
                errors[f"{field}_type"] += 1
        if not isinstance(document.get("applicable_modes"), list):
            errors["applicable_modes_type"] += 1
        source_id = document.get("source_id")
        if source_id is not None and (
            isinstance(source_id, bool) or not isinstance(source_id, (str, int))
        ):
            errors["source_id_type"] += 1
        if not isinstance(document.get("metadata"), dict):
            errors["metadata_type"] += 1

    result = dict(sorted(errors.items()))
    print({"schema_field_count": len(expected), "schema_errors": result})
    if sum(result.values()) != 0:
        raise ValueError("공통 스키마 검증에 실패했습니다.")
    return result


schema_errors = validate_document_schema(documents, EXPECTED_FIELDS)

# COMMAND ----------

HTML_TAG_PATTERN = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
SHORT_CONTENT_THRESHOLD = 40
LONG_CONTENT_THRESHOLD = 20_000
DATA_DRAGON_TYPES = frozenset({"champion", "item", "rune", "summoner_spell"})


def validate_data_quality(docs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """문서 수·중복·빈 값·URL·버전·본문 길이를 집계한다."""
    ids = [str(doc.get("document_id", "")) for doc in docs]
    id_counts = Counter(ids)
    category_counts = Counter(str(doc.get("document_type", "")) for doc in docs)
    content_lengths = [len(str(doc.get("content", ""))) for doc in docs]

    invalid_urls = 0
    html_tags = 0
    version_missing = 0
    locale_missing = 0
    ddragon_version_errors = 0
    ddragon_patch_mix_errors = 0
    patch_version_errors = 0
    patch_ddragon_mix_errors = 0

    for doc in docs:
        source_url = doc.get("source_url")
        if not isinstance(source_url, str):
            invalid_urls += 1
        else:
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                invalid_urls += 1
        html_tags += len(HTML_TAG_PATTERN.findall(str(doc.get("content", ""))))
        if doc.get("version") is None and doc.get("patch_version") is None:
            version_missing += 1
        if not str(doc.get("locale", "")).strip():
            locale_missing += 1

        document_type = doc.get("document_type")
        if document_type in DATA_DRAGON_TYPES:
            ddragon_version_errors += doc.get("version") != EXPECTED_DATA_DRAGON_VERSION
            ddragon_patch_mix_errors += doc.get("patch_version") is not None
        elif document_type == "patch_note":
            patch_version_errors += doc.get("patch_version") not in EXPECTED_PATCH_VERSIONS
            patch_ddragon_mix_errors += doc.get("version") is not None

    quality = {
        "total_documents": len(docs),
        "category_counts": dict(category_counts),
        "category_counts_match": dict(category_counts) == EXPECTED_COUNTS,
        "duplicate_document_ids": sum(
            count - 1 for count in id_counts.values() if count > 1
        ),
        "empty_document_ids": sum(not value.strip() for value in ids),
        "empty_titles": sum(not str(doc.get("title", "")).strip() for doc in docs),
        "empty_contents": sum(not str(doc.get("content", "")).strip() for doc in docs),
        "html_tags": html_tags,
        "utf8_errors": 0,
        "invalid_source_urls": invalid_urls,
        "unexpected_document_types": sum(
            count for key, count in category_counts.items() if key not in EXPECTED_COUNTS
        ),
        "version_missing": version_missing,
        "locale_missing": locale_missing,
        "short_contents": sum(n < SHORT_CONTENT_THRESHOLD for n in content_lengths),
        "long_contents": sum(n > LONG_CONTENT_THRESHOLD for n in content_lengths),
        "data_dragon_version_errors": ddragon_version_errors,
        "data_dragon_patch_mix_errors": ddragon_patch_mix_errors,
        "patch_version_errors": patch_version_errors,
        "patch_data_dragon_mix_errors": patch_ddragon_mix_errors,
    }
    print(quality)
    return quality


quality_result = validate_data_quality(documents)

# COMMAND ----------

SECURITY_PATTERNS: dict[str, re.Pattern[str]] = {
    "riot_api_key": re.compile(r"RGAPI-[A-Za-z0-9_-]{10,}"),
    "openai_key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    "github_token": re.compile(
        r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"
    ),
    "databricks_pat": re.compile(r"\bdapi[a-fA-F0-9]{32,}\b"),
    "azure_key": re.compile(
        r"(?i)(?:azure[_ -]?(?:api[_ -]?)?key|accountkey|clientsecret)"
        r"\s*[\"':=]+\s*[A-Za-z0-9+/=_-]{16,}"
    ),
    "puuid_shape": re.compile(
        r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{70,100}(?![A-Za-z0-9_-])"
    ),
    "match_id": re.compile(
        r"\b(?:KR|NA1|EUW1|EUN1|JP1|BR1|LA1|LA2|OC1|TR1|RU|"
        r"PH2|SG2|TH2|TW2|VN2)_\d{8,}\b"
    ),
    "email": re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"),
    "windows_user_path": re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"']+"),
    "env_file": re.compile(r"(?i)(?:^|[\\/])\.env(?:$|[\\/\s])"),
    "anonymization_map": re.compile(r"anonymization_map\.local\.json", re.I),
}
SENSITIVE_EXACT_KEYS = frozenset(
    {"puuid", "summonerid", "accountid", "matchid", "riotid", "gamename", "tagline"}
)


def iter_strings(value: Any) -> Iterable[str]:
    """중첩 값에서 문자열만 순회하되 내용을 출력하지 않는다."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, Mapping):
        for nested in value.values():
            yield from iter_strings(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from iter_strings(nested)


def iter_normalized_keys(value: Any) -> Iterable[str]:
    """민감 구조 탐지를 위해 정확한 키 이름을 정규화한다."""
    if isinstance(value, Mapping):
        for key, nested in value.items():
            yield str(key).lower().replace("_", "")
            yield from iter_normalized_keys(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for nested in value:
            yield from iter_normalized_keys(nested)


def scan_security(docs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """탐지 문자열은 보관·출력하지 않고 유형별 개수만 반환한다."""
    findings: Counter[str] = Counter()
    for doc in docs:
        for text in iter_strings(doc):
            for finding_type, pattern in SECURITY_PATTERNS.items():
                findings[finding_type] += len(pattern.findall(text))
        for key in iter_normalized_keys(doc):
            if key in SENSITIVE_EXACT_KEYS:
                findings[f"sensitive_structure_{key}"] += 1
    complete = {key: findings.get(key, 0) for key in SECURITY_PATTERNS}
    complete.update(
        {key: count for key, count in sorted(findings.items()) if key not in complete}
    )
    result = {
        "findings_by_type": complete,
        "total_findings": sum(complete.values()),
    }
    print(result)
    return result


security_result = scan_security(documents)

# COMMAND ----------

CATEGORY_LABELS = {
    "champion": "Champion",
    "item": "Item",
    "rune": "Rune",
    "summoner_spell": "Summoner Spell",
    "patch_note": "Patch Note",
    "season": "Season",
    "queue": "Queue",
    "map": "Map",
    "game_mode": "Game Mode",
    "game_type": "Game Type",
}


def summarize_categories(docs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """본문 원문 없이 유형별 버전·언어·길이 통계를 만든다."""
    rows: list[dict[str, Any]] = []
    for document_type, label in CATEGORY_LABELS.items():
        selected = [doc for doc in docs if doc.get("document_type") == document_type]
        lengths = [len(str(doc.get("content", ""))) for doc in selected]
        versions = sorted(
            {
                str(value)
                for doc in selected
                for value in (doc.get("version"), doc.get("patch_version"))
                if value is not None
            }
        )
        languages = sorted({str(doc.get("locale")) for doc in selected})
        rows.append(
            {
                "category": label,
                "document_count": len(selected),
                "versions": versions,
                "languages": languages,
                "average_content_length": round(sum(lengths) / len(lengths), 1)
                if lengths
                else 0.0,
                "minimum_content_length": min(lengths, default=0),
                "maximum_content_length": max(lengths, default=0),
            }
        )
    return rows


category_summary = summarize_categories(documents)
print("category | count | versions | languages | avg_len | min_len | max_len")
for row in category_summary:
    print(
        f"{row['category']} | {row['document_count']} | {row['versions']} | "
        f"{row['languages']} | {row['average_content_length']} | "
        f"{row['minimum_content_length']} | {row['maximum_content_length']}"
    )

# COMMAND ----------

search_field_mapping: dict[str, dict[str, Any]] = {
    "document_id": {
        "roles": ["key", "filterable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "document_type": {
        "roles": ["filterable", "facetable", "sortable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "title": {
        "roles": ["searchable", "sortable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "content": {
        "roles": ["searchable", "retrievable", "vector_source"],
        "suggested_type": "Edm.String",
    },
    "source_url": {
        "roles": ["filterable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "locale": {
        "roles": ["filterable", "facetable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "version": {
        "roles": ["filterable", "facetable", "sortable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "patch_version": {
        "roles": ["filterable", "facetable", "sortable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "category": {
        "roles": ["filterable", "facetable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "target_name": {
        "roles": ["searchable", "filterable", "retrievable"],
        "suggested_type": "Edm.String",
    },
    "applicable_modes": {
        "roles": ["filterable", "facetable", "retrievable"],
        "suggested_type": "Collection(Edm.String)",
    },
    "source_id": {
        "roles": ["filterable", "retrievable", "metadata"],
        "suggested_type": "Edm.String",
    },
    "metadata": {
        "roles": ["retrievable", "metadata"],
        "suggested_type": "Edm.ComplexType or normalized JSON string",
    },
}

if tuple(search_field_mapping) != EXPECTED_FIELDS:
    raise ValueError("검색 필드 매핑이 실제 13개 스키마와 일치하지 않습니다.")
print({"search_mapping_field_count": len(search_field_mapping), "index_created": False})

# COMMAND ----------

PERSONAL_MATCH_TERMS = ("내 경기", "이번 경기", "최근 5경기", "상대 탑", "내가")
ROUTING_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("patch_note", ("패치", "상향", "하향", "변경")),
    ("queue", ("queue", "큐 ID", "솔로랭크")),
    ("map", ("맵", "소환사의 협곡", "맵 ID")),
    ("summoner_spell", ("소환사 주문", "점화", "강타", "텔레포트")),
    ("rune", ("룬", "감전", "정복자", "착취")),
    ("item", ("아이템", "효과", "존야", "밤의 수확자")),
    ("game_mode", ("게임 모드", "아레나", "무작위 총력전")),
    ("champion", ("챔피언", "말파이트", "아리", "징크스")),
)


def route_question(question: str) -> dict[str, Any]:
    """검색을 실행하지 않고 질문에 권장 필터만 부여한다."""
    normalized = question.strip().lower()
    personal_required = any(term.lower() in normalized for term in PERSONAL_MATCH_TERMS)
    matched_types = [
        document_type
        for document_type, keywords in ROUTING_RULES
        if any(keyword.lower() in normalized for keyword in keywords)
    ]
    patch_match = re.search(r"\b(\d{2}\.\d{1,2})\b", normalized)
    version_filter = {"patch_version": patch_match.group(1)} if patch_match else {}
    if personal_required:
        question_type = "개인 경기 데이터 필요"
        policy = "현재 공식 지식 RAG만으로 답변 금지"
    else:
        question_type = "공식 게임 정보"
        policy = "공식 지식 RAG 검색 가능"
    return {
        "question_type": question_type,
        "recommended_document_type_filter": sorted(set(matched_types)),
        "recommended_version_filter": version_filter,
        "hybrid_search_recommended": bool(matched_types),
        "personal_match_data_required": personal_required,
        "answer_policy": policy,
    }


routing_examples = (
    "말파이트는 어떤 챔피언이야?",
    "존야의 모래시계 효과가 뭐야?",
    "감전 룬은 어떻게 사용해?",
    "점화는 어떤 주문이야?",
    "26.18 패치에서 무엇이 바뀌었어?",
    "솔로랭크 Queue ID는 뭐야?",
    "소환사의 협곡 맵 ID는 뭐야?",
    "내 이번 말파이트 경기는 어땠어?",
    "상대 탑보다 내가 무엇을 못했어?",
)
for sample_question in routing_examples:
    print(route_question(sample_question))

# COMMAND ----------

evaluation_questions: list[dict[str, Any]] = [
    {"question": "말파이트는 어떤 챔피언이야?", "expected_document_types": ["champion"], "filters": {}, "personal_match_data_required": False},
    {"question": "아리의 역할과 특징은 무엇이야?", "expected_document_types": ["champion"], "filters": {}, "personal_match_data_required": False},
    {"question": "징크스는 어떤 공격 방식을 사용해?", "expected_document_types": ["champion"], "filters": {}, "personal_match_data_required": False},
    {"question": "존야의 모래시계 효과가 뭐야?", "expected_document_types": ["item"], "filters": {}, "personal_match_data_required": False},
    {"question": "밤의 수확자는 어떤 아이템이야?", "expected_document_types": ["item"], "filters": {}, "personal_match_data_required": False},
    {"question": "치명타 아이템에는 어떤 분류가 있어?", "expected_document_types": ["item"], "filters": {"category": "candidate"}, "personal_match_data_required": False},
    {"question": "감전 룬은 어떤 상황에 사용해?", "expected_document_types": ["rune"], "filters": {}, "personal_match_data_required": False},
    {"question": "정복자 룬의 특징은 무엇이야?", "expected_document_types": ["rune"], "filters": {}, "personal_match_data_required": False},
    {"question": "점화는 어떤 소환사 주문이야?", "expected_document_types": ["summoner_spell"], "filters": {}, "personal_match_data_required": False},
    {"question": "강타 주문은 어떤 모드에서 사용해?", "expected_document_types": ["summoner_spell"], "filters": {}, "personal_match_data_required": False},
    {"question": "26.18 패치에서 어떤 챔피언이 상향됐어?", "expected_document_types": ["patch_note"], "filters": {"patch_version": "26.18"}, "personal_match_data_required": False},
    {"question": "26.17 패치의 아이템 변경 사항은?", "expected_document_types": ["patch_note"], "filters": {"patch_version": "26.17"}, "personal_match_data_required": False},
    {"question": "26.16과 26.18 패치 변경을 어떻게 구분해?", "expected_document_types": ["patch_note"], "filters": {"patch_version": ["26.16", "26.18"]}, "personal_match_data_required": False},
    {"question": "솔로랭크 Queue ID는 뭐야?", "expected_document_types": ["queue"], "filters": {}, "personal_match_data_required": False},
    {"question": "소환사의 협곡 맵 ID는 뭐야?", "expected_document_types": ["map"], "filters": {}, "personal_match_data_required": False},
    {"question": "아레나는 어떤 게임 모드야?", "expected_document_types": ["game_mode"], "filters": {}, "personal_match_data_required": False},
    {"question": "게임 타입과 게임 모드는 어떻게 달라?", "expected_document_types": ["game_type", "game_mode"], "filters": {}, "personal_match_data_required": False},
    {"question": "현재 데이터의 시즌 정보에는 무엇이 있어?", "expected_document_types": ["season"], "filters": {}, "personal_match_data_required": False},
    {"question": "16.18.1 데이터에서 말파이트 정보를 찾아줘.", "expected_document_types": ["champion"], "filters": {"version": "16.18.1"}, "personal_match_data_required": False},
    {"question": "내 이번 말파이트 경기는 어땠어?", "expected_document_types": [], "filters": {}, "personal_match_data_required": True},
    {"question": "상대 탑보다 내가 무엇을 못했어?", "expected_document_types": [], "filters": {}, "personal_match_data_required": True},
    {"question": "최근 5경기에서 어떤 점이 달라졌어?", "expected_document_types": [], "filters": {}, "personal_match_data_required": True},
    {"question": "내 말파이트 경기와 공식 챔피언 역할을 함께 비교해줘.", "expected_document_types": ["champion"], "filters": {}, "personal_match_data_required": True},
    {"question": "26.18 패치 이후 내 점화 사용 경기를 분석해줘.", "expected_document_types": ["patch_note", "summoner_spell"], "filters": {"patch_version": "26.18"}, "personal_match_data_required": True},
]
print(
    {
        "evaluation_question_count": len(evaluation_questions),
        "personal_match_question_count": sum(
            item["personal_match_data_required"] for item in evaluation_questions
        ),
    }
)

# COMMAND ----------

def build_final_summary(
    docs: Sequence[Mapping[str, Any]],
    schema_result: Mapping[str, int],
    quality: Mapping[str, Any],
    security: Mapping[str, Any],
    manifest_result: Mapping[str, Any],
    inputs: Mapping[str, Mapping[str, Any]],
    field_mapping: Mapping[str, Any],
) -> dict[str, Any]:
    """선행 검증 결과만으로 최종 상태를 계산한다."""
    manifest_matches = bool(manifest_result["all_core_checks_passed"])
    source_versions_match = all(
        quality[key] == 0
        for key in (
            "data_dragon_version_errors",
            "data_dragon_patch_mix_errors",
            "patch_version_errors",
            "patch_data_dragon_mix_errors",
        )
    )
    search_mapping_ready = (
        tuple(field_mapping) == EXPECTED_FIELDS and len(field_mapping) == len(EXPECTED_FIELDS)
    )
    blocking_conditions = (
        len(docs) != EXPECTED_TOTAL,
        sum(schema_result.values()) != 0,
        quality["category_counts_match"] is not True,
        quality["duplicate_document_ids"] != 0,
        quality["empty_document_ids"] != 0,
        quality["empty_titles"] != 0,
        quality["empty_contents"] != 0,
        quality["html_tags"] != 0,
        quality["invalid_source_urls"] != 0,
        security["total_findings"] != 0,
        not manifest_matches,
        not source_versions_match,
        inputs["jsonl"]["sha256_matches"] is not True,
        inputs["manifest"]["sha256_matches"] is not True,
        not search_mapping_ready,
    )
    summary = {
        "total_documents": len(docs),
        "schema_field_count": len(EXPECTED_FIELDS),
        "document_id_duplicates": quality["duplicate_document_ids"],
        "empty_titles": quality["empty_titles"],
        "empty_contents": quality["empty_contents"],
        "security_findings": security["total_findings"],
        "manifest_matches": manifest_matches,
        "source_versions_match": source_versions_match,
        "jsonl_sha256_matches": inputs["jsonl"]["sha256_matches"],
        "manifest_sha256_matches": inputs["manifest"]["sha256_matches"],
        "search_mapping_ready": search_mapping_ready,
        "overall_status": "FAIL" if any(blocking_conditions) else "PASS",
    }
    return summary


final_summary = build_final_summary(
    documents,
    schema_errors,
    quality_result,
    security_result,
    manifest_validation,
    file_checks,
    search_field_mapping,
)
print(final_summary)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 다음 단계 — 이 Notebook에서는 수행하지 않음
# MAGIC
# MAGIC 1. 소유권과 비용 한도가 승인된 Compute에서 검증 실행
# MAGIC 2. Azure AI Search 인덱스 스키마 확정
# MAGIC 3. 한국어 텍스트 분석기와 검색 설정 검토
# MAGIC 4. 키워드 + 벡터 하이브리드 검색 구성
# MAGIC 5. 평가 질문으로 검색 품질 측정
# MAGIC 6. Microsoft Foundry Agent에 승인된 검색 도구 연결
# MAGIC 7. 개인 경기 질의는 Databricks 구조화 데이터 도구와 별도 연결
# MAGIC
# MAGIC 이 Notebook은 위 작업을 실행하지 않으며, 실행 전 Compute 담당자·비용 한도·Runtime·자동 종료 시간을 먼저 확정해야 합니다.
