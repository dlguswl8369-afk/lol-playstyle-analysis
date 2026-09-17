# Databricks notebook source
# GitHub backup copy: configure YOUR_* placeholders before use; execution outputs are not included.
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # LoL 자유 질문 수동 RAG 테스트
# MAGIC
# MAGIC Riot ID, 태그라인, 최근 솔로랭크 경기 수와 자유 질문을 입력해 공식정보·개인 경기·혼합 질문을 한 번에 확인한다.
# MAGIC PUUID, Secret, Token, Authorization Header와 전체 경기 원문은 출력하지 않는다.

# COMMAND ----------

existing_widget_names = set(dbutils.widgets.getAll())

if "riot_id" not in existing_widget_names:
    dbutils.widgets.text("riot_id", "", "1. Riot ID")
if "tag_line" not in existing_widget_names:
    dbutils.widgets.text("tag_line", "KR1", "2. 태그라인")
if "match_count" not in existing_widget_names:
    dbutils.widgets.dropdown(
        "match_count",
        "20",
        ["1", "5", "10", "15", "20"],
        "3. 최근 솔로랭크 경기 수",
    )
if "question" not in existing_widget_names:
    dbutils.widgets.text(
        "question",
        "",
        "4. 질문",
    )

# COMMAND ----------

riot_id = dbutils.widgets.get("riot_id").strip()
tag_line = dbutils.widgets.get("tag_line").strip()
match_count = int(dbutils.widgets.get("match_count"))
question = dbutils.widgets.get("question").strip()

if not question:
    usage_guide = {
        "status": "NO_RESULT",
        "message": "질문을 입력한 뒤 Notebook을 다시 실행하세요.",
        "inputs": ["Riot ID", "태그라인", "최근 솔로랭크 경기 수", "자유 질문"],
        "note": "공식정보 질문은 Riot ID 없이 실행할 수 있습니다.",
        "external_api_calls": 0,
    }
    print("사용 방법: Widget에 질문을 입력해 주세요. 외부 API는 호출하지 않았습니다.")
    dbutils.notebook.exit(__import__("json").dumps(usage_guide, ensure_ascii=False))

# COMMAND ----------

from urllib.parse import urlparse
import html
import importlib
import json
import socket
import ssl
import sys
import unicodedata
import urllib.request
from pathlib import Path

import httpx
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
answer_question = orchestrator_module.answer_question
from lol_rag.config import ENTRA_SECRET_KEYS, RIOT_SECRET_KEY, SECRET_SCOPE
classify_question = routing_module.classify_question
classify_question_reason = routing_module.classify_question_reason

# COMMAND ----------

def exception_classes(exc):
    names = []
    current = exc
    while current is not None and len(names) < 8:
        names.append(current.__class__.__name__)
        current = current.__cause__ or current.__context__
    return names


def diagnose_network():
    host = "asia.api.riotgames.com"
    failures = []

    def remember(exc):
        if not failures:
            failures.extend(exception_classes(exc))

    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        dns_resolved = True
    except Exception as exc:
        dns_resolved = False
        remember(exc)

    try:
        with socket.create_connection((host, 443), timeout=8):
            tcp_connected = True
    except Exception as exc:
        tcp_connected = False
        remember(exc)

    try:
        context = ssl.create_default_context()
        with socket.create_connection((host, 443), timeout=8) as raw_socket:
            with context.wrap_socket(raw_socket, server_hostname=host):
                tls_connected = True
    except Exception as exc:
        tls_connected = False
        remember(exc)

    def https_reachable(url, trust_env):
        try:
            with httpx.Client(
                trust_env=trust_env,
                timeout=8.0,
                follow_redirects=False,
            ) as client:
                client.get(url)
            return True
        except Exception as exc:
            remember(exc)
            return False

    default_httpx_reachable = https_reachable(
        "https://asia.api.riotgames.com",
        True,
    )
    trust_env_false_httpx_reachable = https_reachable(
        "https://asia.api.riotgames.com",
        False,
    )
    use_trust_env = not (
        not default_httpx_reachable and trust_env_false_httpx_reachable
    )
    ddragon_https_reachable = https_reachable(
        "https://ddragon.leagueoflegends.com",
        use_trust_env,
    )
    riot_https_reachable = (
        default_httpx_reachable or trust_env_false_httpx_reachable
    )
    return {
        "dns_resolved": dns_resolved,
        "tcp_connected": tcp_connected,
        "tls_connected": tls_connected,
        "riot_https_reachable": riot_https_reachable,
        "ddragon_https_reachable": ddragon_https_reachable,
        "default_httpx_reachable": default_httpx_reachable,
        "trust_env_false_httpx_reachable": trust_env_false_httpx_reachable,
        "selected_trust_env": use_trust_env,
        "exception_class": failures[0] if failures else None,
        "inner_exception_class": failures[1] if len(failures) > 1 else None,
    }


def empty_usage():
    return {
        "riot_calls": 0,
        "search_calls": 0,
        "openai_calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "elapsed_ms": 0,
    }


def blocked_result(status, route, route_reason, answer, error_code):
    return {
        "status": status,
        "route": route,
        "route_reason": route_reason,
        "answer": answer,
        "statistics": {},
        "citations": [],
        "official_documents": [],
        "usage": empty_usage(),
        "error_code": error_code,
        "stage": "preflight",
        "http_status": None,
    }


route = classify_question(question, riot_id=riot_id, tag_line=tag_line)
route_reason = classify_question_reason(
    question,
    riot_id=riot_id,
    tag_line=tag_line,
)

secret_keys = {metadata.key for metadata in dbutils.secrets.list(SECRET_SCOPE)}
entra_keys_present = all(key in secret_keys for key in ENTRA_SECRET_KEYS)
riot_secret_present = RIOT_SECRET_KEY in secret_keys

stored_riot_api_key = (
    dbutils.secrets.get(scope=SECRET_SCOPE, key=RIOT_SECRET_KEY)
    if riot_secret_present
    else ""
)
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
has_internal_control_character = any(
    unicodedata.category(character) == "Cc"
    for character in riot_api_key
)
stored_riot_api_key = None
network_diagnostics = diagnose_network()
egress_blocked = not all(
    network_diagnostics[key]
    for key in (
        "dns_resolved",
        "tcp_connected",
        "tls_connected",
        "riot_https_reachable",
        "ddragon_https_reachable",
    )
)

credential = None
dependencies = None
if not riot_api_key:
    result = blocked_result(
        "FAIL",
        route,
        route_reason,
        "Riot API Secret이 비어 있어 요청을 실행하지 않았습니다.",
        "RIOT_SECRET_REQUIRED",
    )
elif has_internal_control_character:
    result = blocked_result(
        "FAIL",
        route,
        route_reason,
        "Riot API Secret 형식이 올바르지 않아 요청을 실행하지 않았습니다.",
        "RIOT_SECRET_FORMAT_ERROR",
    )
elif egress_blocked:
    result = blocked_result(
        "DATABRICKS_EGRESS_BLOCKED",
        route,
        route_reason,
        "Databricks에서 외부 Riot 서비스로 연결할 수 없습니다.",
        "DATABRICKS_EGRESS_BLOCKED",
    )
elif not entra_keys_present:
    result = blocked_result(
        "AUTH_BLOCKED",
        route,
        route_reason,
        "Entra ID 인증 Secret 메타데이터가 완전하지 않습니다.",
        "ENTRA_SECRET_REQUIRED",
    )
else:
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
        riot_trust_env=network_diagnostics["selected_trust_env"],
    )
    result = answer_question(
        question=question,
        riot_id=riot_id or None,
        tag_line=tag_line or None,
        match_count=match_count,
        dependencies=dependencies,
    )

riot_api_key = None
if dependencies is not None:
    dependencies.riot_api_key = None
if credential is not None:
    credential.close()
credential = None

# COMMAND ----------

def escaped(value):
    return html.escape(str(value if value is not None else ""))


statistics = result.get("statistics") or {}
usage = result.get("usage") or {}
documents = result.get("official_documents") or []
citations = result.get("citations") or []
display_rows = [
    ("질문", question),
    ("질문 경로", result.get("route")),
    ("경로 판정 근거", result.get("route_reason")),
    ("사용한 경기 수", statistics.get("match_count", 0)),
    ("계산된 핵심 통계", json.dumps(statistics, ensure_ascii=False)),
    ("생성 답변", result.get("answer")),
    ("공식 문서 제목", ", ".join(str(item.get("title") or "") for item in documents)),
    ("공식 Source URL", ", ".join(str(item.get("source_url") or "") for item in citations)),
    ("Search 호출 횟수", usage.get("search_calls", 0)),
    ("OpenAI 호출 횟수", usage.get("openai_calls", 0)),
    ("Riot 호출 횟수", usage.get("riot_calls", 0)),
    ("오류 코드", result.get("error_code")),
    ("오류 단계", result.get("stage")),
    ("HTTP 상태", result.get("http_status")),
    ("예외 클래스", result.get("exception_class")),
    ("내부 예외 클래스", result.get("inner_exception_class")),
    ("Secret 형식", json.dumps(secret_format, ensure_ascii=False)),
    ("네트워크 진단", json.dumps(network_diagnostics, ensure_ascii=False)),
    ("입력 Token", usage.get("input_tokens", 0)),
    ("출력 Token", usage.get("output_tokens", 0)),
    ("전체 처리 시간(ms)", usage.get("elapsed_ms", 0)),
    ("최종 상태", result.get("status")),
]
table_html = "".join(
    f"<tr><th style='text-align:left;vertical-align:top;padding:6px'>{escaped(label)}</th>"
    f"<td style='white-space:pre-wrap;padding:6px'>{escaped(value)}</td></tr>"
    for label, value in display_rows
)
displayHTML(
    "<h3>LoL 수동 RAG 결과</h3>"
    "<table style='border-collapse:collapse;width:100%' border='1'>"
    f"{table_html}</table>"
)

safe_output = {
    "status": result.get("status"),
    "route": result.get("route"),
    "route_reason": result.get("route_reason"),
    "answer": result.get("answer"),
    "statistics": statistics,
    "citations": citations,
    "usage": usage,
    "error_code": result.get("error_code"),
    "stage": result.get("stage"),
    "http_status": result.get("http_status"),
    "exception_class": result.get("exception_class"),
    "inner_exception_class": result.get("inner_exception_class"),
    "secret_format": secret_format,
    "network_diagnostics": network_diagnostics,
    "riot_secret_present": riot_secret_present,
    "environment_checks": environment_checks,
}
dbutils.notebook.exit(json.dumps(safe_output, ensure_ascii=False))