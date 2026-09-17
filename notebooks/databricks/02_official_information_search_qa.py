# Databricks notebook source
# GitHub backup copy: configure <...> placeholders before use; execution outputs are not included.
# MAGIC %md
# MAGIC # 공식 게임정보 검색 QA
# MAGIC 사용자 질문을 공식정보 유형으로 분류하고, Git 폴더의 검색 계획을 통해 Azure AI Search를 읽기 전용으로 검증한다. Azure OpenAI, Foundry, 임베딩, LLM은 사용하지 않는다.

# COMMAND ----------

import json
import os
import platform
import subprocess
from urllib.parse import urlparse

EXPECTED_WORKSPACE_HOSTNAME = "<DATABRICKS_RUNTIME_HOSTNAME>"
EXPECTED_WORKSPACE_ID = "<WORKSPACE_ID>"
EXPECTED_USER = "<DATABRICKS_USER>"
EXPECTED_GIT_HEAD = "<GIT_HEAD>"
REPO_ROOT = "/Workspace/Users/<DATABRICKS_USER>/lol-playstyle-analysis"
SEARCH_ENDPOINT = "<AZURE_SEARCH_ENDPOINT>"
SEARCH_INDEX = "lol-official-rag-dev"

ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
actual_workspace_hostname = (urlparse(ctx.apiUrl().get()).hostname or "").strip().lower()
actual_workspace_id = str(ctx.workspaceId().get())
actual_user = ctx.userName().get()
git_head = subprocess.run(
    ["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
    check=True,
    capture_output=True,
    text=True,
    encoding="utf-8",
).stdout.strip()

environment_checks = {
    "workspace_id": actual_workspace_id == EXPECTED_WORKSPACE_ID,
    "user": actual_user == EXPECTED_USER,
    "workspace_hostname": actual_workspace_hostname == EXPECTED_WORKSPACE_HOSTNAME,
    "git_head": git_head == EXPECTED_GIT_HEAD,
}
print(
    json.dumps(
        {
            "workspace_hostname": actual_workspace_hostname,
            "workspace_hostname_length": len(actual_workspace_hostname),
        },
        ensure_ascii=False,
    )
)

if not all(environment_checks.values()):
    raise RuntimeError(f"Environment verification failed: {environment_checks}")

print(json.dumps({
    "workspace_hostname": actual_workspace_hostname,
    "workspace_id": actual_workspace_id,
    "user": actual_user,
    "git_head": git_head,
    "runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION", "unknown"),
    "python": platform.python_version(),
    "checks": environment_checks,
}, ensure_ascii=False))

# COMMAND ----------

import sys

for import_root in (REPO_ROOT, f"{REPO_ROOT}/src"):
    if import_root not in sys.path:
        sys.path.insert(0, import_root)

print({"repo_import_root": REPO_ROOT, "src_import_root_added": True})

# COMMAND ----------

from src.personal_qa.query_router import classify_query
from src.rag.official_query import (
    OfficialSearchPlan,
    build_official_search_plan,
    classify_official_document_type,
    finalize_search_status,
)

print({
    "official_query_imported": True,
    "public_interfaces": [
        "OfficialSearchPlan",
        "build_official_search_plan",
        "classify_official_document_type",
        "finalize_search_status",
    ],
})

# COMMAND ----------

EVALUATION_QUESTIONS = [
    {"question": "말파이트는 어떤 챔피언이야?", "expected_id": "ddragon:champion:Malphite"},
    {"question": "아이템 6655가 뭐야?", "expected_source_id": "6655"},
    {"question": "착취의 손아귀 룬 효과를 알려줘", "expected_id": "ddragon:rune:8437"},
    {"question": "점멸 소환사 주문 효과를 알려줘", "expected_id": "ddragon:summoner_spell:SummonerFlash"},
    {"question": "아레나 점멸은 일반 점멸과 달라?", "expected_id": "ddragon:summoner_spell:SummonerCherryFlash"},
    {"question": "26.18 패치에서 상향된 챔피언은?", "expected_type": "patch_note", "expected_patch": "26.18"},
    {"question": "Queue ID 420은 어떤 게임이야?", "expected_source_id": "420"},
    {"question": "Map ID 11은 어떤 맵이야?", "expected_source_id": "11"},
    {"question": "소환사의 협곡 게임 모드는 뭐야?", "expected_type": "game_mode"},
    {"question": "협곡 게임 타입은 뭐야?", "expected_status": "needs_clarification"},
    {"question": "내 이번 말파이트 경기 어땠어?", "expected_status": "unsupported"},
    {"question": "적팀 라이너보다 내가 뭘 못했어?", "expected_status": "unsupported"},
    {"question": "JADE 모드 점멸", "expected_id": "ddragon:summoner_spell:SummonerFlash_Jade"},
    {"question": "텔레포트와 점멸 효과를 비교해줘", "expected_type": "summoner_spell", "multi_spell": True},
]
print({"evaluation_question_count": len(EVALUATION_QUESTIONS)})

# COMMAND ----------

PLANS = []
for spec in EVALUATION_QUESTIONS:
    question = spec["question"]
    route = classify_query(question)
    plan = build_official_search_plan(question)
    PLANS.append({"spec": spec, "route": route, "plan": plan})

classification_output = [
    {
        "question": row["spec"]["question"],
        "route": row["route"],
        "document_type": row["plan"].document_type,
        "status": row["plan"].status,
    }
    for row in PLANS
]
print(json.dumps(classification_output, ensure_ascii=False, indent=2))

# COMMAND ----------

plan_output = [
    {
        "question": row["spec"]["question"],
        "normalized_query": row["plan"].normalized_query,
        "filter": row["plan"].search_filter,
        "search_mode": row["plan"].search_mode,
        "status": row["plan"].status,
        "preferred_mode": row["plan"].preferred_mode,
        "preferred_source_id": row["plan"].preferred_source_id,
    }
    for row in PLANS
]
print(json.dumps(plan_output, ensure_ascii=False, indent=2))

# COMMAND ----------

import re

AUTH_STATUS = "AUTH_BLOCKED"
AUTH_REASON = None
ACCESS_TOKEN = None
SECRET_SCOPE = "<DATABRICKS_SECRET_SCOPE>"

tenant_id = None
client_id = None
client_secret = None
credential = None
token_result = None
secret_diagnostics = None
credential_created = False
token_request_attempted = False
token_acquired = False
aadsts_code = None
safe_auth_reason = None

try:
    from azure.identity import ClientSecretCredential
except ImportError:
    safe_auth_reason = "azure_identity_not_available"
    AUTH_REASON = safe_auth_reason
else:
    try:
        tenant_id = dbutils.secrets.get(SECRET_SCOPE, "tenant-id")
        client_id = dbutils.secrets.get(SECRET_SCOPE, "client-id")
        client_secret = dbutils.secrets.get(SECRET_SCOPE, "client-secret")
    except Exception:
        safe_auth_reason = "secret_scope_access_failed"
        AUTH_REASON = safe_auth_reason
    else:
        secret_diagnostics = {
            "tenant_id_nonempty": bool(tenant_id),
            "tenant_id_matches_expected": tenant_id == "<AZURE_TENANT_ID>",
            "tenant_id_has_surrounding_whitespace": tenant_id != tenant_id.strip(),
            "tenant_id_has_cr": "\r" in tenant_id,
            "tenant_id_has_lf": "\n" in tenant_id,
            "tenant_id_has_nul": "\x00" in tenant_id,
            "client_id_nonempty": bool(client_id),
            "client_id_matches_expected": client_id == "<AZURE_CLIENT_ID>",
            "client_id_has_surrounding_whitespace": client_id != client_id.strip(),
            "client_id_has_cr": "\r" in client_id,
            "client_id_has_lf": "\n" in client_id,
            "client_id_has_nul": "\x00" in client_id,
            "client_secret_nonempty": bool(client_secret),
            "client_secret_has_surrounding_whitespace": client_secret != client_secret.strip(),
            "client_secret_has_cr": "\r" in client_secret,
            "client_secret_has_lf": "\n" in client_secret,
            "client_secret_has_nul": "\x00" in client_secret,
            "client_secret_has_other_control_character": any(
                (ord(character) < 32 and character not in "\r\n") or ord(character) == 127
                for character in client_secret
            ),
        }
        try:
            credential = ClientSecretCredential(
                tenant_id=tenant_id,
                client_id=client_id,
                client_secret=client_secret,
                logging_enable=False,
                retry_total=0,
            )
            credential_created = True
        except Exception:
            safe_auth_reason = "service_principal_auth_failed_without_aadsts"
            AUTH_REASON = safe_auth_reason
        else:
            token_request_attempted = True
            try:
                token_result = credential.get_token("https://search.azure.com/.default")
                ACCESS_TOKEN = token_result.token
                token_acquired = True
                AUTH_STATUS = "AUTHENTICATED"
                AUTH_REASON = None
            except Exception as exc:
                match = re.search(r"\bAADSTS\d+\b", str(exc))
                aadsts_code = match.group(0) if match else None
                safe_reasons = {
                    "AADSTS7000215": "invalid_client_secret",
                    "AADSTS7000222": "client_secret_expired",
                    "AADSTS700016": "application_not_found",
                    "AADSTS7000112": "service_principal_disabled",
                    "AADSTS7000218": "client_assertion_or_secret_required",
                }
                if aadsts_code:
                    safe_auth_reason = safe_reasons.get(
                        aadsts_code,
                        f"entra_auth_error_{aadsts_code}",
                    )
                else:
                    safe_auth_reason = "service_principal_auth_failed_without_aadsts"
                AUTH_REASON = safe_auth_reason
            finally:
                if credential is not None:
                    try:
                        credential.close()
                    except Exception:
                        pass

auth_diagnostics_output = {
    "secret_diagnostics": secret_diagnostics,
    "credential_created": credential_created,
    "token_request_attempted": token_request_attempted,
    "token_acquired": token_acquired,
    "aadsts_code": aadsts_code,
    "safe_auth_reason": safe_auth_reason,
}
safe_output_json = json.dumps(auth_diagnostics_output, ensure_ascii=False)
for forbidden_value in (tenant_id, client_id, client_secret, ACCESS_TOKEN):
    if forbidden_value and forbidden_value in safe_output_json:
        raise RuntimeError("Unsafe authentication diagnostics output blocked")

tenant_id = None
client_id = None
client_secret = None
token_result = None
credential = None

print(safe_output_json)

# COMMAND ----------

import urllib.error
import urllib.request

SEARCH_RESULTS = {}
SEARCH_CALL_COUNT = 0

if AUTH_STATUS == "AUTHENTICATED":
    for row in PLANS:
        plan = row["plan"]
        question = row["spec"]["question"]
        if plan.status != "resolved":
            continue

        payload = {
            "search": plan.normalized_query,
            "filter": plan.search_filter,
            "searchMode": "any",
            "top": 3,
            "count": True,
            "select": (
                "document_id,title,document_type,content,source_url,version,"
                "patch_version,applicable_modes,source_id"
            ),
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version=2024-07-01",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {ACCESS_TOKEN}",
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            },
        )
        SEARCH_CALL_COUNT += 1
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                SEARCH_RESULTS[question] = json.loads(
                    response.read().decode("utf-8", errors="strict")
                )
        except urllib.error.HTTPError as exc:
            SEARCH_RESULTS[question] = {"error": f"HTTP_{exc.code}", "value": []}
            if exc.code in (401, 403):
                AUTH_STATUS = "AUTH_BLOCKED"
                AUTH_REASON = f"search_data_plane_HTTP_{exc.code}"
                break
        except Exception as exc:
            SEARCH_RESULTS[question] = {"error": type(exc).__name__, "value": []}
            break

ACCESS_TOKEN = None
print({
    "auth_status": AUTH_STATUS,
    "search_call_count": SEARCH_CALL_COUNT,
    "searched_questions": len(SEARCH_RESULTS),
})

# COMMAND ----------

def short_excerpt(value: object, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")

TOP_RESULT_ROWS = []
for row in PLANS:
    question = row["spec"]["question"]
    result = SEARCH_RESULTS.get(question)
    if not result:
        continue
    for rank, document in enumerate(result.get("value", [])[:3], start=1):
        TOP_RESULT_ROWS.append({
            "question": question,
            "rank": rank,
            "document_id": document.get("document_id"),
            "title": document.get("title"),
            "document_type": document.get("document_type"),
            "content_excerpt": short_excerpt(document.get("content")),
            "source_url": document.get("source_url"),
            "version": document.get("version"),
            "patch_version": document.get("patch_version"),
            "applicable_modes": document.get("applicable_modes"),
            "search_score": document.get("@search.score"),
        })

print({"top_result_row_count": len(TOP_RESULT_ROWS)})

# COMMAND ----------

if TOP_RESULT_ROWS:
    display(spark.createDataFrame(TOP_RESULT_ROWS))
else:
    print({"search_results": "not_executed", "auth_status": AUTH_STATUS})

# COMMAND ----------

EVALUATION_ROWS = []
for row in PLANS:
    spec = row["spec"]
    plan = row["plan"]
    question = spec["question"]
    result = SEARCH_RESULTS.get(question)
    values = result.get("value", []) if result else []

    if spec.get("expected_status") == "unsupported":
        passed = (
            plan.status == "unsupported"
            and plan.document_type is None
            and question not in SEARCH_RESULTS
        )
        verdict = "PASS" if passed else "FAIL"
    elif spec.get("expected_status") == "needs_clarification":
        passed = plan.status == "needs_clarification" and question not in SEARCH_RESULTS
        verdict = "PASS" if passed else "FAIL"
    elif AUTH_STATUS != "AUTHENTICATED":
        passed = None
        verdict = "AUTH_BLOCKED"
    else:
        ids = {item.get("document_id") for item in values[:3]}
        source_ids = {str(item.get("source_id")) for item in values[:3]}
        evidence_ok = all(
            item.get("document_type") == plan.document_type
            and bool(item.get("title"))
            and bool(item.get("content"))
            and str(item.get("source_url") or "").startswith("https://")
            for item in values[:3]
        )
        expected_ok = True
        if spec.get("expected_id"):
            expected_ok = spec["expected_id"] in ids
        elif spec.get("expected_source_id"):
            expected_ok = spec["expected_source_id"] in source_ids
        elif spec.get("expected_type"):
            expected_ok = any(
                item.get("document_type") == spec["expected_type"]
                and (
                    not spec.get("expected_patch")
                    or item.get("patch_version") == spec["expected_patch"]
                )
                for item in values[:3]
            )
        if spec.get("multi_spell"):
            expected_ok = expected_ok and plan.preferred_source_id is None
        passed = bool(values) and evidence_ok and expected_ok
        verdict = "PASS" if passed else "FAIL"

    EVALUATION_ROWS.append({
        "question": question,
        "route": row["route"],
        "document_type": plan.document_type,
        "normalized_query": plan.normalized_query,
        "filter": plan.search_filter,
        "status": plan.status,
        "verdict": verdict,
    })

display(spark.createDataFrame(EVALUATION_ROWS))

# COMMAND ----------

if AUTH_STATUS != "AUTHENTICATED":
    OVERALL_STATUS = "AUTH_BLOCKED"
elif all(row["verdict"] == "PASS" for row in EVALUATION_ROWS):
    OVERALL_STATUS = "PASS"
else:
    OVERALL_STATUS = "FAIL"

SUMMARY = {
    "overall_status": OVERALL_STATUS,
    "auth_status": AUTH_STATUS,
    "auth_reason": AUTH_REASON,
    "question_count": len(EVALUATION_ROWS),
    "search_call_count": SEARCH_CALL_COUNT,
    "pass_count": sum(row["verdict"] == "PASS" for row in EVALUATION_ROWS),
    "fail_count": sum(row["verdict"] == "FAIL" for row in EVALUATION_ROWS),
    "auth_blocked_count": sum(row["verdict"] == "AUTH_BLOCKED" for row in EVALUATION_ROWS),
    "personal_questions_excluded": all(
        row["verdict"] == "PASS"
        for row in EVALUATION_ROWS
        if row["status"] == "unsupported"
    ),
}
print(json.dumps(SUMMARY, ensure_ascii=False, indent=2))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 다음 단계
# MAGIC Azure OpenAI 연결 전에는 Databricks Compute에서 사용할 사용자 또는 애플리케이션 Entra ID 인증 경로와 Search Index Data Reader 권한을 리소스 범위에서 확정해야 한다. 인증이 준비된 뒤에도 검색 근거를 먼저 검증하고, 별도 승인 전에는 모델 배포·임베딩·LLM 호출을 수행하지 않는다.
