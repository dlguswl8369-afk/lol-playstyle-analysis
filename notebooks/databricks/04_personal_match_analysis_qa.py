# Databricks notebook source
# GitHub backup copy: configure YOUR_* placeholders before use; execution outputs are not included.
# MAGIC %md
# MAGIC # LoL 개인 경기 분석 Data Agent QA
# MAGIC
# MAGIC 익명화된 최근 솔로랭크 경기 수치를 Python으로 먼저 계산한 뒤, 필요한 공식정보만 Azure AI Search에서 조회하고 Azure OpenAI로 근거 기반 한국어 답변을 생성한다.
# MAGIC PUUID, 소환사명, Secret, Token, Authorization Header와 전체 원문 Context는 출력하지 않는다.

# COMMAND ----------

from urllib.parse import urlparse
import hashlib
import json
import re
import urllib.request

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
print(json.dumps({
    "runtime_hostname": runtime_hostname,
    "workspace_id": workspace_id,
    "current_user": current_user,
    "environment_checks": environment_checks,
}, ensure_ascii=False))
if not all(environment_checks.values()):
    raise RuntimeError("environment_validation_failed")

# COMMAND ----------

import sys
import time
from collections import Counter
from pathlib import Path
from statistics import fmean

from azure.identity import ClientSecretCredential

GIT_ROOT = Path("/Workspace/Users/YOUR_DATABRICKS_USER/lol-playstyle-analysis")
SRC_ROOT = GIT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from personal_qa.match_analysis import MatchAnalysisService
from personal_qa.opponent_comparison import compare_with_position_opponent
from personal_qa.query_router import classify_query
from rag.official_query import build_official_search_plan, finalize_search_status

DATA_ROOT = Path("/Workspace/Users/YOUR_DATABRICKS_USER/rag_hyunji_dev/input/personal_qa")
DATA_FILES = {
    "matches": DATA_ROOT / "match_summaries.jsonl",
    "participants": DATA_ROOT / "match_participants.jsonl",
    "targets": DATA_ROOT / "target_matches.jsonl",
}
SEARCH_ENDPOINT = "https://5dt-team3-lol-search-dev.search.windows.net"
SEARCH_INDEX = "lol-official-rag-dev"
SEARCH_API_VERSION = "2024-07-01"
OPENAI_ENDPOINT = "https://5dt-team3-lol-openai-dev.openai.azure.com"
OPENAI_DEPLOYMENT = "gpt-5-mini-rag"
OPENAI_API_VERSION = "v1"

QUESTIONS = [
    "내 이번 말파이트 경기 어땠어?",
    "적팀 라이너보다 내가 뭘 못했어?",
    "최근 20경기에서 내 플레이스타일은 어때?",
    "내가 개선해야 할 점 3가지를 알려줘",
    "말파이트는 어떤 챔피언이야?",
    "점멸과 순간이동의 차이를 알려줘",
]


def read_jsonl(path):
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


matches = read_jsonl(DATA_FILES["matches"])
participants = read_jsonl(DATA_FILES["participants"])
target_matches = read_jsonl(DATA_FILES["targets"])
target_matches = sorted(target_matches, key=lambda row: row["game_start_utc"], reverse=True)

forbidden_fields = {"puuid", "summoner_name", "summoner_id", "riot_id", "game_id"}
observed_fields = set().union(*(row.keys() for row in participants))
data_checks = {
    "match_count_20": len(matches) == 20,
    "participant_count_200": len(participants) == 200,
    "target_count_20": len(target_matches) == 20,
    "ten_participants_per_match": all(
        sum(row["match_id"] == match["match_id"] for row in participants) == 10
        for match in matches
    ),
    "queue_420_only": all(match["queue_id"] == 420 for match in matches),
    "target_alias_only": all(row["player_id"] == "TARGET_PLAYER" for row in target_matches),
    "pii_fields_absent": not bool(forbidden_fields & observed_fields),
}
if not all(data_checks.values()):
    raise RuntimeError("personal_data_validation_failed")

data_hashes = {name: sha256_file(path) for name, path in DATA_FILES.items()}
analysis_service = MatchAnalysisService(matches, participants)
route_checks = {question: classify_query(question) for question in QUESTIONS}
print(json.dumps({
    "data_checks": data_checks,
    "data_counts": {
        "matches": len(matches),
        "participants": len(participants),
        "target_matches": len(target_matches),
    },
    "available_fields": sorted(observed_fields),
    "unavailable_fields": [
        "cs", "cs_per_min", "gold_diff_10", "gold_diff_15", "cs_diff_10", "cs_diff_15"
    ],
    "route_checks": route_checks,
}, ensure_ascii=False))

# COMMAND ----------

def numeric_average(rows, field):
    values = [float(row[field]) for row in rows if isinstance(row.get(field), (int, float))]
    return round(fmean(values), 4) if values else None


def aggregate_matches(rows):
    if not rows:
        return None
    total_kills = sum(int(row["kills"]) for row in rows)
    total_deaths = sum(int(row["deaths"]) for row in rows)
    total_assists = sum(int(row["assists"]) for row in rows)
    return {
        "match_count": len(rows),
        "win_rate": round(sum(bool(row["win"]) for row in rows) / len(rows), 4),
        "kda": round((total_kills + total_assists) / max(total_deaths, 1), 4),
        "avg_kills": numeric_average(rows, "kills"),
        "avg_deaths": numeric_average(rows, "deaths"),
        "avg_assists": numeric_average(rows, "assists"),
        "avg_kill_participation": numeric_average(rows, "kill_participation"),
        "avg_cs": numeric_average(rows, "cs"),
        "avg_cs_per_min": numeric_average(rows, "cs_per_min"),
        "avg_gold_per_min": numeric_average(rows, "gold_per_min"),
        "avg_damage_per_min": numeric_average(rows, "damage_per_min"),
        "avg_damage_taken_per_min": numeric_average(rows, "damage_taken_per_min"),
        "avg_vision_per_min": numeric_average(rows, "vision_per_min"),
        "avg_wards_placed": numeric_average(rows, "wards_placed"),
        "avg_wards_killed": numeric_average(rows, "wards_killed"),
        "avg_game_duration_seconds": numeric_average(rows, "game_duration_seconds"),
    }


valid_target_matches = [row for row in target_matches if row["game_duration_seconds"] >= 300]
remake_match_ids = [row["match_id"] for row in target_matches if row["game_duration_seconds"] < 300]
latest_match = valid_target_matches[0]
malphite_matches = [
    row for row in valid_target_matches if row["champion_name"].casefold() == "malphite"
]


def safe_target_summary(row):
    fields = (
        "match_id", "champion_name", "team_position", "win", "kills", "deaths", "assists",
        "kda", "kill_participation", "cs", "cs_per_min", "gold_earned", "gold_per_min",
        "damage_to_champions", "damage_per_min", "damage_taken", "damage_taken_per_min",
        "vision_score", "vision_per_min", "wards_placed", "wards_killed",
        "game_duration_seconds",
    )
    return {field: row.get(field) for field in fields}


def safe_opponent_comparison(match_id):
    comparison = compare_with_position_opponent(participants, match_id, "TARGET_PLAYER")
    return {
        "match_id": match_id,
        "status": comparison.get("opponent_match_status"),
        "reason": comparison.get("reason"),
        "target_champion": (comparison.get("target") or {}).get("champion_name"),
        "opponent_champion": (comparison.get("opponent") or {}).get("champion_name"),
        "metric_comparisons": comparison.get("metric_comparisons") or [],
        "strengths": comparison.get("strengths") or [],
        "weaknesses": comparison.get("weaknesses") or [],
        "limitations": comparison.get("limitations") or [],
    }


recent_aggregate = aggregate_matches(valid_target_matches)
recent_five = valid_target_matches[:5]
previous_matches = valid_target_matches[5:]
recent_five_aggregate = aggregate_matches(recent_five)
previous_aggregate = aggregate_matches(previous_matches)


def build_improvement_evidence():
    definitions = [
        ("평균 데스", "avg_deaths", False),
        ("분당 골드", "avg_gold_per_min", True),
        ("분당 챔피언 피해량", "avg_damage_per_min", True),
        ("킬 관여율", "avg_kill_participation", True),
        ("분당 시야 점수", "avg_vision_per_min", True),
        ("평균 와드 제거", "avg_wards_killed", True),
    ]
    candidates = []
    for label, field, higher_is_better in definitions:
        recent_value = recent_five_aggregate.get(field)
        previous_value = previous_aggregate.get(field)
        if recent_value is None or previous_value is None:
            continue
        delta = round(recent_value - previous_value, 4)
        deterioration = -delta if higher_is_better else delta
        if deterioration > 0:
            candidates.append({
                "metric": label,
                "field": field,
                "recent_5": recent_value,
                "previous_period": previous_value,
                "delta": delta,
                "higher_is_better": higher_is_better,
                "deterioration": deterioration,
            })
    candidates.sort(key=lambda item: item["deterioration"], reverse=True)
    return candidates[:3]


personal_evidence = {
    QUESTIONS[0]: {
        "status": "grounded" if malphite_matches else "insufficient",
        "question_type": "latest_champion_match",
        "requested_champion": "Malphite",
        "match": safe_target_summary(malphite_matches[0]) if malphite_matches else None,
        "available_target_champions": dict(Counter(row["champion_name"] for row in valid_target_matches)),
        "limitations": ["최근 20경기 표본에 말파이트 경기가 없어 임의의 경기를 대체하지 않습니다."] if not malphite_matches else [],
        "source_match_ids": [malphite_matches[0]["match_id"]] if malphite_matches else [],
    },
    QUESTIONS[1]: {
        "status": "grounded",
        "question_type": "latest_match_position_opponent_comparison",
        "target": safe_target_summary(latest_match),
        "opponent_comparison": safe_opponent_comparison(latest_match["match_id"]),
        "unavailable_metrics": ["cs", "cs_per_min", "10분·15분 골드/CS 차이"],
        "source_match_ids": [latest_match["match_id"]],
    },
    QUESTIONS[2]: {
        "status": "grounded",
        "question_type": "recent_20_playstyle",
        "source_match_count": len(target_matches),
        "valid_match_count": len(valid_target_matches),
        "excluded_remake_match_ids": remake_match_ids,
        "aggregate": recent_aggregate,
        "champion_counts": dict(Counter(row["champion_name"] for row in valid_target_matches)),
        "position_counts": dict(Counter(row["team_position"] for row in valid_target_matches)),
        "unavailable_metrics": ["CS", "분당 CS", "10분·15분 골드/CS 차이"],
        "source_match_ids": [row["match_id"] for row in valid_target_matches],
    },
    QUESTIONS[3]: {
        "status": "grounded",
        "question_type": "deterministic_improvement_priorities",
        "comparison_basis": f"최근 5경기와 직전 {len(previous_matches)}경기의 평균 비교",
        "improvement_evidence": build_improvement_evidence(),
        "recent_5": recent_five_aggregate,
        "previous_period": previous_aggregate,
        "unavailable_metrics": ["CS", "분당 CS", "10분·15분 골드/CS 차이"],
        "source_match_ids": [row["match_id"] for row in valid_target_matches],
    },
}

if personal_evidence[QUESTIONS[1]]["opponent_comparison"]["status"] != "matched":
    personal_evidence[QUESTIONS[1]]["status"] = "insufficient"
if len(personal_evidence[QUESTIONS[3]]["improvement_evidence"]) != 3:
    raise RuntimeError("insufficient_deterministic_improvement_evidence")

print(json.dumps({
    "latest_match_id": latest_match["match_id"],
    "malphite_match_count": len(malphite_matches),
    "valid_recent_match_count": len(valid_target_matches),
    "excluded_remake_count": len(remake_match_ids),
    "opponent_status": personal_evidence[QUESTIONS[1]]["opponent_comparison"]["status"],
    "improvement_evidence_count": len(personal_evidence[QUESTIONS[3]]["improvement_evidence"]),
}, ensure_ascii=False))

# COMMAND ----------

tenant_id = dbutils.secrets.get("YOUR_SECRET_SCOPE", "tenant-id")
client_id = dbutils.secrets.get("YOUR_SECRET_SCOPE", "client-id")
client_secret = dbutils.secrets.get("YOUR_SECRET_SCOPE", "client-secret")

secret_diagnostics = {
    "tenant_id_nonempty": bool(tenant_id),
    "tenant_id_has_format_issue": tenant_id != tenant_id.strip() or any(ch in tenant_id for ch in "\r\n\x00"),
    "client_id_nonempty": bool(client_id),
    "client_id_has_format_issue": client_id != client_id.strip() or any(ch in client_id for ch in "\r\n\x00"),
    "client_secret_nonempty": bool(client_secret),
    "client_secret_has_format_issue": client_secret != client_secret.strip() or any(ch in client_secret for ch in "\r\n\x00"),
}
if not all((tenant_id, client_id, client_secret)) or any(
    value for key, value in secret_diagnostics.items() if key.endswith("format_issue")
):
    raise RuntimeError("secret_format_issue")

credential = ClientSecretCredential(
    tenant_id=tenant_id,
    client_id=client_id,
    client_secret=client_secret,
    logging_enable=False,
    retry_total=0,
)
search_token = credential.get_token("https://search.azure.com/.default").token
openai_token = credential.get_token("https://cognitiveservices.azure.com/.default").token
tenant_id = None
client_id = None
client_secret = None
print(json.dumps({
    "secret_diagnostics": secret_diagnostics,
    "search_token_acquired": bool(search_token),
    "openai_token_acquired": bool(openai_token),
}, ensure_ascii=False))

# COMMAND ----------

SEARCH_URL = f"{SEARCH_ENDPOINT}/indexes/{SEARCH_INDEX}/docs/search?api-version={SEARCH_API_VERSION}"
OPENAI_URL = f"{OPENAI_ENDPOINT}/openai/{OPENAI_API_VERSION}/responses"
SEARCH_SELECT = ",".join([
    "document_id", "document_type", "title", "content", "source_url",
    "version", "patch_version", "applicable_modes", "source_id",
])

SYSTEM_PROMPT = """당신은 LoL 개인 경기 분석 Data Agent다.
EVIDENCE에 포함된 계산된 경기 통계와 OFFICIAL_CONTEXT만 근거로 짧고 명확한 한국어로 답한다.
경기 수치를 직접 다시 계산하거나 EVIDENCE에 없는 원인, 사실, 개선점을 추측하지 않는다.
데이터가 없으면 다른 경기로 대체하지 말고 '데이터 없음' 또는 '확인할 수 없음'이라고 명시한다.
개인정보를 추정하거나 출력하지 않고, 경기 출처는 제공된 익명 Match ID만 사용한다.
CS와 10분·15분 지표가 없으면 그대로 제한사항으로 밝힌다.
공식정보 질문은 OFFICIAL_CONTEXT만 사용하고 패치·게임 모드를 섞지 않는다.
공식 출처는 제공된 document_id, title, source_url만 그대로 반환한다.
개선점은 EVIDENCE.improvement_evidence에 있는 항목만 사용한다.
answer에는 한 줄 평가, 핵심 수치, 잘한 점, 아쉬운 점, 개선할 점, 비교 기준을 질문에 맞게 포함한다."""

CITATION_SCHEMA = {
    "type": "object",
    "properties": {
        "document_id": {"type": "string"},
        "title": {"type": "string"},
        "source_url": {"type": ["string", "null"]},
    },
    "required": ["document_id", "title", "source_url"],
    "additionalProperties": False,
}
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["grounded", "insufficient"]},
        "one_line_evaluation": {"type": "string"},
        "answer": {"type": "string"},
        "strengths": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "weaknesses": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "improvements": {"type": "array", "maxItems": 3, "items": {"type": "string"}},
        "comparison_basis": {"type": "string"},
        "match_ids": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
        "official_citations": {"type": "array", "maxItems": 3, "items": CITATION_SCHEMA},
    },
    "required": [
        "status", "one_line_evaluation", "answer", "strengths", "weaknesses",
        "improvements", "comparison_basis", "match_ids", "official_citations",
    ],
    "additionalProperties": False,
}

search_call_count = 0
openai_call_count = 0


def post_json(url, body, token, timeout):
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def search_official(question):
    global search_call_count
    if question == QUESTIONS[5]:
        search_text = "*"
        search_filter = (
            "document_type eq 'summoner_spell' and "
            "(source_id eq 'SummonerFlash' or source_id eq 'SummonerTeleport')"
        )
        expected_ids = {
            "ddragon:summoner_spell:SummonerFlash",
            "ddragon:summoner_spell:SummonerTeleport",
        }
    else:
        plan = build_official_search_plan(question)
        if plan.status in {"unsupported", "needs_clarification"}:
            return [], plan.search_filter, plan.normalized_query, set(), plan.status, None
        search_text = plan.normalized_query
        search_filter = plan.search_filter
        expected_ids = {"ddragon:champion:Malphite"}
    body = {
        "search": search_text,
        "filter": search_filter,
        "top": 5,
        "select": SEARCH_SELECT,
        "queryType": "simple",
        "searchMode": "any",
    }
    started = time.perf_counter()
    search_call_count += 1
    response = post_json(SEARCH_URL, body, search_token, 30)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    documents = response.get("value", [])
    if question == QUESTIONS[5]:
        by_source = {str(doc.get("source_id")): doc for doc in documents}
        documents = [
            by_source[source_id]
            for source_id in ("SummonerFlash", "SummonerTeleport")
            if source_id in by_source
        ]
        status = "resolved" if documents else "no_result"
    else:
        status = finalize_search_status(plan, len(documents)).status
        documents = documents[:3]
    return documents[:3], search_filter, search_text, expected_ids, status, latency_ms


def official_context(documents):
    return [
        {
            "document_id": str(doc.get("document_id") or ""),
            "source_id": str(doc.get("source_id") or ""),
            "title": str(doc.get("title") or ""),
            "document_type": str(doc.get("document_type") or ""),
            "content": str(doc.get("content") or "")[:2800],
            "source_url": doc.get("source_url"),
            "patch_version": doc.get("patch_version"),
            "applicable_modes": doc.get("applicable_modes") or [],
        }
        for doc in documents
    ]


def extract_output_text(response):
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                return content.get("text", "")
    return ""


def generate_answer(question, route, evidence, documents):
    global openai_call_count
    model_input = json.dumps(
        {
            "question": question,
            "route": route,
            "evidence": evidence,
            "official_context": official_context(documents),
        },
        ensure_ascii=False,
    )
    body = {
        "model": OPENAI_DEPLOYMENT,
        "instructions": SYSTEM_PROMPT,
        "input": model_input,
        "reasoning": {"effort": "minimal"},
        "max_output_tokens": 700,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "personal_match_data_agent_answer",
                "schema": OUTPUT_SCHEMA,
                "strict": True,
            }
        },
    }
    started = time.perf_counter()
    openai_call_count += 1
    response = post_json(OPENAI_URL, body, openai_token, 60)
    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    structured = json.loads(extract_output_text(response))
    return structured, response.get("usage", {}), latency_ms, response.get("status")

# COMMAND ----------

results = []
for question in QUESTIONS:
    is_official = question in QUESTIONS[4:]
    route = "knowledge_query" if is_official else "personal_match_query"
    documents = []
    search_filter = None
    search_text = None
    expected_ids = set()
    search_status = None
    search_latency_ms = None
    evidence = personal_evidence.get(question, {"status": "grounded", "question_type": "official_information"})
    if is_official:
        documents, search_filter, search_text, expected_ids, search_status, search_latency_ms = search_official(question)

    answer, usage, ai_latency_ms, response_status = generate_answer(
        question, route, evidence, documents
    )
    context_ids = {str(doc.get("document_id")) for doc in documents}
    cited_ids = {str(item.get("document_id")) for item in answer.get("official_citations", [])}
    expected_status = evidence.get("status") if not is_official else "grounded"
    checks = {
        "response_completed": response_status == "completed",
        "answer_nonempty": bool(answer.get("answer")),
        "answer_has_korean": bool(re.search(r"[가-힣]", answer.get("answer") or "")),
        "status_matches": answer.get("status") == expected_status,
        "citations_grounded": cited_ids.issubset(context_ids),
        "no_raw_identifier": not bool(re.search(r"(?:puuid|summoner|riot.?id)", answer.get("answer") or "", re.IGNORECASE)),
    }
    if is_official:
        checks["search_resolved"] = search_status == "resolved"
        checks["expected_documents_present"] = expected_ids.issubset(context_ids)
        checks["citations_present"] = bool(answer.get("official_citations"))
    else:
        checks["no_unexpected_official_context"] = not documents
        checks["source_match_ids_valid"] = set(answer.get("match_ids") or []).issubset(
            set(evidence.get("source_match_ids") or [])
        )
    if question == QUESTIONS[0]:
        checks["malphite_absence_preserved"] = not malphite_matches and answer.get("status") == "insufficient"
    if question == QUESTIONS[1]:
        checks["opponent_exactly_matched"] = evidence["opponent_comparison"]["status"] == "matched"
    if question == QUESTIONS[3]:
        checks["three_improvements"] = len(answer.get("improvements") or []) == 3
    if question == QUESTIONS[5]:
        source_ids = {str(doc.get("source_id")) for doc in documents}
        checks["exact_spell_sources"] = source_ids == {"SummonerFlash", "SummonerTeleport"}
        checks["special_mode_excluded"] = not ({"SummonerCherryFlash", "SummonerFlash_Jade"} & source_ids)

    verdict = "PASS" if all(checks.values()) else "FAIL"
    results.append({
        "question": question,
        "route": route,
        "search_text": search_text,
        "search_filter": search_filter,
        "search_status": search_status,
        "source_match_ids": evidence.get("source_match_ids") or [],
        "calculated_evidence": evidence,
        "top_documents": [
            {
                "document_id": doc.get("document_id"),
                "source_id": doc.get("source_id"),
                "title": doc.get("title"),
                "source_url": doc.get("source_url"),
                "applicable_modes": doc.get("applicable_modes") or [],
            }
            for doc in documents
        ],
        "answer": answer,
        "search_latency_ms": search_latency_ms,
        "ai_latency_ms": ai_latency_ms,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "checks": checks,
        "verdict": verdict,
        "failure_reason": None if verdict == "PASS" else ",".join(
            key for key, value in checks.items() if not value
        ),
    })

search_token = None
openai_token = None
credential.close()
credential = None

print(json.dumps({
    "search_call_count": search_call_count,
    "openai_call_count": openai_call_count,
    "result_count": len(results),
}, ensure_ascii=False))

# COMMAND ----------

from pyspark.sql.types import DoubleType, LongType, StringType, StructField, StructType

RESULT_SCHEMA = StructType([
    StructField("question", StringType(), False),
    StructField("route", StringType(), False),
    StructField("search_text", StringType(), True),
    StructField("search_filter", StringType(), True),
    StructField("source_match_ids", StringType(), False),
    StructField("calculated_evidence", StringType(), False),
    StructField("top_documents", StringType(), False),
    StructField("answer", StringType(), False),
    StructField("search_latency_ms", DoubleType(), True),
    StructField("ai_latency_ms", DoubleType(), False),
    StructField("input_tokens", LongType(), False),
    StructField("output_tokens", LongType(), False),
    StructField("verdict", StringType(), False),
    StructField("failure_reason", StringType(), True),
])

flat_results = [
    {
        "question": item["question"],
        "route": item["route"],
        "search_text": item["search_text"],
        "search_filter": item["search_filter"],
        "source_match_ids": json.dumps(item["source_match_ids"], ensure_ascii=False),
        "calculated_evidence": json.dumps(item["calculated_evidence"], ensure_ascii=False),
        "top_documents": json.dumps(item["top_documents"], ensure_ascii=False),
        "answer": json.dumps(item["answer"], ensure_ascii=False),
        "search_latency_ms": item["search_latency_ms"],
        "ai_latency_ms": item["ai_latency_ms"],
        "input_tokens": item["input_tokens"],
        "output_tokens": item["output_tokens"],
        "verdict": item["verdict"],
        "failure_reason": item["failure_reason"],
    }
    for item in results
]
display(spark.createDataFrame(flat_results, schema=RESULT_SCHEMA))

pass_count = sum(item["verdict"] == "PASS" for item in results)
fail_count = len(results) - pass_count
input_tokens = sum(item["input_tokens"] for item in results)
output_tokens = sum(item["output_tokens"] for item in results)
estimated_cost_usd = round(
    input_tokens / 1_000_000 * 0.25 + output_tokens / 1_000_000 * 2.00,
    8,
)
summary = {
    "overall_status": "PASS" if (
        pass_count == 6 and search_call_count <= 6 and openai_call_count <= 6
    ) else "FAIL",
    "question_count": len(results),
    "pass_count": pass_count,
    "fail_count": fail_count,
    "search_call_count": search_call_count,
    "openai_call_count": openai_call_count,
    "input_tokens": input_tokens,
    "output_tokens": output_tokens,
    "estimated_cost_usd": estimated_cost_usd,
    "data_counts": {
        "matches": len(matches),
        "participants": len(participants),
        "target_matches": len(target_matches),
        "valid_matches": len(valid_target_matches),
        "excluded_remakes": len(remake_match_ids),
    },
    "data_hashes": data_hashes,
}
report = {
    "summary": summary,
    "results": results,
    "environment_checks": environment_checks,
    "data_checks": data_checks,
}
print(json.dumps(summary, ensure_ascii=False))
if summary["overall_status"] != "PASS":
    raise RuntimeError("personal_match_data_agent_qa_failed")
dbutils.notebook.exit(json.dumps(report, ensure_ascii=False))