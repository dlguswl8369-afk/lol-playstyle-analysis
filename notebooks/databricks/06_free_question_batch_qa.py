# Databricks notebook source
# GitHub backup copy: configure YOUR_* placeholders before use; execution outputs are not included.
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # LoL 자유 질문 30개 배치 QA
# MAGIC
# MAGIC 개인 경기 기본 데이터를 한 번만 수집해 12개 personal\_match와 5개 mixed 질문이 공유한다.
# MAGIC Secret, Token, Authorization Header, PUUID, Riot ID 원문은 출력하지 않는다.

# COMMAND ----------

from urllib.parse import urlparse
import importlib
import json
import re
import sys
import unicodedata
import urllib.request
from pathlib import Path
from time import perf_counter

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

import lol_rag.orchestrator as orchestrator_module
import lol_rag.routing as routing_module
import lol_rag.riot_client as riot_client_module

importlib.invalidate_caches()
riot_client_module = importlib.reload(riot_client_module)
routing_module = importlib.reload(routing_module)
orchestrator_module = importlib.reload(orchestrator_module)

AgentDependencies = orchestrator_module.AgentDependencies
collect_player_context = orchestrator_module.collect_player_context
answer_question = orchestrator_module.answer_question
RiotApiError = riot_client_module.RiotApiError
classify_question = routing_module.classify_question
classify_question_reason = routing_module.classify_question_reason
needs_timeline = routing_module.needs_timeline

from lol_rag.config import ENTRA_SECRET_KEYS, RIOT_SECRET_KEY, SECRET_SCOPE

# COMMAND ----------

RIOT_ID = "YOUR_RIOT_ID"
TAG_LINE = "YOUR_TAG_LINE"
MATCH_COUNT = 15

QUESTION_ROWS = [
    (1, "내가 최근에 제일 많이 플레이한 챔피언은?", "personal_match"),
    (2, "최근 15경기 승률은 몇 퍼센트야?", "personal_match"),
    (3, "최근 경기에서 내 KDA는 어땠어?", "personal_match"),
    (4, "최근 15경기의 평균 킬, 데스, 어시스트를 알려줘.", "personal_match"),
    (5, "내가 가장 잘한 경기는 어떤 경기야?", "personal_match"),
    (6, "내가 가장 못한 경기는 어떤 경기야?", "personal_match"),
    (7, "최근 경기에서 가장 많이 죽은 판을 알려줘.", "personal_match"),
    (8, "최근 5경기와 그 이전 경기의 성적을 비교해줘.", "personal_match"),
    (9, "내 플레이스타일의 장점과 단점을 알려줘.", "personal_match"),
    (10, "내가 개선해야 할 점 3가지를 알려줘.", "personal_match"),
    (11, "최근 경기에서 시야 점수는 어땠어?", "personal_match"),
    (12, "적팀 같은 포지션 선수와 비교했을 때 내가 부족했던 점은 뭐야?", "personal_match"),
    (13, "말파이트는 어떤 챔피언이야?", "official_information"),
    (14, "세라핀의 역할과 주요 특징을 알려줘.", "official_information"),
    (15, "아이템 6655가 뭐야?", "official_information"),
    (16, "루덴의 메아리는 어떤 챔피언에게 어울려?", "official_information"),
    (17, "착취의 손아귀 룬 효과를 알려줘.", "official_information"),
    (18, "서포터가 사용할 만한 룬을 알려줘.", "official_information"),
    (19, "점멸 소환사 주문의 효과를 알려줘.", "official_information"),
    (20, "점멸과 순간이동의 차이를 비교해줘.", "official_information"),
    (21, "26.18 패치에서 상향된 챔피언은 누구야?", "official_information"),
    (22, "26.18 패치에서 세라핀은 변경됐어?", "official_information"),
    (23, "Queue ID 420은 어떤 게임이야?", "official_information"),
    (24, "Map ID 11은 어떤 맵이야?", "official_information"),
    (25, "소환사의 협곡에서 사용하는 게임 모드는 뭐야?", "official_information"),
    (26, "내가 가장 많이 한 챔피언의 특징과 플레이 방법을 알려줘.", "mixed"),
    (27, "최근 경기 기록을 바탕으로 나에게 잘 맞는 챔피언을 추천해줘.", "mixed"),
    (28, "내 최근 플레이에서 많이 죽는 이유를 분석하고 도움이 될 아이템을 추천해줘.", "mixed"),
    (29, "내가 최근에 사용한 챔피언과 잘 어울리는 룬을 공식정보를 근거로 알려줘.", "mixed"),
    (30, "내 플레이스타일을 분석하고 다음 경기에서 실천할 개선 방법을 알려줘.", "mixed"),
]

# COMMAND ----------

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
if not (
    secret_format["nonempty"]
    and not secret_format["has_surrounding_whitespace"]
    and not secret_format["has_cr"]
    and not secret_format["has_lf"]
    and not secret_format["has_nul"]
    and not secret_format["has_other_control_character"]
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
tenant_id = None
client_id = None
client_secret = None

dependencies = AgentDependencies(
    entra_credential=credential,
    riot_api_key=riot_api_key,
    riot_trust_env=True,
    openai_max_output_tokens=1200,
)

prefetch_started = perf_counter()
player_context = None
prefetch_error = None
try:
    player_context = collect_player_context(
        riot_id=RIOT_ID,
        tag_line=TAG_LINE,
        match_count=MATCH_COUNT,
        dependencies=dependencies,
        include_latest_timeline=any(
            needs_timeline(question)
            for _, question, expected in QUESTION_ROWS
            if expected in {"personal_match", "mixed"}
        ),
    )
except RiotApiError as exc:
    prefetch_error = {
        "error_code": exc.code,
        "stage": exc.stage,
        "http_status": exc.http_status,
        "exception_class": exc.exception_class,
        "inner_exception_class": exc.inner_exception_class,
    }
except Exception as exc:
    prefetch_error = {
        "error_code": "PLAYER_CONTEXT_PREFETCH_FAILED",
        "exception_class": type(exc).__name__,
    }

prefetch_elapsed_ms = round((perf_counter() - prefetch_started) * 1000, 1)
prefetch_riot_calls = player_context.riot_calls if player_context is not None else 0

# COMMAND ----------

def failed_cached_result(question):
    actual_route = classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE)
    return {
        "status": "FAIL",
        "route": actual_route,
        "route_reason": classify_question_reason(
            question,
            riot_id=RIOT_ID,
            tag_line=TAG_LINE,
        ),
        "answer": "개인 경기 기본 데이터를 수집하지 못했습니다.",
        "statistics": {},
        "citations": [],
        "official_documents": [],
        "usage": {
            "riot_calls": 0,
            "search_calls": 0,
            "openai_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "elapsed_ms": 0,
        },
        **(prefetch_error or {}),
    }


def evaluate_result(expected_route, result):
    failures = []
    actual_route = str(result.get("route") or "")
    answer = str(result.get("answer") or "").strip()
    statistics = result.get("statistics") or {}
    calculated = statistics.get("statistics") or {}
    documents = result.get("official_documents") or []
    citations = result.get("citations") or []
    usage = result.get("usage") or {}
    document_ids = {str(document.get("document_id") or "") for document in documents}
    citation_ids = {str(citation.get("document_id") or "") for citation in citations}

    if actual_route != expected_route:
        failures.append("route_mismatch")
    if not answer:
        failures.append("empty_answer")
    if int(usage.get("openai_calls") or 0) > 1:
        failures.append("openai_call_limit_exceeded")
    if not citation_ids.issubset(document_ids):
        failures.append("citation_not_in_context")
    if expected_route == "official_information" and int(usage.get("riot_calls") or 0) != 0:
        failures.append("official_question_used_riot")
    if expected_route == "personal_match" and int(usage.get("search_calls") or 0) != 0:
        failures.append("personal_question_used_search")
    if expected_route in {"personal_match", "mixed"} and not calculated:
        failures.append("personal_statistics_missing")
    if expected_route == "mixed" and not documents:
        failures.append("mixed_official_context_missing")
    if expected_route == "mixed" and not citations:
        failures.append("mixed_citation_missing")
    if str(result.get("status") or "") in {"FAIL", "AUTH_BLOCKED"}:
        failures.append("runtime_failure")
    if expected_route == "official_information" and not documents:
        if str(result.get("status") or "") not in {
            "NO_RESULT",
            "NO_DATA",
            "NEEDS_CLARIFICATION",
        }:
            failures.append("official_grounding_missing")
    return failures


batch_rows = []
for number, question, expected_route in QUESTION_ROWS:
    started = perf_counter()
    try:
        if expected_route in {"personal_match", "mixed"} and player_context is None:
            result = failed_cached_result(question)
        else:
            result = answer_question(
                question=question,
                riot_id=RIOT_ID,
                tag_line=TAG_LINE,
                match_count=MATCH_COUNT,
                dependencies=dependencies,
                player_context=player_context,
            )
    except Exception as exc:
        result = {
            "status": "FAIL",
            "route": classify_question(question, riot_id=RIOT_ID, tag_line=TAG_LINE),
            "route_reason": classify_question_reason(
                question,
                riot_id=RIOT_ID,
                tag_line=TAG_LINE,
            ),
            "answer": "질문 처리 중 안전하게 보고할 수 있는 오류가 발생했습니다.",
            "statistics": {},
            "citations": [],
            "official_documents": [],
            "usage": {
                "riot_calls": 0,
                "search_calls": 0,
                "openai_calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "elapsed_ms": round((perf_counter() - started) * 1000, 1),
            },
            "error_code": "UNEXPECTED_QUESTION_ERROR",
            "exception_class": type(exc).__name__,
        }

    failures = evaluate_result(expected_route, result)
    statistics = result.get("statistics") or {}
    citations = result.get("citations") or []
    documents = result.get("official_documents") or []
    usage = result.get("usage") or {}
    batch_rows.append(
        {
            "number": number,
            "question": question,
            "expected_route": expected_route,
            "actual_route": str(result.get("route") or ""),
            "route_reason": str(result.get("route_reason") or ""),
            "status": str(result.get("status") or ""),
            "answer": str(result.get("answer") or ""),
            "statistics_present": bool(statistics.get("statistics")),
            "statistics_json": json.dumps(statistics, ensure_ascii=False, default=str),
            "citations_json": json.dumps(citations, ensure_ascii=False, default=str),
            "official_document_ids": [
                str(document.get("document_id") or "") for document in documents
            ],
            "riot_calls": int(usage.get("riot_calls") or 0),
            "search_calls": int(usage.get("search_calls") or 0),
            "openai_calls": int(usage.get("openai_calls") or 0),
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "elapsed_ms": float(usage.get("elapsed_ms") or 0),
            "error_code": result.get("error_code"),
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
    DoubleType,
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
        StructField("route_reason", StringType(), True),
        StructField("status", StringType(), False),
        StructField("answer", StringType(), False),
        StructField("statistics_present", BooleanType(), False),
        StructField("statistics_json", StringType(), False),
        StructField("citations_json", StringType(), False),
        StructField("official_document_ids", ArrayType(StringType(), False), False),
        StructField("riot_calls", IntegerType(), False),
        StructField("search_calls", IntegerType(), False),
        StructField("openai_calls", IntegerType(), False),
        StructField("input_tokens", IntegerType(), False),
        StructField("output_tokens", IntegerType(), False),
        StructField("elapsed_ms", DoubleType(), False),
        StructField("error_code", StringType(), True),
        StructField("failure_reason", StringType(), True),
        StructField("passed", BooleanType(), False),
    ]
)

result_df = spark.createDataFrame(batch_rows, schema=RESULT_SCHEMA).orderBy("number")
display(result_df)

pass_count = sum(row["passed"] for row in batch_rows)
fail_count = len(batch_rows) - pass_count
route_summary = {}
for route_name in ("personal_match", "official_information", "mixed"):
    selected = [row for row in batch_rows if row["expected_route"] == route_name]
    correct = sum(row["actual_route"] == route_name for row in selected)
    route_summary[route_name] = {
        "correct": correct,
        "total": len(selected),
        "accuracy": round(correct / len(selected), 4) if selected else None,
    }

summary = {
    "status": "PASS" if fail_count == 0 else "FAIL",
    "pass_count": pass_count,
    "fail_count": fail_count,
    "route_summary": route_summary,
    "prefetch_riot_calls": prefetch_riot_calls,
    "prefetch_elapsed_ms": prefetch_elapsed_ms,
    "question_riot_calls": sum(row["riot_calls"] for row in batch_rows),
    "search_calls": sum(row["search_calls"] for row in batch_rows),
    "openai_calls": sum(row["openai_calls"] for row in batch_rows),
    "input_tokens": sum(row["input_tokens"] for row in batch_rows),
    "output_tokens": sum(row["output_tokens"] for row in batch_rows),
    "secret_format": secret_format,
    "environment_checks": environment_checks,
    "prefetch_error": prefetch_error,
    "results": batch_rows,
}
dbutils.notebook.exit(json.dumps(summary, ensure_ascii=False, default=str))
