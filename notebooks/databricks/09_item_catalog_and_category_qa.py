# Databricks notebook source
# GitHub backup copy: configure <...> placeholders before use; execution outputs are not included.
# MAGIC %md
# MAGIC # LoL 아이템 카탈로그·카테고리 QA
# MAGIC
# MAGIC Data Dragon 공식 아이템 메타데이터로 구매 순위를 완성 장비·재료·소모품·장신구·신발로 분리한다.

# COMMAND ----------

import importlib
import json
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from azure.identity import ClientSecretCredential

EXPECTED_WORKSPACE_ID = "<WORKSPACE_ID>"
EXPECTED_USER = "<DATABRICKS_USER>"
EXPECTED_RUNTIME_HOSTNAME = "<DATABRICKS_RUNTIME_HOSTNAME>"
EXPECTED_GIT_BRANCH = "feature/rag-personal-qa-prep"
EXPECTED_GIT_HEAD = "<GIT_HEAD>"

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
workspace_id = str(ctx.workspaceId().get())
current_user = spark.sql("SELECT current_user() AS user").first()["user"]
runtime_hostname = (urlparse(ctx.apiUrl().get()).hostname or "").strip().lower()
environment_checks = {
    "workspace_id_matches": workspace_id == EXPECTED_WORKSPACE_ID,
    "user_matches": current_user == EXPECTED_USER,
    "runtime_hostname_matches": runtime_hostname == EXPECTED_RUNTIME_HOSTNAME,
}
if not all(environment_checks.values()):
    raise RuntimeError("environment_validation_failed")

try:
    MODE = dbutils.widgets.get("mode")
except Exception:
    MODE = "full"
if MODE not in {"source_preflight", "full"}:
    raise RuntimeError("unsupported_mode")

GIT_SRC = Path("/Workspace/Users/<DATABRICKS_USER>/lol-playstyle-analysis/src")
if str(GIT_SRC) not in sys.path:
    sys.path.insert(0, str(GIT_SRC))

import lol_rag.official_search as official_search_module
import lol_rag.orchestrator as orchestrator_module
import lol_rag.personal_analysis as personal_analysis_module
import lol_rag.routing as routing_module

importlib.invalidate_caches()
official_search_module = importlib.reload(official_search_module)
personal_analysis_module = importlib.reload(personal_analysis_module)
routing_module = importlib.reload(routing_module)
orchestrator_module = importlib.reload(orchestrator_module)

AgentDependencies = orchestrator_module.AgentDependencies
collect_player_context = orchestrator_module.collect_player_context
answer_question = orchestrator_module.answer_question

from lol_rag.config import ENTRA_SECRET_KEYS, RIOT_SECRET_KEY, SECRET_SCOPE

# COMMAND ----------

RIOT_ID = "<RIOT_ID>"
TAG_LINE = "<TAG_LINE>"
MATCH_COUNT = 15

secret_keys = {metadata.key for metadata in dbutils.secrets.list(SECRET_SCOPE)}
if RIOT_SECRET_KEY not in secret_keys:
    raise RuntimeError("riot_secret_metadata_missing")
stored_riot_api_key = dbutils.secrets.get(scope=SECRET_SCOPE, key=RIOT_SECRET_KEY)
riot_api_key = stored_riot_api_key.strip()
secret_format = {
    "nonempty": bool(stored_riot_api_key),
    "has_surrounding_whitespace": stored_riot_api_key != riot_api_key,
    "has_cr": "\r" in stored_riot_api_key,
    "has_lf": "\n" in stored_riot_api_key,
    "has_nul": "\x00" in stored_riot_api_key,
    "has_other_control_character": any(
        unicodedata.category(character) == "Cc"
        and character not in {"\r", "\n", "\x00"}
        for character in stored_riot_api_key
    ),
}
stored_riot_api_key = None
if not secret_format["nonempty"] or any(
    secret_format[key]
    for key in (
        "has_surrounding_whitespace",
        "has_cr",
        "has_lf",
        "has_nul",
        "has_other_control_character",
    )
):
    riot_api_key = None
    raise RuntimeError("riot_secret_format_invalid")

dependencies = AgentDependencies(entra_credential=None, riot_api_key=riot_api_key)
player_context = collect_player_context(
    riot_id=RIOT_ID,
    tag_line=TAG_LINE,
    match_count=MATCH_COUNT,
    dependencies=dependencies,
    include_latest_timeline=False,
)
prefetch_riot_calls = player_context.riot_calls
observed_2530 = []
for row in player_context.rows:
    if "2530" not in (row.get("final_item_ids") or []):
        continue
    match = (player_context.internals.get(row["match_id"]) or {}).get("match") or {}
    observed_2530.append(
        {
            "match_id": row["match_id"],
            "game_version": str((match.get("info") or {}).get("gameVersion") or ""),
        }
    )

if MODE == "source_preflight":
    total_calls = player_context.riot_calls
    dependencies.riot_api_key = None
    riot_api_key = None
    player_context = None
    dbutils.notebook.exit(
        json.dumps(
            {
                "status": "PASS" if observed_2530 else "OFFICIAL_ITEM_MATCH_NOT_FOUND",
                "mode": MODE,
                "observed_2530": observed_2530,
                "riot_calls": total_calls,
                "secret_format": secret_format,
                "environment_checks": environment_checks,
            },
            ensure_ascii=False,
        )
    )

if not all(key in secret_keys for key in ENTRA_SECRET_KEYS):
    raise RuntimeError("entra_secret_metadata_missing")
tenant_id = dbutils.secrets.get(scope=SECRET_SCOPE, key="tenant-id")
client_id = dbutils.secrets.get(scope=SECRET_SCOPE, key="client-id")
client_secret = dbutils.secrets.get(scope=SECRET_SCOPE, key="client-secret")
credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
    logging_enable=False,
    retry_total=0,
)
tenant_id = client_id = client_secret = None
dependencies.entra_credential = credential

# COMMAND ----------

question_specs = [
    (1, "아이템 2530이 뭐야?", "official_information"),
    (3, "최근 경기에서 내 최종 아이템 빌드를 알려줘.", "personal_match"),
    (4, "내가 최근 경기에서 가장 자주 산 아이템은 뭐야?", "personal_match"),
    (5, "내가 최근 경기에서 가장 자주 산 장비는 뭐야?", "personal_match"),
    (6, "내가 자주 산 소모품은 뭐야?", "personal_match"),
    (7, "내가 가장 자주 사용한 장신구는 뭐야?", "personal_match"),
    (8, "내가 최근 경기에서 가장 자주 산 조합 재료는 뭐야?", "personal_match"),
]


def _document_ids(result):
    return [
        str(document.get("document_id") or "")
        for document in result.get("official_documents") or []
    ]


def _contains_raw_identifier(value):
    serialized = json.dumps(value, ensure_ascii=False, default=str)
    raw_match_ids = [
        str(internal.get("raw_match_id") or "")
        for internal in player_context.internals.values()
        if internal.get("raw_match_id")
    ]
    return player_context.target_puuid in serialized or any(
        raw_id in serialized for raw_id in raw_match_ids
    )


def _validate(number, expected_route, result, official_name):
    failures = []
    usage = result.get("usage") or {}
    stats = result.get("statistics") or {}
    purchase = stats.get("item_purchase_statistics") or {}
    answer = str(result.get("answer") or "")
    if result.get("route") != expected_route:
        failures.append("route_mismatch")
    if result.get("status") != "PASS":
        failures.append("status_not_pass")
    if not answer.strip():
        failures.append("empty_answer")
    if _contains_raw_identifier({"answer": answer, "statistics": stats}):
        failures.append("raw_identifier_exposed")
    if number in {1, 2}:
        if "ddragon:item:2530" not in _document_ids(result):
            failures.append("item_2530_document_missing")
        if int(usage.get("riot_calls") or 0) != 0:
            failures.append("official_question_used_riot")
    if number == 3:
        if official_name not in answer or "아이템 2530" in answer:
            failures.append("final_build_name_not_resolved")
    if number == 4:
        for label in (
            "전체 구매 1위",
            "완성 장비 1위",
            "조합 재료 1위",
            "소모품 1위",
            "장신구 1위",
            "신발 1위",
        ):
            if label not in answer:
                failures.append("category_summary_incomplete")
                break
    expected_lists = {
        5: ("most_common_completed_equipment", "completed_equipment"),
        6: ("most_common_consumables", "consumable"),
        7: ("most_common_trinkets", "trinket"),
        8: ("most_common_components", "component"),
    }
    if number in expected_lists:
        key, category = expected_lists[number]
        ranking = purchase.get(key) or []
        if not ranking:
            failures.append("category_ranking_missing")
        if any(item.get("category") != category for item in ranking):
            failures.append("category_contamination")
    if number >= 3 and int(usage.get("openai_calls") or 0) != 0:
        failures.append("deterministic_question_used_openai")
    return failures


rows = []
first = answer_question(
    question=question_specs[0][1],
    riot_id=RIOT_ID,
    tag_line=TAG_LINE,
    match_count=MATCH_COUNT,
    dependencies=dependencies,
    player_context=player_context,
)
first_documents = first.get("official_documents") or []
official_name = str(first_documents[0].get("title") or "") if first_documents else ""
if not official_name:
    raise RuntimeError("item_2530_official_name_missing")
question_specs.insert(1, (2, f"{official_name}은 무슨 아이템이야?", "official_information"))

for number, question, expected_route in question_specs:
    result = first if number == 1 else answer_question(
        question=question,
        riot_id=RIOT_ID,
        tag_line=TAG_LINE,
        match_count=MATCH_COUNT,
        dependencies=dependencies,
        player_context=player_context,
    )
    failures = _validate(number, expected_route, result, official_name)
    usage = result.get("usage") or {}
    rows.append(
        {
            "number": number,
            "question": question,
            "expected_route": expected_route,
            "actual_route": str(result.get("route") or ""),
            "route_reason": str(result.get("route_reason") or ""),
            "status": str(result.get("status") or ""),
            "answer": str(result.get("answer") or ""),
            "statistics_json": json.dumps(
                result.get("statistics") or {}, ensure_ascii=False, default=str
            ),
            "search_query": str(result.get("search_query") or ""),
            "search_filter": str(result.get("search_filter") or ""),
            "official_document_ids": _document_ids(result),
            "citations_json": json.dumps(
                result.get("citations") or [], ensure_ascii=False, default=str
            ),
            "riot_calls": int(usage.get("riot_calls") or 0),
            "search_calls": int(usage.get("search_calls") or 0),
            "openai_calls": int(usage.get("openai_calls") or 0),
            "failure_reason": ",".join(failures) if failures else None,
            "passed": not failures,
        }
    )

total_riot_calls = player_context.riot_calls
dependencies.riot_api_key = None
riot_api_key = None
player_context = None
credential.close()
credential = None

# COMMAND ----------

from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

RESULT_SCHEMA = StructType(
    [
        StructField("number", IntegerType(), False),
        StructField("question", StringType(), False),
        StructField("expected_route", StringType(), False),
        StructField("actual_route", StringType(), False),
        StructField("route_reason", StringType(), False),
        StructField("status", StringType(), False),
        StructField("answer", StringType(), False),
        StructField("statistics_json", StringType(), False),
        StructField("search_query", StringType(), False),
        StructField("search_filter", StringType(), False),
        StructField("official_document_ids", ArrayType(StringType(), False), False),
        StructField("citations_json", StringType(), False),
        StructField("riot_calls", IntegerType(), False),
        StructField("search_calls", IntegerType(), False),
        StructField("openai_calls", IntegerType(), False),
        StructField("failure_reason", StringType(), True),
        StructField("passed", BooleanType(), False),
    ]
)
display(spark.createDataFrame(rows, RESULT_SCHEMA).orderBy("number"))
pass_count = sum(row["passed"] for row in rows)
summary = {
    "status": "PASS" if pass_count == len(rows) else "FAIL",
    "mode": MODE,
    "official_item_name": official_name,
    "pass_count": pass_count,
    "fail_count": len(rows) - pass_count,
    "prefetch_riot_calls": prefetch_riot_calls,
    "total_riot_calls": total_riot_calls,
    "search_calls": sum(row["search_calls"] for row in rows),
    "openai_calls": sum(row["openai_calls"] for row in rows),
    "observed_2530": observed_2530,
    "secret_format": secret_format,
    "environment_checks": environment_checks,
    "results": rows,
}
dbutils.notebook.exit(json.dumps(summary, ensure_ascii=False, default=str))
