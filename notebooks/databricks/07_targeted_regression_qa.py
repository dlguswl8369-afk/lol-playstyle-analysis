# Databricks notebook source
# GitHub backup copy: configure YOUR_* placeholders before use; execution outputs are not included.
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # LoL 자유 질문 표적 회귀 QA
# MAGIC
# MAGIC 시야 결정적 답변, 유효 경기 5+5 분할, 일반 아이템 이름 resolver만 검증한다.
# MAGIC Secret, Token, Authorization Header, PUUID, Riot ID 원문은 출력하지 않는다.

# COMMAND ----------

from urllib.parse import urlparse
import importlib
import json
import sys
import unicodedata
import urllib.request
from pathlib import Path

from azure.identity import ClientSecretCredential

EXPECTED_WORKSPACE_ID = "YOUR_WORKSPACE_ID"
EXPECTED_USER = "YOUR_DATABRICKS_USER"
EXPECTED_RUNTIME_HOSTNAME = "YOUR_RUNTIME_HOSTNAME"
EXPECTED_GIT_BRANCH = "feature/rag-personal-qa-prep"
EXPECTED_GIT_HEAD = "YOUR_EXPECTED_GIT_HEAD"
GIT_REPO_ID = "YOUR_GIT_REPO_ID"

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
workspace_id = str(ctx.workspaceId().get())
current_user = spark.sql("SELECT current_user() AS user").first()["user"]
runtime_hostname = (urlparse(ctx.apiUrl().get()).hostname or "").strip().lower()

workspace_token = ctx.apiToken().get()
repo_request = urllib.request.Request(
    f"{ctx.apiUrl().get().rstrip('/')}/api/2.0/repos/{GIT_REPO_ID}",
    headers={"Authorization": f"Bearer {workspace_token}"},
    method="GET",
)
with urllib.request.urlopen(repo_request, timeout=15) as response:
    repo_metadata = json.loads(response.read().decode("utf-8"))
workspace_token = None

environment_checks = {
    "workspace_id_matches": workspace_id == EXPECTED_WORKSPACE_ID,
    "user_matches": current_user == EXPECTED_USER,
    "runtime_hostname_matches": runtime_hostname == EXPECTED_RUNTIME_HOSTNAME,
    "git_branch_matches": repo_metadata.get("branch") == EXPECTED_GIT_BRANCH,
    "git_head_matches": repo_metadata.get("head_commit_id") == EXPECTED_GIT_HEAD,
}
if not all(environment_checks.values()):
    raise RuntimeError("environment_validation_failed")

GIT_SRC = Path("/Workspace/Users/YOUR_DATABRICKS_USER/lol-playstyle-analysis/src")
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
RiotApiError = orchestrator_module.RiotApiError

from lol_rag.config import ENTRA_SECRET_KEYS, RIOT_SECRET_KEY, SECRET_SCOPE

# COMMAND ----------

RIOT_ID = "YOUR_RIOT_ID"
TAG_LINE = "YOUR_TAG_LINE"
MATCH_COUNT = 15
QUESTIONS = [
    (1, "최근 경기에서 시야 점수는 어땠어?", "personal_match"),
    (2, "최근 5경기와 그 이전 5경기의 성적을 비교해줘.", "personal_match"),
    (3, "루덴의 메아리 뭐야?", "official_information"),
    (4, "루덴의 메아리는 어떤 챔피언에게 어울려?", "official_information"),
    (5, "무한의 대검이 뭐야?", "official_information"),
    (6, "아이템 6655가 뭐야?", "official_information"),
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

player_context = collect_player_context(
    riot_id=RIOT_ID,
    tag_line=TAG_LINE,
    match_count=MATCH_COUNT,
    dependencies=dependencies,
    include_latest_timeline=False,
)
prefetch_riot_calls = player_context.riot_calls

# COMMAND ----------

def validate(number, expected_route, result):
    failures = []
    usage = result.get("usage") or {}
    documents = result.get("official_documents") or []
    document_ids = [str(document.get("document_id") or "") for document in documents]
    statistics = result.get("statistics") or {}
    if result.get("route") != expected_route:
        failures.append("route_mismatch")
    if result.get("status") != "PASS":
        failures.append("status_not_pass")
    if not str(result.get("answer") or "").strip():
        failures.append("empty_answer")
    if number == 1:
        if int(usage.get("openai_calls") or 0) != 0:
            failures.append("vision_used_openai")
        for label in ("평균 시야 점수", "분당 시야 점수", "평균 와드 설치 수", "평균 와드 제거 수"):
            if label not in str(result.get("answer") or ""):
                failures.append("vision_metric_missing")
                break
    if number == 2:
        recent = statistics.get("recent_5_statistics") or {}
        previous = statistics.get("previous_statistics") or {}
        if recent.get("match_count") != 5 or previous.get("match_count") != 5:
            failures.append("period_split_not_five_plus_five")
    if number in {3, 4, 6} and "ddragon:item:6655" not in document_ids:
        failures.append("luden_document_missing")
    if number == 5 and not any(
        document_id.startswith("ddragon:item:") for document_id in document_ids
    ):
        failures.append("infinity_edge_document_missing")
    if number in {3, 4, 5}:
        if result.get("search_query") != "*":
            failures.append("item_exact_query_missing")
        if "document_type eq 'item'" not in str(result.get("search_filter") or ""):
            failures.append("item_filter_missing")
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
            "status": str(result.get("status") or ""),
            "answer": str(result.get("answer") or ""),
            "statistics_json": json.dumps(statistics, ensure_ascii=False, default=str),
            "search_query": str(result.get("search_query") or ""),
            "search_filter": str(result.get("search_filter") or ""),
            "official_document_ids": [
                str(document.get("document_id") or "")
                for document in result.get("official_documents") or []
            ],
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
    "question_riot_calls": sum(row["riot_calls"] for row in rows),
    "search_calls": sum(row["search_calls"] for row in rows),
    "openai_calls": sum(row["openai_calls"] for row in rows),
    "input_tokens": sum(row["input_tokens"] for row in rows),
    "output_tokens": sum(row["output_tokens"] for row in rows),
    "secret_format": secret_format,
    "environment_checks": environment_checks,
    "results": rows,
}
dbutils.notebook.exit(json.dumps(summary, ensure_ascii=False, default=str))
