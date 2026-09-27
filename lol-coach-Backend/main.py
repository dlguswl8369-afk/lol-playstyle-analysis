import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any, Literal

import requests
import uvicorn
from azure.identity import ClientSecretCredential
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
FRONTEND_DIR = PROJECT_DIR / "lol-coach-Frontend"
SRC_DIR = BASE_DIR / "src"

# Load configuration by file location rather than the shell's current working
# directory. This lets the app start from either the repository root or the
# backend directory.
load_dotenv(BASE_DIR / ".env", override=False)
load_dotenv(PROJECT_DIR / "lol-rag-local-test.env", override=False)

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lol_rag.orchestrator import AgentDependencies, answer_question  # noqa: E402

DATABRICKS_JOB_ID = os.getenv("DATABRICKS_JOB_ID")
DATABRICKS_HOST = os.getenv("DATABRICKS_HOST")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

ANALYSIS_CACHE_TTL_SECONDS = int(os.getenv("ANALYSIS_CACHE_TTL_SECONDS", "600"))
COACH_CACHE_TTL_SECONDS = int(os.getenv("COACH_CACHE_TTL_SECONDS", "600"))
_PLAYER_CONTEXT_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_COACH_RESULT_CACHE: dict[tuple[str, str], dict[str, Any]] = {}
_CACHE_LOCK = Lock()
_RAG_LOCK = Lock()
_RAG_DEPENDENCIES: AgentDependencies | None = None


app = FastAPI(title="LoL AI Coaching API")


class LolRagRequest(BaseModel):
    mode: Literal["official", "personal"] | None = None
    game_name: str | None = None
    riot_id: str | None = None
    tag_line: str | None = None
    match_count: int = Field(default=10, ge=1, le=20)
    question: str = Field(min_length=1)


class PlayerAnalysisRequest(BaseModel):
    riot_id: str = Field(min_length=1)
    tag_line: str = Field(min_length=1)
    match_count: int = Field(default=10, ge=1, le=20)


class CoachRequest(BaseModel):
    question: str = Field(min_length=1)
    riot_id: str | None = None
    tag_line: str | None = None
    match_count: int = Field(default=10, ge=1, le=20)


class CoachingReportRequest(BaseModel):
    riot_id: str = Field(min_length=1)
    tag_line: str = Field(min_length=1)
    match_count: int = Field(default=10, ge=1, le=20)


def _player_cache_key(game_name: str, tag_line: str) -> tuple[str, str]:
    return game_name.strip().casefold(), tag_line.strip().casefold()


def _cache_get(cache: dict, key: tuple[str, str]) -> Any | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        entry = cache.get(key)
        if not entry:
            return None
        if entry["expires_at"] <= now:
            cache.pop(key, None)
            return None
        return entry["value"]


def _cache_set(cache: dict, key: tuple[str, str], value: Any, ttl: int) -> None:
    with _CACHE_LOCK:
        cache[key] = {
            "expires_at": time.monotonic() + max(ttl, 1),
            "value": value,
        }


def _average(matches: list[dict[str, Any]], key: str) -> float:
    values = [float(match[key]) for match in matches if match.get(key) is not None]
    return sum(values) / len(values) if values else 0.0


def build_player_summary(matches: list[dict[str, Any]]) -> dict[str, Any]:
    """Build factual aggregates once on the server for dashboard display."""
    wins = sum(bool(match.get("win")) for match in matches)
    games = len(matches)
    return {
        "games": games,
        "wins": wins,
        "win_rate": (wins / games * 100) if games else 0.0,
        "metrics": {
            "kda": _average(matches, "kda"),
            "cs_per_min": _average(matches, "cs_per_min"),
            "gold_per_min": _average(matches, "gold_per_min"),
            "kill_participation": _average(matches, "kill_participation") * 100,
            "vision_score_per_min": _average(matches, "vision_score_per_min"),
            "damage_per_min": _average(matches, "damage_per_min"),
            "objective_damage_per_min": _average(matches, "objective_damage_per_min"),
        },
    }


def missing_rag_config() -> list[str]:
    required = {
        "AZURE_TENANT_ID": os.getenv("AZURE_TENANT_ID"),
        "AZURE_CLIENT_ID": os.getenv("AZURE_CLIENT_ID"),
        "AZURE_CLIENT_SECRET": os.getenv("AZURE_CLIENT_SECRET"),
        "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT"),
        "AZURE_SEARCH_INDEX": os.getenv("AZURE_SEARCH_INDEX"),
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "AZURE_OPENAI_DEPLOYMENT": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    }
    return [name for name, value in required.items() if not value]


def get_rag_dependencies() -> AgentDependencies:
    global _RAG_DEPENDENCIES
    missing = missing_rag_config()
    if missing:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "RAG_CONFIG_MISSING", "missing": missing},
        )
    with _RAG_LOCK:
        if _RAG_DEPENDENCIES is None:
            credential = ClientSecretCredential(
                tenant_id=os.environ["AZURE_TENANT_ID"],
                client_id=os.environ["AZURE_CLIENT_ID"],
                client_secret=os.environ["AZURE_CLIENT_SECRET"],
            )
            _RAG_DEPENDENCIES = AgentDependencies(
                entra_credential=credential,
                riot_api_key=RIOT_API_KEY,
            )
    return _RAG_DEPENDENCIES


_SENSITIVE_RESPONSE_KEYS = {
    "account_id",
    "authorization",
    "client_secret",
    "puuid",
    "raw_match_id",
    "riot_api_key",
    "summoner_id",
    "target_puuid",
    "token",
    "x-riot-token",
}


def safe_rag_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: safe_rag_response(item)
            for key, item in value.items()
            if str(key).casefold() not in _SENSITIVE_RESPONSE_KEYS
        }
    if isinstance(value, list):
        return [safe_rag_response(item) for item in value]
    return value


def parse_riot_id_query(value: str) -> tuple[str, str] | None:
    """Parse an exact ``gameName#tagLine`` query without treating prose as an ID."""

    matched = re.fullmatch(r"\s*([^#\r\n]{1,100}?)\s*#\s*([^#\s]{2,10})\s*", value)
    if not matched:
        return None
    game_name, tag_line = (part.strip() for part in matched.groups())
    return (game_name, tag_line) if game_name and tag_line else None


def parse_embedded_riot_question(value: str) -> tuple[str, str, str] | None:
    """Parse ``gameName/#tagLine/ question`` and ``gameName#tagLine question``."""

    patterns = (
        r"\s*([^#/\r\n]{1,100}?)\s*/\s*#?([^#/\s]{2,10})\s*/\s*(.+?)\s*",
        r"\s*([^#\r\n]{1,100}?)\s*#\s*([^#\s]{2,10})\s+(.+?)\s*",
    )
    for pattern in patterns:
        matched = re.fullmatch(pattern, value)
        if not matched:
            continue
        game_name, tag_line, question = (part.strip() for part in matched.groups())
        if game_name and tag_line and question:
            return game_name, tag_line, question
    return None


def needs_riot_id_context(question: str) -> bool:
    return bool(
        re.search(
            r"^\s*[\"']?\s*(?:나|내가|나는|내|나의)\b|"
            r"(?:최근|이번)\s*(?:전적|경기).*(?:어때|어떻게|알려|요약)",
            question,
            re.IGNORECASE,
        )
    )


def missing_databricks_config() -> list[str]:
    config = {
        "DATABRICKS_HOST": DATABRICKS_HOST,
        "DATABRICKS_TOKEN": DATABRICKS_TOKEN,
        "DATABRICKS_JOB_ID": DATABRICKS_JOB_ID,
    }
    return [name for name, value in config.items() if not value]


def simple_player_statistics_response(
    game_name: str,
    tag_line: str,
    analysis: dict[str, Any],
    elapsed_ms: float,
) -> dict[str, Any]:
    summary = analysis.get("summary") or {}
    metrics = summary.get("metrics") or {}
    main_champion = summary.get("main_champion") or {}
    rank = analysis.get("rank") or {}
    games = int(summary.get("games") or 0)
    wins = int(summary.get("wins") or 0)
    losses = max(games - wins, 0)
    win_rate = float(summary.get("win_rate") or 0)

    if games == 0:
        answer = f"{game_name}#{tag_line}의 최근 솔로랭크 경기 기록이 없습니다."
        status = "NO_MATCH_DATA"
    else:
        rank_text = "UNRANKED"
        if rank:
            rank_text = " ".join(
                str(value)
                for value in (
                    rank.get("tier"),
                    rank.get("rank"),
                    f"{rank.get('leaguePoints', 0)} LP",
                )
                if value not in (None, "")
            )
        champion_text = "확인할 수 없음"
        if main_champion.get("champion_name"):
            champion_text = (
                f"{main_champion['champion_name']} "
                f"({int(main_champion.get('match_count') or 0)}경기, "
                f"승률 {float(main_champion.get('win_rate') or 0):.0f}%)"
            )
        answer = "\n".join(
            [
                f"{game_name}#{tag_line} 최근 {games}경기 통계",
                f"전적: {wins}승 {losses}패 · 승률 {win_rate:.0f}%",
                f"현재 티어: {rank_text}",
                f"주력 챔피언: {champion_text}",
                (
                    f"평균 지표: KDA {float(metrics.get('kda') or 0):.2f} · "
                    f"분당 CS {float(metrics.get('cs_per_min') or 0):.1f} · "
                    f"분당 골드 {float(metrics.get('gold_per_min') or 0):.0f} · "
                    f"분당 피해 {float(metrics.get('damage_per_min') or 0):.0f} · "
                    f"분당 시야 {float(metrics.get('vision_score_per_min') or 0):.2f}"
                ),
            ]
        )
        status = "PASS"

    return {
        "status": status,
        "route": "personal_match",
        "route_reason": "direct_riot_id_query",
        "subject": {"game_name": game_name, "tag_line": tag_line},
        "answer": answer,
        "statistics": {
            "games": games,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "main_champion": main_champion or None,
            "metrics": metrics,
            "rank": rank or None,
        },
        "citations": [],
        "official_documents": [],
        "usage": {
            "search_calls": 0,
            "openai_calls": 0,
            "elapsed_ms": round(elapsed_ms, 1),
        },
    }


def puuid_to_player_id(puuid: str) -> str:
    return hashlib.sha256(puuid.encode("utf-8")).hexdigest()[:24]


# 솔로 티어 가져오기
def get_solo_tier(puuid: str) -> str:
    """Return current solo-queue tier or UNKNOWN when unavailable."""
    url = f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}"
    headers = {"X-Riot-Token": RIOT_API_KEY}

    try:
        response = requests.get(url, headers=headers, timeout=30)
    except requests.RequestException:
        return "UNKNOWN"

    if response.status_code != 200:
        return "UNKNOWN"

    try:
        entries = response.json()
    except ValueError:
        return "UNKNOWN"

    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return str(entry.get("tier") or "UNKNOWN").upper()

    return "UNKNOWN"


# 솔로 랭크 가져오기
def get_solo_rank(puuid: str) -> dict[str, Any] | None:
    """Return the solo-queue rank in the shape expected by the frontend."""
    response = requests.get(
        f"https://kr.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}",
        headers={"X-Riot-Token": RIOT_API_KEY},
        timeout=30,
    )
    if response.status_code != 200:
        return None

    for entry in response.json():
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return {
                "tier": entry.get("tier"),
                "rank": entry.get("rank"),
                "leaguePoints": entry.get("leaguePoints", 0),
                "wins": entry.get("wins", 0),
                "losses": entry.get("losses", 0),
            }
    return None


# 소환사 가져오기
def get_summoner(puuid: str) -> dict[str, Any]:
    response = requests.get(
        f"https://kr.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}",
        headers={"X-Riot-Token": RIOT_API_KEY},
        timeout=30,
    )
    if response.status_code != 200:
        return {}
    return response.json()


# 최근 경기 검색
def get_recent_match_ids(puuid: str, count: int = 10):

    url = f"https://asia.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids"

    headers = {"X-Riot-Token": RIOT_API_KEY}

    params = {"start": 0, "count": count, "queue": 420}

    response = requests.get(url, headers=headers, params=params, timeout=30)

    if response.status_code != 200:
        return []

    return response.json()


# 경기 detail 가져오기
def get_match_detail(match_id: str):

    url = f"https://asia.api.riotgames.com/lol/match/v5/matches/{match_id}"

    headers = {"X-Riot-Token": RIOT_API_KEY}

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        return None

    return response.json()


def extract_player_from_match(match_detail: dict, puuid: str):

    if not match_detail:
        return None

    participants = match_detail.get("info", {}).get("participants", [])

    for participant in participants:
        if participant.get("puuid") == puuid:
            return participant

    return None


def build_player_features(match_detail: dict, puuid: str):

    player = extract_player_from_match(match_detail, puuid)

    if player is None:
        return None

    info = match_detail["info"]
    team_id = player["teamId"]

    team_players = [p for p in info["participants"] if p["teamId"] == team_id]

    team_total_kills = sum(p["kills"] for p in team_players)

    team_total_gold = sum(p["goldEarned"] for p in team_players)

    team_total_damage = sum(p["totalDamageDealtToChampions"] for p in team_players)

    team_total_damage_taken = sum(p["totalDamageTaken"] for p in team_players)

    duration_seconds = info["gameDuration"]
    duration_minutes = duration_seconds / 60

    total_cs = player["totalMinionsKilled"] + player["neutralMinionsKilled"]

    game_start_timestamp = info.get("gameStartTimestamp")
    game_start_datetime = None
    if game_start_timestamp:
        game_start_datetime = datetime.fromtimestamp(
            game_start_timestamp / 1000, tz=UTC
        ).isoformat()

    perks = player.get("perks") or {}
    styles = perks.get("styles") or []
    primary_style = styles[0] if styles else {}
    secondary_style = styles[1] if len(styles) > 1 else {}
    selections = primary_style.get("selections") or []
    kills = player["kills"]
    deaths = player["deaths"]
    assists = player["assists"]
    kill_participation = (kills + assists) / team_total_kills if team_total_kills > 0 else 0

    return {
        "match_id": match_detail["metadata"]["matchId"],
        "game_duration_seconds": duration_seconds,
        "game_start_datetime": game_start_datetime,
        "champion_id": player["championId"],
        "champion_name": player["championName"],
        "team_position": player["teamPosition"],
        "win": player["win"],
        "item0": player.get("item0", 0),
        "item1": player.get("item1", 0),
        "item2": player.get("item2", 0),
        "item3": player.get("item3", 0),
        "item4": player.get("item4", 0),
        "item5": player.get("item5", 0),
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "kda": (player["kills"] + player["assists"]) / max(player["deaths"], 1),
        "total_cs": total_cs,
        "cs_per_min": total_cs / duration_minutes,
        "gold_earned": player["goldEarned"],
        "gold_per_min": player["goldEarned"] / duration_minutes,
        "total_damage_dealt_to_champions": player["totalDamageDealtToChampions"],
        "damage_per_min": player["totalDamageDealtToChampions"] / duration_minutes,
        "total_damage_taken": player["totalDamageTaken"],
        "damage_taken_per_min": player["totalDamageTaken"] / duration_minutes,
        "vision_score": player["visionScore"],
        "vision_score_per_min": player["visionScore"] / duration_minutes,
        "wards_placed": player["wardsPlaced"],
        "wards_killed": player["wardsKilled"],
        "vision_wards_bought_in_game": player["visionWardsBoughtInGame"],
        "damage_dealt_to_objectives": player["damageDealtToObjectives"],
        "objective_damage_per_min": player["damageDealtToObjectives"] / duration_minutes,
        "kill_participation": (kill_participation),
        "gold_share": (player["goldEarned"] / team_total_gold if team_total_gold > 0 else 0),
        "damage_share": (
            player["totalDamageDealtToChampions"] / team_total_damage
            if team_total_damage > 0
            else 0
        ),
        "damage_taken_share": (
            player["totalDamageTaken"] / team_total_damage_taken
            if team_total_damage_taken > 0
            else 0
        ),
        "damage_efficiency": (
            player["totalDamageDealtToChampions"] / max(player["totalDamageTaken"], 1)
        ),
        "wards_placed_per_min": player["wardsPlaced"] / duration_minutes,
        "wards_killed_per_min": player["wardsKilled"] / duration_minutes,
        "vision_wards_bought_per_min": (player["visionWardsBoughtInGame"] / duration_minutes),
        # Camel-case aliases consumed by the bundled dashboard. Keeping the
        # original snake-case fields preserves the Databricks notebook input.
        "championName": player["championName"],
        "championLevel": player.get("champLevel", 0),
        "summoner1Id": player.get("summoner1Id", 0),
        "summoner2Id": player.get("summoner2Id", 0),
        "items": [player.get(f"item{slot}", 0) for slot in range(7)],
        "primaryRuneId": selections[0].get("perk", 0) if selections else 0,
        "subRuneStyle": secondary_style.get("style", 0),
        "totalCS": total_cs,
        "csPerMin": total_cs / duration_minutes,
        "goldPerMin": player["goldEarned"] / duration_minutes,
        "damageToChampions": player["totalDamageDealtToChampions"],
        "visionScore": player["visionScore"],
        "killParticipation": kill_participation * 100,
        "gameMinutes": duration_minutes,
        "teamPosition": player["teamPosition"],
    }


def get_recent_player_features(match_ids: list, puuid: str):

    if not match_ids:
        return []

    def load_features(match_id: str) -> dict[str, Any] | None:
        try:
            match_detail = get_match_detail(match_id)
            if match_detail is None:
                return None
            features = build_player_features(match_detail, puuid)
            if features and features["game_duration_seconds"] >= 300:
                return features
        except (KeyError, TypeError, ValueError, requests.RequestException):
            return None
        return None

    # Riot match details are independent requests. A small worker pool removes
    # most of the sequential network wait while staying below typical rate
    # limits. executor.map preserves the newest-to-oldest match order.
    worker_count = min(5, len(match_ids))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        recent_games = [
            features for features in executor.map(load_features, match_ids) if features is not None
        ]

    return recent_games


def run_databricks_coaching(player_id: str, recent_games: list, tier: str):
    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}

    url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"

    compact_games = [
        {
            key: game.get(key)
            for key in (
                "match_id",
                "champion_id",
                "champion_name",
                "team_position",
                "win",
                "game_start_datetime",
                "item0",
                "item1",
                "item2",
                "item3",
                "item4",
                "item5",
                "deaths",
                "kda",
                "kill_participation",
                "cs_per_min",
                "gold_per_min",
                "gold_share",
                "damage_per_min",
                "damage_taken_per_min",
                "damage_share",
                "damage_taken_share",
                "damage_efficiency",
                "vision_score_per_min",
                "wards_placed_per_min",
                "wards_killed_per_min",
                "vision_wards_bought_per_min",
                "objective_damage_per_min",
            )
        }
        for game in recent_games
    ]

    recent_games_json = json.dumps(compact_games, ensure_ascii=False, separators=(",", ":"))

    payload_bytes = len(recent_games_json.encode("utf-8"))
    if payload_bytes >= 10000:
        return {
            "status": "error",
            "error": "recent_games_json_too_large",
            "payload_bytes": payload_bytes,
        }

    payload = {
        "job_id": int(DATABRICKS_JOB_ID),
        "notebook_params": {
            "player_id": player_id,
            "recent_games_json": recent_games_json,
            "tier": tier or "UNKNOWN",
        },
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    if response.status_code != 200:
        return None

    return response.json()


@app.get("/api")
def root():
    return {"status": "success", "message": "LoL AI Coaching API"}


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/player-analysis")
def player_analysis(request: PlayerAnalysisRequest):
    """Adapter between the bundled dashboard and the Riot API helpers."""
    if not RIOT_API_KEY:
        raise HTTPException(
            status_code=503,
            detail={"error_code": "RIOT_API_KEY_MISSING"},
        )

    try:
        response = requests.get(
            "https://asia.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{request.riot_id}/{request.tag_line}",
            headers={"X-Riot-Token": RIOT_API_KEY},
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=502,
            detail={"error_code": "RIOT_API_UNAVAILABLE"},
        ) from exc

    error_codes = {
        401: "RIOT_API_KEY_EXPIRED_OR_FORBIDDEN",
        403: "RIOT_API_KEY_EXPIRED_OR_FORBIDDEN",
        404: "RIOT_ACCOUNT_NOT_FOUND",
        429: "RIOT_RATE_LIMITED",
    }
    if response.status_code != 200:
        raise HTTPException(
            status_code=response.status_code if response.status_code in error_codes else 502,
            detail={"error_code": error_codes.get(response.status_code, "RIOT_API_ERROR")},
        )

    account = response.json()
    puuid = account.get("puuid")
    if not puuid:
        raise HTTPException(
            status_code=404,
            detail={"error_code": "RIOT_ACCOUNT_NOT_FOUND"},
        )

    # These three Riot endpoints are independent, so fetch them concurrently.
    with ThreadPoolExecutor(max_workers=3) as executor:
        summoner_future = executor.submit(get_summoner, puuid)
        rank_future = executor.submit(get_solo_rank, puuid)
        match_ids_future = executor.submit(get_recent_match_ids, puuid, request.match_count)
        summoner = summoner_future.result()
        rank = rank_future.result()
        match_ids = match_ids_future.result()
    matches = get_recent_player_features(match_ids, puuid)

    _cache_set(
        _PLAYER_CONTEXT_CACHE,
        _player_cache_key(request.riot_id, request.tag_line),
        {
            "puuid": puuid,
            "player_id": puuid_to_player_id(puuid),
            "tier": (rank or {}).get("tier") or "UNKNOWN",
            "recent_games": matches,
        },
        ANALYSIS_CACHE_TTL_SECONDS,
    )

    return {
        "profile": {
            "profileIconId": summoner.get("profileIconId", 0),
            "summonerLevel": summoner.get("summonerLevel", 0),
        },
        "rank": rank,
        "summary": build_player_summary(matches),
        "matches": matches,
    }


@app.post("/api/coaching-report")
def coaching_report(request: CoachingReportRequest):
    missing_config = missing_databricks_config()
    if missing_config:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "DATABRICKS_CONFIG_MISSING",
                "missing": missing_config,
            },
        )

    result = coach_by_riot_id(request.riot_id, request.tag_line)
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail=result)

    return {
        "status": "success",
        "cached": bool(result.get("cached")),
        "run_id": result.get("run_id"),
        "match_count": result.get("match_count", 0),
        "report": result.get("result") or {},
    }


@app.post("/api/chat")
def chat(request: CoachRequest):
    missing_config = missing_databricks_config()
    if missing_config:
        raise HTTPException(
            status_code=503,
            detail={
                "error_code": "DATABRICKS_CONFIG_MISSING",
                "missing": missing_config,
            },
        )
    if not request.riot_id or not request.tag_line:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "RIOT_ID_REQUIRED"},
        )

    result = coach_by_riot_id(request.riot_id, request.tag_line)
    if result.get("status") != "success":
        raise HTTPException(status_code=502, detail=result)

    report = result.get("result") or {}
    coaching = report.get("coaching") or {}
    answer_parts = [
        coaching.get("play_summary"),
        coaching.get("playstyle_summary"),
        *(coaching.get("coaching") or []),
        coaching.get("overall_comment"),
    ]
    answer = "\n".join(str(part) for part in answer_parts if part)
    return {
        "answer": answer or "코칭 결과를 생성하지 못했습니다.",
        "route": "personal_analysis",
        "citations": [],
        "report": report,
    }


@app.get("/riot/account/{game_name}/{tag_line}")
def get_riot_account(game_name: str, tag_line: str):

    url = (
        f"https://asia.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    )

    headers = {"X-Riot-Token": RIOT_API_KEY}

    response = requests.get(url, headers=headers, timeout=30)

    if response.status_code != 200:
        return {
            "status": "error",
            "status_code": response.status_code,
            "message": "Riot 계정을 조회하지 못했습니다.",
        }

    account = response.json()

    puuid = account["puuid"]
    player_id = puuid_to_player_id(puuid)
    tier = get_solo_tier(puuid)

    # 최근 솔로랭크 10경기 ID 조회
    match_ids = get_recent_match_ids(puuid, count=10)
    recent_games = get_recent_player_features(match_ids, puuid)

    return {
        "status": "success",
        "game_name": account["gameName"],
        "tag_line": account["tagLine"],
        "puuid": puuid,
        "player_id": player_id,
        "tier": tier,
        "match_count": len(recent_games),
        "recent_games": recent_games,
    }


@app.post("/api/rag")
@app.post("/lol/rag")
def rag_search(request: LolRagRequest):
    direct_riot_id = parse_riot_id_query(request.question) if request.mode is None else None
    if direct_riot_id:
        started = time.perf_counter()
        game_name, tag_line = direct_riot_id
        analysis = player_analysis(
            PlayerAnalysisRequest(
                riot_id=game_name,
                tag_line=tag_line,
                match_count=request.match_count,
            )
        )
        return safe_rag_response(
            simple_player_statistics_response(
                game_name,
                tag_line,
                analysis,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        )

    embedded = parse_embedded_riot_question(request.question) if request.mode is None else None
    requested_game_name = request.game_name or request.riot_id
    if request.mode == "official":
        game_name = ""
        tag_line = ""
        effective_question = request.question.strip()
    elif request.mode == "personal":
        game_name = (requested_game_name or "").strip()
        tag_line = (request.tag_line or "").strip().removeprefix("#")
        effective_question = request.question.strip()
        missing = [
            field
            for field, value in (("game_name", game_name), ("tag_line", tag_line))
            if not value
        ]
        if missing:
            raise HTTPException(
                status_code=400,
                detail={
                    "error_code": "RIOT_ID_REQUIRED",
                    "message": "개인 경기 질문에는 소환사명과 태그가 필요합니다.",
                    "missing": missing,
                },
            )
    elif embedded:
        game_name, tag_line, effective_question = embedded
    else:
        game_name = (requested_game_name or "").strip()
        tag_line = (request.tag_line or "").strip().removeprefix("#")
        effective_question = request.question.strip()

    if (
        request.mode != "official"
        and needs_riot_id_context(effective_question)
        and not (game_name and tag_line)
    ):
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": "RIOT_ID_REQUIRED",
                "message": (
                    "먼저 게임이름#태그를 입력하거나 질문 앞에 게임이름/#태그/를 붙여 주세요."
                ),
            },
        )

    result = answer_question(
        question=effective_question,
        riot_id=game_name or None,
        tag_line=tag_line or None,
        match_count=request.match_count,
        dependencies=get_rag_dependencies(),
        mode=request.mode,
    )
    if game_name and tag_line:
        result["subject"] = {"game_name": game_name, "tag_line": tag_line}
    return safe_rag_response(result)


@app.get("/coach/riot/{game_name}/{tag_line}")
def coach_by_riot_id(game_name: str, tag_line: str):
    player_key = _player_cache_key(game_name, tag_line)
    cached_context = _cache_get(_PLAYER_CONTEXT_CACHE, player_key)

    if cached_context:
        puuid = cached_context["puuid"]
        player_id = cached_context["player_id"]
        tier = cached_context["tier"]
        recent_games = cached_context["recent_games"]
        account = {"gameName": game_name, "tagLine": tag_line}
    else:
        # Fallback for direct API calls that did not open player analysis first.
        url = (
            "https://asia.api.riotgames.com"
            f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        )
        riot_headers = {"X-Riot-Token": RIOT_API_KEY}

        response = requests.get(url, headers=riot_headers, timeout=30)
        if response.status_code != 200:
            return {
                "status": "error",
                "message": "Riot 계정을 조회하지 못했습니다.",
                "status_code": response.status_code,
            }

        account = response.json()
        puuid = account["puuid"]
        player_id = puuid_to_player_id(puuid)
        tier = get_solo_tier(puuid)
        match_ids = get_recent_match_ids(puuid, count=10)
        recent_games = get_recent_player_features(match_ids, puuid)
        _cache_set(
            _PLAYER_CONTEXT_CACHE,
            player_key,
            {
                "puuid": puuid,
                "player_id": player_id,
                "tier": tier,
                "recent_games": recent_games,
            },
            ANALYSIS_CACHE_TTL_SECONDS,
        )

    if not recent_games:
        return {"status": "error", "message": "분석할 수 있는 최근 솔로랭크 경기가 없습니다."}

    match_signature = hashlib.sha256(
        json.dumps(
            [game.get("match_id") for game in recent_games],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    coach_cache_key = player_id, match_signature
    cached_result = _cache_get(_COACH_RESULT_CACHE, coach_cache_key)
    if cached_result:
        return {**cached_result, "cached": True}

    # 3. Databricks Job 실행
    job_response = run_databricks_coaching(player_id, recent_games, tier)
    if job_response is None:
        return {"status": "error", "message": "Databricks Job 실행 요청에 실패했습니다."}

    if job_response.get("status") == "error":
        return {
            "status": "error",
            "message": "Databricks Job 파라미터 생성에 실패했습니다.",
            "detail": job_response,
        }

    run_id = job_response["run_id"]
    db_headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}

    # 4. Job 완료까지 대기 (최대 15분)
    deadline = time.time() + 900
    run_info = None

    while time.time() < deadline:
        status_response = requests.get(
            f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get",
            headers=db_headers,
            params={"run_id": run_id},
            timeout=30,
        )

        if status_response.status_code != 200:
            return {
                "status": "error",
                "message": "Databricks Job 상태를 확인하지 못했습니다.",
                "run_id": run_id,
                "detail": status_response.text,
            }

        run_info = status_response.json()
        state = run_info.get("state", {})
        life_cycle_state = state.get("life_cycle_state")

        if life_cycle_state == "TERMINATED":
            if state.get("result_state") != "SUCCESS":
                return {
                    "status": "error",
                    "message": "Databricks Job이 실패했습니다.",
                    "run_id": run_id,
                    "result_state": state.get("result_state"),
                    "state_message": state.get("state_message"),
                }
            break

        if life_cycle_state in {"SKIPPED", "INTERNAL_ERROR"}:
            return {
                "status": "error",
                "message": "Databricks Job 실행에 실패했습니다.",
                "run_id": run_id,
                "life_cycle_state": life_cycle_state,
            }

        time.sleep(3)
    else:
        return {
            "status": "error",
            "message": "Databricks Job 완료 대기 시간이 초과되었습니다.",
            "run_id": run_id,
        }

    # 5. Notebook의 dbutils.notebook.exit() 결과 가져오기
    tasks = run_info.get("tasks", [])
    output_run_id = tasks[0]["run_id"] if tasks else run_id

    output_response = requests.get(
        f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output",
        headers=db_headers,
        params={"run_id": output_run_id},
        timeout=30,
    )

    if output_response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 결과를 가져오지 못했습니다.",
            "run_id": run_id,
            "detail": output_response.text,
        }

    output_data = output_response.json()
    result_text = output_data.get("notebook_output", {}).get("result")

    if not result_text:
        return {"status": "error", "message": "Notebook 결과가 비어 있습니다.", "run_id": run_id}

    try:
        coaching_result = json.loads(result_text)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Notebook 결과 JSON을 해석하지 못했습니다.",
            "run_id": run_id,
            "raw_result": result_text,
        }

    # Notebook 자체가 validation/error 상태를 반환했다면 성공으로 감싸지 않음
    if coaching_result.get("status") != "ok":
        return {
            "status": "error",
            "game_name": account["gameName"],
            "tag_line": account["tagLine"],
            "player_id": player_id,
            "tier": tier,
            "match_count": len(recent_games),
            "run_id": run_id,
            "result": coaching_result,
        }

    # 6. 최종 AI 코칭 결과를 FastAPI 응답으로 반환
    result = {
        "status": "success",
        "game_name": account["gameName"],
        "tag_line": account["tagLine"],
        "player_id": player_id,
        "tier": tier,
        "match_count": len(recent_games),
        "run_id": run_id,
        "result": coaching_result,
    }
    _cache_set(
        _COACH_RESULT_CACHE,
        coach_cache_key,
        result,
        COACH_CACHE_TTL_SECONDS,
    )
    return result


@app.get("/coach/{player_id}")
def get_coaching(player_id: str):

    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}", "Content-Type": "application/json"}

    # 1. Databricks Job 실행
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"

    payload = {"job_id": int(DATABRICKS_JOB_ID), "notebook_params": {"player_id": player_id}}

    response = requests.post(url, headers=headers, json=payload, timeout=30)

    if response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 실행 요청에 실패했습니다.",
            "detail": response.json(),
        }

    run_id = response.json()["run_id"]

    # 2. Job 완료 여부 확인
    while True:
        status_response = requests.get(
            f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get",
            headers=headers,
            params={"run_id": run_id},
            timeout=30,
        )

        if status_response.status_code != 200:
            return {
                "status": "error",
                "message": "Databricks Job 상태를 확인하지 못했습니다.",
                "run_id": run_id,
                "detail": status_response.json(),
            }

        run_info = status_response.json()

        life_cycle_state = run_info["state"]["life_cycle_state"]

        if life_cycle_state == "TERMINATED":
            break

        if life_cycle_state in ["SKIPPED", "INTERNAL_ERROR"]:
            return {
                "status": "error",
                "message": "Databricks Job 실행에 실패했습니다.",
                "run_id": run_id,
            }

        time.sleep(3)

    # 3. ai_coaching 태스크의 실제 run_id 가져오기
    task_run_id = run_info["tasks"][0]["run_id"]

    # 4. 태스크의 Notebook 결과 가져오기
    output_response = requests.get(
        f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output",
        headers=headers,
        params={"run_id": task_run_id},
        timeout=30,
    )

    if output_response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 결과를 가져오지 못했습니다.",
            "run_id": run_id,
            "task_run_id": task_run_id,
            "detail": output_response.json(),
        }

    output_data = output_response.json()

    # dbutils.notebook.exit()으로 반환한 JSON 문자열
    result_text = output_data["notebook_output"]["result"]

    # JSON 문자열 → Python 객체
    coaching_result = json.loads(result_text)

    return coaching_result


@app.get("/databricks/config-check")
def databricks_config_check():
    return {
        "host_loaded": bool(DATABRICKS_HOST),
        "token_loaded": bool(DATABRICKS_TOKEN),
        "job_id_loaded": bool(DATABRICKS_JOB_ID),
        "riot_api_key_loaded": bool(RIOT_API_KEY),
    }


@app.get("/databricks/test")
def test_databricks_connection():

    url = f"{DATABRICKS_HOST}/api/2.1/jobs/list"

    headers = {"Authorization": f"Bearer {DATABRICKS_TOKEN}"}

    response = requests.get(url, headers=headers, timeout=30)

    return {"status_code": response.status_code, "connected": response.status_code == 200}


if FRONTEND_DIR.is_dir():

    @app.get("/", include_in_schema=False)
    def frontend_entry() -> FileResponse:
        return FileResponse(FRONTEND_DIR / "index.html")

    # Keep this last so API routes take precedence over the root mount.
    app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)
