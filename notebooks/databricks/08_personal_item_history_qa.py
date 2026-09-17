# Databricks notebook source
# GitHub backup copy: configure <...> placeholders before use; execution outputs are not included.
# MAGIC %md
# MAGIC # LoL 개인 경기 아이템 기록 QA
# MAGIC
# MAGIC Riot Match-V5 최종 인벤토리와 Timeline 구매 이력을 Azure AI Search의 공식 아이템 문서와 연결한다.
# MAGIC 실제 Riot ID, PUUID, Match ID, Secret, Token, Authorization Header는 출력하지 않는다.

# COMMAND ----------

from urllib.parse import urlparse
import importlib
import json
import sys
import unicodedata
from pathlib import Path

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

GIT_SRC = Path("/Workspace/Users/<DATABRICKS_USER>/lol-playstyle-analysis/src")
if str(GIT_SRC) not in sys.path:
    sys.path.insert(0, str(GIT_SRC))

import lol_rag.official_search as official_search_module
import lol_rag.personal_analysis as personal_analysis_module
import lol_rag.routing as routing_module
import lol_rag.orchestrator as orchestrator_module

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
QUESTIONS = [
    (1, "여신의 눈물은 무슨 아이템이야?", "official_information"),
    (2, "여신의 눈물 왜 써?", "official_information"),
    (3, "내가 최근 경기에서 여신의 눈물을 자주 샀어?", "personal_match"),
    (4, "내가 여신의 눈물을 몇 분에 샀어?", "personal_match"),
    (5, "내가 최근 경기에서 가장 자주 산 아이템은 뭐야?", "personal_match"),
    (6, "최근 경기에서 내 최종 아이템 빌드를 알려줘.", "personal_match"),
    (7, "내가 자주 구매한 아이템의 효과도 알려줘.", "mixed"),
    (8, "루덴의 메아리 뭐야?", "official_information"),
    (9, "무한의 대검 효과를 알려줘.", "official_information"),
    (10, "존재하지않는아이템123은 무슨 아이템이야?", "official_information"),
]

secret_keys = {metadata.key for metadata in dbutils.secrets.list(SECRET_SCOPE)}
if not all(key in secret_keys for key in ENTRA_SECRET_KEYS):
    raise RuntimeError("entra_secret_metadata_missing")
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

dependencies = AgentDependencies(
    entra_credential=credential,
    riot_api_key=riot_api_key,
    riot_trust_env=True,
    openai_max_output_tokens=700,
)

# Account, Summoner, League, Match ID, Match 상세는 실행당 한 번만 수집한다.
player_context = collect_player_context(
    riot_id=RIOT_ID,
    tag_line=TAG_LINE,
    match_count=MATCH_COUNT,
    dependencies=dependencies,
    include_latest_timeline=False,
)
prefetch_riot_calls = player_context.riot_calls

# COMMAND ----------

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
    return bool(
        player_context.target_puuid in serialized
        or any(raw_id in serialized for raw_id in raw_match_ids)
    )


def validate(number, expected_route, result):
    failures = []
    route = result.get("route")
    status = result.get("status")
    usage = result.get("usage") or {}
    answer = str(result.get("answer") or "")
    statistics = result.get("statistics") or {}
    document_ids = _document_ids(result)

    if route != expected_route:
        failures.append("route_mismatch")
    expected_statuses = {"NO_DATA", "NO_RESULT"} if number == 10 else {"PASS"}
    if status not in expected_statuses:
        failures.append("unexpected_status")
    if not answer.strip():
        failures.append("empty_answer")
    if _contains_raw_identifier(
        {"answer": answer, "statistics": statistics, "citations": result.get("citations")}
    ):
        failures.append("raw_identifier_exposed")

    if number in {1, 2, 8, 9, 10} and int(usage.get("riot_calls") or 0) != 0:
        failures.append("official_question_used_riot")
    if number in {3, 4, 5}:
        item_statistics = statistics.get("item_purchase_statistics") or {}
        if not item_statistics:
            failures.append("purchase_statistics_missing")
        if int(item_statistics.get("timeline_matches_loaded") or 0) <= 0:
            failures.append("timeline_not_loaded")
        if int(usage.get("openai_calls") or 0) != 0:
            failures.append("deterministic_item_answer_used_openai")
    if number == 6 and not statistics.get("final_item_statistics"):
        failures.append("final_item_statistics_missing")
    if number == 7:
        if not statistics.get("item_purchase_statistics"):
            failures.append("mixed_purchase_statistics_missing")
        if not document_ids:
            failures.append("mixed_official_context_missing")
    if number in {1, 2, 3, 4} and status == "PASS":
        if not any(document_id.startswith("ddragon:item:") for document_id in document_ids):
            failures.append("tear_document_missing")
    if number == 8 and "ddragon:item:6655" not in document_ids:
        failures.append("luden_document_missing")
    if number == 9 and "ddragon:item:3031" not in document_ids:
        failures.append("infinity_edge_document_missing")
    if number == 10 and document_ids:
        failures.append("nonexistent_item_was_resolved")
    if number in {3, 4}:
        item_statistics = statistics.get("item_purchase_statistics") or {}
        required = {
            "matches_analyzed",
            "matches_purchased",
            "purchase_match_rate",
            "total_purchase_events",
            "per_match_purchase_times",
            "matches_finished_with_item",
        }
        if not required.issubset(item_statistics):
            failures.append("purchase_metrics_incomplete")
    return failures


rows = []
for number, question, expected_route in QUESTIONS:
    result = answer_question(
        question=question,
        riot_id=RIOT_ID,
        tag_line=TAG_LINE,
        match_count=MATCH_COUNT,
        dependencies=dependencies,
        player_context=player_context,
    )
    failures = validate(number, expected_route, result)
    usage = result.get("usage") or {}
    statistics = result.get("statistics") or {}
    rows.append(
        {
            "number": number,
            "question": question,
            "expected_route": expected_route,
            "actual_route": str(result.get("route") or ""),
            "route_reason": str(result.get("route_reason") or ""),
            "status": str(result.get("status") or ""),
            "answer": str(result.get("answer") or ""),
            "statistics_json": json.dumps(statistics, ensure_ascii=False, default=str),
            "search_query": str(result.get("search_query") or ""),
            "search_filter": str(result.get("search_filter") or ""),
            "official_document_ids": _document_ids(result),
            "citations_json": json.dumps(
                result.get("citations") or [], ensure_ascii=False, default=str
            ),
            "riot_calls": int(usage.get("riot_calls") or 0),
            "search_calls": int(usage.get("search_calls") or 0),
            "openai_calls": int(usage.get("openai_calls") or 0),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
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
        StructField("input_tokens", IntegerType(), False),
        StructField("output_tokens", IntegerType(), False),
        StructField("failure_reason", StringType(), True),
        StructField("passed", BooleanType(), False),
    ]
)

display(spark.createDataFrame(rows, RESULT_SCHEMA).orderBy("number"))
pass_count = sum(row["passed"] for row in rows)
summary = {
    "status": "PASS" if pass_count == len(rows) else "FAIL",
    "pass_count": pass_count,
    "fail_count": len(rows) - pass_count,
    "prefetch_riot_calls": prefetch_riot_calls,
    "total_riot_calls": total_riot_calls,
    "question_riot_calls": sum(row["riot_calls"] for row in rows),
    "search_calls": sum(row["search_calls"] for row in rows),
    "openai_calls": sum(row["openai_calls"] for row in rows),
    "input_tokens": sum(row["input_tokens"] for row in rows),
    "output_tokens": sum(row["output_tokens"] for row in rows),
    "secret_format": secret_format,
    "environment_checks": environment_checks,
    "expected_git_branch": EXPECTED_GIT_BRANCH,
    "expected_git_head": EXPECTED_GIT_HEAD,
    "results": rows,
}
dbutils.notebook.exit(json.dumps(summary, ensure_ascii=False, default=str))
