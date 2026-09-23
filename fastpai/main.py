import hashlib
import json
import os
import time
from datetime import UTC, datetime

import requests
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

DATABRICKS_JOB_ID = os.getenv("DATABRICKS_JOB_ID")
DATABRICKS_HOST = os.getenv("DATABRICKS_HOST")
DATABRICKS_TOKEN = os.getenv("DATABRICKS_TOKEN")
RIOT_API_KEY = os.getenv("RIOT_API_KEY")

app = FastAPI()

def puuid_to_player_id(puuid: str) -> str:
    return hashlib.sha256(
        puuid.encode("utf-8")
    ).hexdigest()[:24]


def get_solo_tier(puuid: str) -> str:
    """Return current solo-queue tier or UNKNOWN when unavailable."""
    url = (
        "https://kr.api.riotgames.com"
        f"/lol/league/v4/entries/by-puuid/{puuid}"
    )
    headers = {"X-Riot-Token": RIOT_API_KEY}

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=30
        )
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

def get_recent_match_ids(puuid: str, count: int = 10):

    url = (
        "https://asia.api.riotgames.com"
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids"
    )

    headers = {
        "X-Riot-Token": RIOT_API_KEY
    }

    params = {
        "start": 0,
        "count": count,
        "queue": 420
    }

    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30
    )

    if response.status_code != 200:
        return []

    return response.json()

def get_match_detail(match_id: str):

    url = (
        "https://asia.api.riotgames.com"
        f"/lol/match/v5/matches/{match_id}"
    )

    headers = {
        "X-Riot-Token": RIOT_API_KEY
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

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

    player = extract_player_from_match(
        match_detail,
        puuid
    )

    if player is None:
        return None

    info = match_detail["info"]
    team_id = player["teamId"]

    team_players = [
        p for p in info["participants"]
        if p["teamId"] == team_id
    ]

    team_total_kills = sum(
        p["kills"] for p in team_players
    )

    team_total_gold = sum(
        p["goldEarned"] for p in team_players
    )

    team_total_damage = sum(
        p["totalDamageDealtToChampions"]
        for p in team_players
    )

    team_total_damage_taken = sum(
        p["totalDamageTaken"]
        for p in team_players
    )

    duration_seconds = info["gameDuration"]
    duration_minutes = duration_seconds / 60

    total_cs = (
        player["totalMinionsKilled"]
        + player["neutralMinionsKilled"]
    )

    game_start_timestamp = info.get("gameStartTimestamp")
    game_start_datetime = None
    if game_start_timestamp:
        game_start_datetime = datetime.fromtimestamp(
            game_start_timestamp / 1000,
            tz=UTC
        ).isoformat()

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
        "kills": player["kills"],
        "deaths": player["deaths"],
        "assists": player["assists"],
        "kda": (
            player["kills"] + player["assists"]
        ) / max(player["deaths"], 1),
        "total_cs": total_cs,
        "cs_per_min": total_cs / duration_minutes,
        "gold_earned": player["goldEarned"],
        "gold_per_min": player["goldEarned"] / duration_minutes,
        "total_damage_dealt_to_champions":
            player["totalDamageDealtToChampions"],
        "damage_per_min":
            player["totalDamageDealtToChampions"] / duration_minutes,
        "total_damage_taken":
            player["totalDamageTaken"],
        "damage_taken_per_min":
            player["totalDamageTaken"] / duration_minutes,
        "vision_score": player["visionScore"],
        "vision_score_per_min":
            player["visionScore"] / duration_minutes,
        "wards_placed": player["wardsPlaced"],
        "wards_killed": player["wardsKilled"],
        "vision_wards_bought_in_game":
            player["visionWardsBoughtInGame"],
        "damage_dealt_to_objectives":
            player["damageDealtToObjectives"],
        "objective_damage_per_min":
            player["damageDealtToObjectives"] / duration_minutes,
        "kill_participation": (
            (player["kills"] + player["assists"])
            / team_total_kills
            if team_total_kills > 0
            else 0
        ),

        "gold_share": (
            player["goldEarned"] / team_total_gold
            if team_total_gold > 0
            else 0
        ),

        "damage_share": (
            player["totalDamageDealtToChampions"]
            / team_total_damage
            if team_total_damage > 0
            else 0
        ),
        "damage_taken_share": (
            player["totalDamageTaken"] / team_total_damage_taken
            if team_total_damage_taken > 0
            else 0
        ),
        "damage_efficiency": (
            player["totalDamageDealtToChampions"]
            / max(player["totalDamageTaken"], 1)
        ),
        "wards_placed_per_min": player["wardsPlaced"] / duration_minutes,
        "wards_killed_per_min": player["wardsKilled"] / duration_minutes,
        "vision_wards_bought_per_min": (
            player["visionWardsBoughtInGame"] / duration_minutes
        ),
    }

def get_recent_player_features(
    match_ids: list,
    puuid: str
):

    recent_games = []

    for match_id in match_ids:

        match_detail = get_match_detail(match_id)

        if match_detail is None:
            continue

        features = build_player_features(
            match_detail,
            puuid
        )

        if features is not None:
            # 5분 미만의 비정상적으로 짧은 경기 제외
            if features["game_duration_seconds"] < 300:
                continue

            recent_games.append(features)

    return recent_games

def run_databricks_coaching(
    player_id: str,
    recent_games: list,
    tier: str
):
    headers = {
        "Authorization": f"Bearer {DATABRICKS_TOKEN}",
        "Content-Type": "application/json"
    }

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

    recent_games_json = json.dumps(
        compact_games,
        ensure_ascii=False,
        separators=(",", ":")
    )

    payload_bytes = len(recent_games_json.encode("utf-8"))
    if payload_bytes >= 10000:
        return {
            "status": "error",
            "error": "recent_games_json_too_large",
            "payload_bytes": payload_bytes
        }

    payload = {
        "job_id": int(DATABRICKS_JOB_ID),
        "notebook_params": {
            "player_id": player_id,
            "recent_games_json": recent_games_json,
            "tier": tier or "UNKNOWN"
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        return None

    return response.json()


@app.get("/")
def root():
    return {
        "status": "success",
        "message": "LoL AI Coaching API"
    }

@app.get("/riot/account/{game_name}/{tag_line}")
def get_riot_account(game_name: str, tag_line: str):

    url = (
        f"https://asia.api.riotgames.com"
        f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    )

    headers = {
        "X-Riot-Token": RIOT_API_KEY
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    if response.status_code != 200:
        return {
            "status": "error",
            "status_code": response.status_code,
            "message": "Riot 계정을 조회하지 못했습니다."
        }

    account = response.json()

    puuid = account["puuid"]
    player_id = puuid_to_player_id(puuid)
    tier = get_solo_tier(puuid)

    # 최근 솔로랭크 10경기 ID 조회
    match_ids = get_recent_match_ids(
        puuid,
        count=10
    )
    recent_games = get_recent_player_features(
        match_ids,
        puuid
    )

    return {
        "status": "success",
        "game_name": account["gameName"],
        "tag_line": account["tagLine"],
        "puuid": puuid,
        "player_id": player_id,
        "tier": tier,
        "match_count": len(recent_games),
        "recent_games": recent_games
    }

@app.get("/coach/riot/{game_name}/{tag_line}")
def coach_by_riot_id(game_name: str, tag_line: str):

    # 1. Riot ID → PUUID
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
            "status_code": response.status_code
        }

    account = response.json()
    puuid = account["puuid"]
    player_id = puuid_to_player_id(puuid)
    tier = get_solo_tier(puuid)

    # 2. 최근 솔로랭크 경기 → Feature 생성
    match_ids = get_recent_match_ids(puuid, count=10)
    recent_games = get_recent_player_features(match_ids, puuid)

    if not recent_games:
        return {
            "status": "error",
            "message": "분석할 수 있는 최근 솔로랭크 경기가 없습니다."
        }

    # 3. Databricks Job 실행
    job_response = run_databricks_coaching(player_id, recent_games, tier)
    if job_response is None:
        return {
            "status": "error",
            "message": "Databricks Job 실행 요청에 실패했습니다."
        }

    if job_response.get("status") == "error":
        return {
            "status": "error",
            "message": "Databricks Job 파라미터 생성에 실패했습니다.",
            "detail": job_response
        }

    run_id = job_response["run_id"]
    db_headers = {
        "Authorization": f"Bearer {DATABRICKS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 4. Job 완료까지 대기 (최대 15분)
    deadline = time.time() + 900
    run_info = None

    while time.time() < deadline:
        status_response = requests.get(
            f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get",
            headers=db_headers,
            params={"run_id": run_id},
            timeout=30
        )

        if status_response.status_code != 200:
            return {
                "status": "error",
                "message": "Databricks Job 상태를 확인하지 못했습니다.",
                "run_id": run_id,
                "detail": status_response.text
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
                    "state_message": state.get("state_message")
                }
            break

        if life_cycle_state in {"SKIPPED", "INTERNAL_ERROR"}:
            return {
                "status": "error",
                "message": "Databricks Job 실행에 실패했습니다.",
                "run_id": run_id,
                "life_cycle_state": life_cycle_state
            }

        time.sleep(3)
    else:
        return {
            "status": "error",
            "message": "Databricks Job 완료 대기 시간이 초과되었습니다.",
            "run_id": run_id
        }

    # 5. Notebook의 dbutils.notebook.exit() 결과 가져오기
    tasks = run_info.get("tasks", [])
    output_run_id = tasks[0]["run_id"] if tasks else run_id

    output_response = requests.get(
        f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output",
        headers=db_headers,
        params={"run_id": output_run_id},
        timeout=30
    )

    if output_response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 결과를 가져오지 못했습니다.",
            "run_id": run_id,
            "detail": output_response.text
        }

    output_data = output_response.json()
    result_text = output_data.get("notebook_output", {}).get("result")

    if not result_text:
        return {
            "status": "error",
            "message": "Notebook 결과가 비어 있습니다.",
            "run_id": run_id
        }

    try:
        coaching_result = json.loads(result_text)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "message": "Notebook 결과 JSON을 해석하지 못했습니다.",
            "run_id": run_id,
            "raw_result": result_text
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
            "result": coaching_result
        }

    # 6. 최종 AI 코칭 결과를 FastAPI 응답으로 반환
    return {
        "status": "success",
        "game_name": account["gameName"],
        "tag_line": account["tagLine"],
        "player_id": player_id,
        "tier": tier,
        "match_count": len(recent_games),
        "run_id": run_id,
        "result": coaching_result
    }

@app.get("/coach/{player_id}")
def get_coaching(player_id: str):

    headers = {
        "Authorization": f"Bearer {DATABRICKS_TOKEN}",
        "Content-Type": "application/json"
    }

    # 1. Databricks Job 실행
    url = f"{DATABRICKS_HOST}/api/2.1/jobs/run-now"

    payload = {
        "job_id": int(DATABRICKS_JOB_ID),
        "notebook_params": {
            "player_id": player_id
        }
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    if response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 실행 요청에 실패했습니다.",
            "detail": response.json()
        }

    run_id = response.json()["run_id"]

    # 2. Job 완료 여부 확인
    while True:

        status_response = requests.get(
            f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get",
            headers=headers,
            params={"run_id": run_id},
            timeout=30
        )

        if status_response.status_code != 200:
            return {
                "status": "error",
                "message": "Databricks Job 상태를 확인하지 못했습니다.",
                "run_id": run_id,
                "detail": status_response.json()
            }

        run_info = status_response.json()

        life_cycle_state = run_info["state"]["life_cycle_state"]

        if life_cycle_state == "TERMINATED":
            break

        if life_cycle_state in ["SKIPPED", "INTERNAL_ERROR"]:
            return {
                "status": "error",
                "message": "Databricks Job 실행에 실패했습니다.",
                "run_id": run_id
            }

        time.sleep(3)

    # 3. ai_coaching 태스크의 실제 run_id 가져오기
    task_run_id = run_info["tasks"][0]["run_id"]

    # 4. 태스크의 Notebook 결과 가져오기
    output_response = requests.get(
        f"{DATABRICKS_HOST}/api/2.1/jobs/runs/get-output",
        headers=headers,
        params={"run_id": task_run_id},
        timeout=30
    )

    if output_response.status_code != 200:
        return {
            "status": "error",
            "message": "Databricks Job 결과를 가져오지 못했습니다.",
            "run_id": run_id,
            "task_run_id": task_run_id,
            "detail": output_response.json()
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
        "riot_api_key_loaded": bool(RIOT_API_KEY)
    }


@app.get("/databricks/test")
def test_databricks_connection():

    url = f"{DATABRICKS_HOST}/api/2.1/jobs/list"

    headers = {
        "Authorization": f"Bearer {DATABRICKS_TOKEN}"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    return {
        "status_code": response.status_code,
        "connected": response.status_code == 200
    }
