# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 04. ML Training — Play Style Clustering & Performance Profiling
# MAGIC
# MAGIC LoL Insight Coach / 수정본
# MAGIC
# MAGIC 기존 노트북 대비 주요 변경점
# MAGIC
# MAGIC | # | 문제 | 수정 |
# MAGIC |---|---|---|
# MAGIC | 1 | 13개 피처를 한 번에 군집화 → 군집이 "잘함/못함" 등급으로 갈림 | 피처를 **스타일 축 / 성과 축**으로 분리, 스타일 축만 군집화 |
# MAGIC | 2 | 스타일 축도 전반적 성과와 상관 | **행 단위 프로파일 센터링**으로 성과 수준 제거, 형태만 남김 |
# MAGIC | 3 | 포지션 Z-score 후 StandardScaler 이중 적용 | 이중 정규화 제거. `position_feature_stats`가 스케일러 역할 |
# MAGIC | 4 | mean/std 기반 Z + 클리핑 없음 | **median/IQR 로버스트 Z + ±3 클리핑** |
# MAGIC | 5 | silhouette만으로 k 선택 → 항상 k=2 | silhouette + **최소 군집 비중** + **부트스트랩 ARI 안정성** |
# MAGIC | 6 | 전체 통합 K-Means | **포지션별 모델 5개** |
# MAGIC | 7 | 상위 3 / 하위 3 무조건 추출 | **신뢰도 수축 + 임계값 + 계열 중복 제거 + 스타일 보정** |
# MAGIC | 8 | 서빙 경로에 Spark ML 의존 | **순수 Python/NumPy 추론 아티팩트** export |
# MAGIC | 9 | 최근 추세 로직 없음 | 5 vs 5 비교 + 유의미성 임계값 |
# MAGIC | 10 | 평가·재현 근거 없음 | ICC/신뢰도 산출, MLflow 로깅 |
# MAGIC
# MAGIC ### v9 추가 수정
# MAGIC
# MAGIC | # | 문제 | 수정 |
# MAGIC |---|---|---|
# MAGIC | 11 | `HAS_TIER=False` — silver 에 tier 컬럼이 없어 아이언과 다이아가 같은 기준선 | **01 노트북**이 silver 에 `tier`/`tier_source` 를 남기도록 수정 (04 는 그대로) |
# MAGIC | 12 | `finding_tag` 가 전부 `neutral` — 컷 0.30 을 넘는 군집·지표 조합이 없음 | **16-b** 기준선 분포 출력 + **16-c** 컷 권고값 자동 계산 (목표 분류 15~25%, 잡음 바닥 하한) |
# MAGIC | 13 | `objective_damage_per_min` 이 성과 축에서 승패 정보를 그대로 보유 (승패차 +1.05) | **09-c** 승패차 큰 성과 축 자동 탐지 후 `win` 으로 잔차화, `perf_residual` 로 export |
# MAGIC | 14 | 09-b 와 09-c 가 같은 회귀를 각자 구현 | **09-a** 공통 잔차화 함수로 통합 |
# MAGIC | 15 | 추론 `_assign_style` 이 09-b 잔차화를 적용하지 않아 학습과 다른 좌표계에서 거리 계산 | `PlayStyleAnalyzer.style_vector` 신설, explain_v2 는 이를 위임 |

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. 설정

# COMMAND ----------

# ============================================================
# 00. 설정
# ============================================================
BASE = "abfss://lol-data@5dt2ndteam3.dfs.core.windows.net/"

PATH_SILVER     = BASE + "silver/match_participants"
PATH_MODEL_FEAT = BASE + "gold/model_features"
PATH_POS_STATS  = BASE + "gold/position_feature_stats"
PATH_CLUSTERS   = BASE + "gold/play_style_clusters"
PATH_STYLE_DEF  = BASE + "gold/play_style_definitions"
PATH_SERVING    = BASE + "models/serving/style_model.json"

BRONZE_TABLE = "lol_insight.bronze.match_participants"

# objective_damage_per_min 제외 (v8)
#   승리 경기 중앙값 +0.579 / 패배 -0.47 → 승패차 +1.05.
#   이겨야 억제기·넥서스를 칠 기회가 생기므로 스타일이 아니라 결과 지표다.
#   이 축이 지배하던 군집은 승률 0.82~0.90 으로 몰려 있었다.
#   성과 축·피드백 재료로는 계속 사용한다 (ALL_FEATURES 에는 남음).
STYLE_FEATURES = [
    "kill_participation",
    "damage_share",
    "gold_share",
    "damage_taken_per_min",
    "vision_score_per_min",
]

PERF_FEATURES = [
    "objective_damage_per_min",
    "cs_per_min",
    "gold_per_min",
    "damage_per_min",
    "kda",
    "resource_efficiency",
]

ALL_FEATURES = sorted(set(STYLE_FEATURES + PERF_FEATURES))

PERF_FAMILIES = {
    "growth": ["cs_per_min", "gold_per_min"],
    "combat": ["kda"],
    "damage": ["damage_per_min", "resource_efficiency"],
    "objective": ["objective_damage_per_min"],
}

POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

Z_CLIP             = 3.0
ANALYSIS_GAMES     = 10
MIN_GAMES          = 5
MIN_POSITION_GAMES = 5
MIN_DURATION       = 900

# FastAPI의 현재 10경기 handoff와 calibration을 일치시키기 위해 10으로 고정한다.
# reliability와 threshold 진단은 이 값으로 노트북을 다시 실행해 산출해야 하며,
# 이전 15경기 artifact를 재사용하면 안 된다.
# 임계값은 표본 수가 바뀌면 분포도 바뀌므로 Cell 25 권고로 재확인한다.
STRENGTH_THRESHOLD    = 0.35
IMPROVEMENT_THRESHOLD = 0.30

ALWAYS_SHOW_STRENGTH    = True
ALWAYS_SHOW_IMPROVEMENT = True

# ------------------------------------------------------------
# 신뢰도 하한 (v9)
# ------------------------------------------------------------
# reliability(n) = n*ICC / (1 + (n-1)*ICC) — "ANALYSIS_GAMES 경기 중앙값을 얼마나 믿는가".
# kda 0.452, objective_damage_per_min 0.5 처럼 낮은 지표는 변동의 절반 이상이
# 경기 간 잡음이다. 점수 수축(median × reliability)만으로는 부족하다.
# median 이 크면 수축 후에도 문턱을 넘어 목록에 올라가고, 사용자에게는
# "CS 수급이 부족합니다" 같은 확신에 찬 문장으로 나간다.
# 그래서 수축과 별개로 "강점/개선점 목록에 올릴 자격" 을 따로 건다.
#
# 0.50 이 아니라 0.55 인 이유:
#   비교가 `reliability < MIN_RELIABILITY` 이므로 0.50 으로 두면
#   objective_damage_per_min(정확히 0.500)이 통과해 버린다. 0.4996 이면 빠지고
#   0.5012 면 남는 칼날 위라, 이 축을 확실히 빼려면 0.55 여야 한다.
#
# 이 값을 바꾸기 전에 Cell 07-b 출력(문턱별 생존 지표·계열 수)을 먼저 본다.
MIN_RELIABILITY = 0.55

# "exclude": 미달 지표를 강점/개선점 후보에서 아예 제외
# "flag"   : 목록에는 남기되 low_reliability 표시와 완곡 힌트를 붙인다 (현재 선택)
# 어느 쪽이든 all_scores(디버깅용)에는 전부 남는다.
#
# flag 를 쓰는 이유:
#   PERF_FAMILIES 에는 1개짜리 계열이 둘 있다 — combat=[kda], objective=[objective_damage_per_min].
#   0.55 에서 두 지표가 모두 미달이라, exclude 로 두면 그 계열이 통째로 사라진다.
#   강점/개선점은 계열당 1개씩만 뽑으므로 살아남은 계열 수가 곧 "한 번에 나올 수 있는
#   항목 수" 의 상한이다. exclude 면 계열이 4개 → 2개로 줄어 강점·개선점이 각각 최대
#   2개로 묶이고, 사용자에게 가장 익숙한 KDA·오브젝트 기여도가 영원히 등장하지 않는다.
#
#   flag 는 계열 4개를 유지하면서도 "확신에 찬 문장" 은 막는다.
#     - 항목에 low_reliability=True 가 붙고 prompt_hint 가 완곡 표현으로 바뀐다
#     - priority 가 3 이하로 밀려 신뢰할 수 있는 지표보다 뒤에 온다
#     - has_strengths / has_improvements 가 False 가 되어 프롬프트가 단정 분기를 타지 않는다
#
#   ANALYSIS_GAMES=10 재학습에서는 실제 ICC 결과에 따라 통과 여부가 달라질 수 있다.
#   flag 를 유지하면 넘으면 표시가 안 붙고, 못 넘어도 사라지지 않고 완곡하게 나간다.
#   07-b 재실행 후 실제 reliability 를 보고 exclude 복귀를 판단한다.
LOW_RELIABILITY_MODE = "flag"

TREND_MIN_GAMES = 5
TREND_THRESHOLD = 0.7

# 전체 unstable 비율이 17%였으므로 0.40 유지. 40% 초과 시 0.35 권고를 출력한다.
DOMINANT_SHARE = 0.40
MIXED_SHARE    = 0.30
MIXED_TOP2     = 0.60

# 군집별 성과 중앙값이 이 값을 넘어야 finding_tag 가 neutral 이 아니게 된다.
#   b >  CUT → core_strength / core_weakness
#   b < -CUT → structural / below_style
# 0.30 은 09-b 잔차화를 넣기 전 분포에 맞춘 값이라 지금은 거의 아무도 못 넘는다.
# Cell 16-c 가 실제 분포를 보고 권고값을 계산한다. 그 값을 여기에 붙여 넣는다.
STYLE_BASELINE_CUT = 0.30

# ------------------------------------------------------------
# 09-c. 성과 축 승패 잔차화 (v9)
# ------------------------------------------------------------
# objective_damage_per_min 처럼 "이겨야 올라가는" 성과 축은 z 자체가 승패 정보를
# 들고 있다. 그대로 두면 "이겨서 높다"가 "잘해서 높다"로 포장돼 사용자에게 나간다.
# 승리/패배 median z 차이가 이 값 이상인 축을 자동 탐지해 win 으로 잔차화한다.
PERF_RESIDUAL_CUT = 0.40

# 잔차화 대상을 objective_damage_per_min 하나로 고정한다.
#   자동 탐지(None)로 두면 kda·gold_per_min 처럼 원래 승패와 강하게 붙어 있는 축까지
#   같이 잡힌다. 그것까지 보정하면 "이겨서 좋은 수치" 가 전반적으로 사라지는 대신
#   제품 동작 변화도 커서, 이번에는 문제로 보고된 축만 고친다.
#   09-c 는 탐지 결과와 실제 대상을 함께 출력하므로, 제외된 축이 무엇인지는 매 실행마다
#   확인할 수 있다. 나중에 범위를 넓히려면 None 으로 되돌리면 된다.
PERF_RESIDUAL_FEATURES = ["objective_damage_per_min"]

SEED               = 42
TEST_GAMES         = 10

print("스타일 축:", len(STYLE_FEATURES), "/ 성과 축:", len(PERF_FEATURES))
print("전체 사용 피처:", len(ALL_FEATURES))


# COMMAND ----------

# ============================================================
# 00-b. 설명 모듈 로드
# ============================================================
# cluster_diagnostics.py / playstyle_explain_v2.py / playstyle_analyzer.py 를
# 이 노트북과 "같은 폴더" 에 Import 한다. 이때 형식을 반드시 File 로 고른다.
# Auto-detect 로 올리면 노트북으로 변환되어 import 가 되지 않는다.
#
# 아래는 경로를 자동으로 찾는다. 못 찾으면 어디를 뒤졌는지와 원인을 출력한다.

import os
import sys

REQUIRED_MODULES = ["cluster_diagnostics", "playstyle_explain_v2"]
# playstyle_analyzer 는 04 가 인라인 클래스를 쓰므로 없어도 실행된다.
# 다만 서빙과 같은 코드를 쓰는지 확인하려면 함께 올려 두는 편이 좋다.
OPTIONAL_MODULES = ["playstyle_analyzer"]

# 자동 탐색이 실패할 때만 여기에 폴더 경로를 직접 넣는다. 예:
#   MODULE_DIR = "/Workspace/Users/you@example.com/lol_pipeline"
MODULE_DIR = ""


def _candidate_dirs():
    """모듈이 있을 만한 폴더 후보. 앞쪽이 우선순위가 높다."""
    seen, found = set(), []

    def add(path):
        if path and path not in seen and os.path.isdir(path):
            seen.add(path)
            found.append(path)

    add(MODULE_DIR)
    add(os.getcwd())                       # Databricks 노트북의 작업 폴더 = 노트북 폴더
    try:                                   # 노트북 경로 API (런타임에 따라 없을 수 있다)
        _ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        add(os.path.dirname("/Workspace" + _ctx.notebookPath().get()))
    except Exception:
        pass
    add(os.path.dirname(os.getcwd()))      # 한 단계 위 폴더
    return found


def _has_all(directory, modules):
    return all(os.path.exists(os.path.join(directory, f"{m}.py")) for m in modules)


MODULE_PATH = next((d for d in _candidate_dirs() if _has_all(d, REQUIRED_MODULES)), None)

if MODULE_PATH:
    if MODULE_PATH not in sys.path:
        sys.path.insert(0, MODULE_PATH)
    missing_optional = [m for m in OPTIONAL_MODULES
                        if not os.path.exists(os.path.join(MODULE_PATH, f"{m}.py"))]
    print(f"모듈 경로: {MODULE_PATH}")
    if missing_optional:
        print(f"[참고] 선택 모듈 없음: {missing_optional} — 04 실행에는 지장 없지만 "
              "FastAPI 배포에는 필요하다.")
else:
    print("[실패] cluster_diagnostics.py / playstyle_explain_v2.py 를 찾지 못했습니다.")
    print("       아래에서 실제로 무엇이 올라가 있는지 확인하세요.\n")
    for directory in _candidate_dirs():
        try:
            entries = sorted(os.listdir(directory))
        except Exception as error:
            print(f"  {directory}\n    (읽기 실패: {error})")
            continue
        py_files = [e for e in entries if e.endswith(".py")]
        print(f"  {directory}")
        print(f"    .py 파일: {py_files if py_files else '없음'}")
        similar = [e for e in entries
                   if not e.endswith(".py")
                   and any(key in e for key in ("cluster", "playstyle"))]
        if similar:
            print(f"    확장자 없는 비슷한 이름: {similar}")
            print("      → Auto-detect 로 올려 노트북으로 변환됐을 가능성이 높다.")
    print("\n  확인할 것")
    print("   1. 세 파일을 이 노트북과 같은 폴더에 올렸는가")
    print("   2. Import 형식을 'File' 로 했는가 (Auto-detect 는 노트북으로 바꿔 버린다)")
    print("   3. 파일명이 정확한가 — 다운로드하면 'cluster_diagnostics__1_.py' 처럼 바뀌는 일이 잦다")
    print("   4. 그래도 안 되면 위 MODULE_DIR 에 폴더 경로를 직접 넣고 이 셀을 다시 실행한다")
    raise ModuleNotFoundError(
        "cluster_diagnostics / playstyle_explain_v2 를 찾지 못했습니다. 위 안내를 확인하세요."
    )

%reload_ext autoreload
%autoreload 2

import inspect
from cluster_diagnostics import build_cluster_report, build_explain_artifact
import cluster_diagnostics as _cd
from playstyle_explain_v2 import make_explainable, build_coaching_prompt_v2

# 옛 버전이 캐시에 남아 있으면 진단 결과가 그대로라 혼동하기 쉽다.
assert "exclude_derived" in inspect.signature(_cd.skill_leak_check).parameters, (
    "cluster_diagnostics 가 구버전입니다. 파일을 Overwrite 로 다시 Import 한 뒤 "
    "클러스터를 Detach & re-attach 하세요."
)
print("설명 모듈 로드 완료")

# COMMAND ----------

df_silver = spark.read.format("delta").load(PATH_SILVER)
need = ["match_id","player_id","game_start_datetime","team_position",
        "champion_id","champion_name","patch_version","win",
        "kills","deaths","assists",
        "kda","kill_participation","cs_per_min","gold_per_min",
        "damage_per_min","damage_taken_per_min","vision_score_per_min",
        "objective_damage_per_min","damage_share","gold_share"]
missing = [c for c in need if c not in df_silver.columns]
print("없는 컬럼:", missing)
print("tier 있음:", "tier" in df_silver.columns)

# COMMAND ----------

# MAGIC %md
# MAGIC ### 제외한 피처에 대하여
# MAGIC
# MAGIC `wards_placed`, `wards_killed` 2개는 제외함.
# MAGIC
# MAGIC - 정수 카운트(대부분 0~5)라 경기 간 분산이 매우 커서 현재 분석 창에서도 안정적으로 수렴하기 어려움
# MAGIC - 2가지 요소가  `vision_score_per_min`에 이미 반영되는 구성 요소라 중복
# MAGIC - 기존 노트북에서 개선점 3칸을 전부 이 계열이 차지한 원인
# MAGIC
# MAGIC 아래 셀에서 ICC를 계산하면 이 판단이 데이터로 확인

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. 데이터 로드 · 파생 · 품질 점검

# COMMAND ----------

# ============================================================
# 01. 데이터 로드 (솔로랭크만 + 티어 컬럼 보존)
# ============================================================
from pyspark.sql import functions as F, Window
import pandas as pd
import numpy as np
import json
import math

df_silver = spark.read.format("delta").load(PATH_SILVER)

BASE_COLS = [
    "match_id", "player_id", "game_start_datetime", "team_position",
    "champion_id", "champion_name", "patch_version", "win",
    "kills", "deaths", "assists",
]
RAW_FEATURES = [
    "kda", "kill_participation", "cs_per_min", "gold_per_min",
    "damage_per_min", "damage_taken_per_min", "vision_score_per_min",
    "objective_damage_per_min", "damage_share", "gold_share",
    "vision_wards_bought_in_game",
]

DERIVED = {"resource_efficiency"}
missing_in_raw = set(ALL_FEATURES) - set(RAW_FEATURES) - DERIVED
if missing_in_raw:
    raise ValueError(f"RAW_FEATURES 에 추가하세요: {sorted(missing_in_raw)}")
missing_in_silver = [c for c in RAW_FEATURES if c not in df_silver.columns]
if missing_in_silver:
    raise ValueError(f"silver 에 없는 컬럼: {missing_in_silver}")

# 중요: tier가 silver에 있어도 select에서 빼면 다음 셀에서 찾을 수 없다.
TIER_COLS = ["tier", "tier_source"] if "tier_source" in df_silver.columns else (["tier"] if "tier" in df_silver.columns else [])

df_model = (
    df_silver
    .filter(F.col("queue_id") == 420)
    .select(*BASE_COLS, *TIER_COLS, *RAW_FEATURES)
    .filter(F.col("team_position").isin(POSITIONS))
    .withColumn(
        "resource_efficiency",
        F.when(F.col("gold_share") > 0, F.col("damage_share") / F.col("gold_share")),
    )
)

print("행:", df_model.count())
print("플레이어:", df_model.select("player_id").distinct().count())
print("tier 컬럼 보존:", "tier" in df_model.columns)


# COMMAND ----------

# ============================================================
# 02. 품질 점검 — inf / null / deaths=0
# ============================================================
# kda 가 (k+a)/deaths 로 계산됐다면 deaths=0 인 경기에서 inf 가 됩니다.
# inf 가 하나라도 있으면 stddev 가 NaN 이 되어 Z-score 전체가 무너집니다.

def bad_count(c):
    col = F.col(c).cast("double")
    return F.sum(
        F.when(col.isNull() | F.isnan(col) | (F.abs(col) == float("inf")), 1)
         .otherwise(0)
    ).alias(c)

display(
    df_model.select(
        F.count("*").alias("__rows"),
        F.sum(F.when(F.col("deaths") == 0, 1).otherwise(0)).alias("__deaths_zero"),
        *[bad_count(c) for c in ALL_FEATURES],
    )
)

# COMMAND ----------

# ============================================================
# 03. 이상값 정리
# ============================================================
# inf / NaN 을 null 로 통일하고, 결측이 있는 행을 제외한다.
# kda는 이미 결측치가 없음으로 나옴으로 재계산하지 않는다.
# (0 데스 경기를 버리면 잘한 경기가 통째로 사라지므로 치환이 맞습니다)

for c in ALL_FEATURES:
    col = F.col(c).cast("double")
    df_model = df_model.withColumn(
        c,
        F.when(col.isNull() | F.isnan(col) | (F.abs(col) == float("inf")), None)
         .otherwise(col)
    )

# 피처가 하나라도 비면 Z-score 프로필이 왜곡되므로 해당 행 제외
before = df_model.count()
df_model = df_model.dropna(subset=ALL_FEATURES)
after = df_model.count()
print(f"정리 후 행: {after}  (제외 {before - after}행, {100*(before-after)/before:.2f}%)")

# COMMAND ----------

# ============================================================
# 03-b. 경기 시간 필터 — 조기 항복/탈주 경기 제외
# ============================================================
# 15분 미만 경기는 분당 지표(cs_per_min 등)가 극단적으로 튀고 오브젝트·시야
# 플레이가 나오기 전에 끝나므로, 스타일 군집과 기준 통계를 모두 왜곡합니다.
# 반드시 기준 통계 생성(05) 보다 앞에서 걸러야 합니다.

DUR_COL = "game_duration_seconds"

if DUR_COL in df_silver.columns:
    dur_sdf = df_silver.select("match_id", "player_id", DUR_COL)
else:
    print(f"[알림] silver 에 {DUR_COL} 이 없어 {BRONZE_TABLE} 에서 조인합니다.")
    dur_sdf = spark.table(BRONZE_TABLE).select("match_id", "player_id", DUR_COL)

dur_sdf = dur_sdf.dropDuplicates(["match_id", "player_id"])

before = df_model.count()
df_model = (
    df_model
    .join(dur_sdf, on=["match_id", "player_id"], how="left")
    .filter(F.col(DUR_COL).isNotNull() & (F.col(DUR_COL) >= MIN_DURATION))
)
after = df_model.count()
print(f"경기 시간 필터: {before} → {after} (제외 {before - after}행, {100*(before-after)/before:.2f}%)")

display(df_model.groupBy("team_position").count().orderBy("team_position"))

# COMMAND ----------

# ============================================================
# 04. 티어 버킷
# ============================================================
# 01에서 매치 시드 티어를 같은 경기 10명에게 전파해 저장한다.
# 추론 시 입력 tier도 같은 버킷 규칙을 사용해야 한다.

TIER_BUCKET_MAP = {
    "IRON": "IRON_BRONZE", "BRONZE": "IRON_BRONZE",
    "SILVER": "SILVER", "GOLD": "GOLD", "PLATINUM": "PLATINUM",
    "EMERALD": "EMERALD",
    "DIAMOND": "DIAMOND_PLUS", "MASTER": "DIAMOND_PLUS",
    "GRANDMASTER": "DIAMOND_PLUS", "CHALLENGER": "DIAMOND_PLUS",
}

HAS_TIER = "tier" in df_model.columns and df_model.filter(F.col("tier").isNotNull()).limit(1).count() > 0

if HAS_TIER:
    mapping = F.create_map(*[
        x for tier, bucket in TIER_BUCKET_MAP.items()
        for x in (F.lit(tier), F.lit(bucket))
    ])
    df_model = df_model.withColumn(
        "tier_bucket",
        F.coalesce(mapping[F.upper(F.trim(F.col("tier")))], F.lit("ALL")),
    )
else:
    df_model = df_model.withColumn("tier_bucket", F.lit("ALL"))
    print("[경고] silver에 유효한 tier가 없어 포지션 전체 기준으로 진행합니다.")

GROUP_KEYS = ["team_position", "tier_bucket"]
tier_counts = df_model.groupBy("tier_bucket").count()
tier_total = df_model.count()
tier_known = df_model.filter(F.col("tier_bucket") != "ALL").count()
tier_coverage = tier_known / max(tier_total, 1)

print(f"티어 보정 적용: {HAS_TIER} / 커버리지: {tier_coverage:.1%}")
display(tier_counts.orderBy("tier_bucket"))
display(df_model.groupBy(*GROUP_KEYS).count().orderBy(*GROUP_KEYS))


# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. 기준 통계 — position_feature_stats

# COMMAND ----------

# ============================================================
# 05. 로버스트 기준 통계 생성
# ============================================================
# 기존: mean / std  →  수정: median / (IQR / 1.349)
# LoL 지표는 오른쪽 꼬리가 길어 평균·표준편차 기준 Z 는 한쪽으로 쏠립니다.

MIN_GROUP_N = 300   # 이보다 표본이 적으면 포지션 전체 통계로 폴백

stat_exprs = []
for c in ALL_FEATURES:
    stat_exprs += [
        F.expr(f"percentile_approx(`{c}`, 0.25, 1000)").alias(f"{c}__p25"),
        F.expr(f"percentile_approx(`{c}`, 0.50, 1000)").alias(f"{c}__p50"),
        F.expr(f"percentile_approx(`{c}`, 0.75, 1000)").alias(f"{c}__p75"),
        F.avg(c).alias(f"{c}__mean"),
        F.stddev(c).alias(f"{c}__std"),
        F.count(c).alias(f"{c}__n"),
    ]

wide_group = df_model.groupBy(*GROUP_KEYS).agg(*stat_exprs).toPandas()
wide_pos   = df_model.groupBy("team_position").agg(*stat_exprs).toPandas()

def to_long(pdf, has_tier):
    rows = []
    for _, r in pdf.iterrows():
        for c in ALL_FEATURES:
            iqr = r[f"{c}__p75"] - r[f"{c}__p25"]
            sigma = iqr / 1.349 if (pd.notna(iqr) and iqr > 0) else r[f"{c}__std"]
            if not pd.notna(sigma) or sigma <= 0:
                sigma = 1.0          # 분산이 0 인 지표 — Z 는 항상 0 이 됨
            rows.append({
                "team_position": r["team_position"],
                "tier_bucket": r["tier_bucket"] if has_tier else "__FALLBACK__",
                "feature": c,
                "center": float(r[f"{c}__p50"]),
                "sigma": float(sigma),
                "25percent": float(r[f"{c}__p25"]),
                "75percent": float(r[f"{c}__p75"]),
                "mean": float(r[f"{c}__mean"]),
                "std": float(r[f"{c}__std"]) if pd.notna(r[f"{c}__std"]) else None,
                "count": int(r[f"{c}__n"]),
            })
    return pd.DataFrame(rows)

stats_group = to_long(wide_group, True)
stats_pos   = to_long(wide_pos, False)

# 표본이 부족한 (포지션, 티어) 조합은 포지션 전체 통계로 대체
thin = stats_group["count"] < MIN_GROUP_N
if thin.any():
    fb = stats_pos.set_index(["team_position", "feature"])
    for idx in stats_group.index[thin]:
        key = (stats_group.at[idx, "team_position"], stats_group.at[idx, "feature"])
        if key in fb.index:
            for col in ["center", "sigma", "25percent", "75percent", "mean", "std"]:
                stats_group.at[idx, col] = fb.at[key, col]
    print(f"[폴백] 표본 부족 {int(thin.sum())}건을 포지션 전체 통계로 대체")

print("기준 통계 행:", len(stats_group))
display(stats_group.head(12))

# COMMAND ----------

# ============================================================
# 06. 로버스트 Z-score 적용 (+ 클리핑)
# ============================================================
cs_rows = []
for (pos, tb), g in stats_group.groupby(GROUP_KEYS):
    row = {"team_position": pos, "tier_bucket": tb}
    for _, r in g.iterrows():
        row[f"{r['feature']}__center"] = float(r["center"])
        row[f"{r['feature']}__sigma"] = float(r["sigma"])
    cs_rows.append(row)

df_cs = spark.createDataFrame(pd.DataFrame(cs_rows))

df_z = df_model.join(F.broadcast(df_cs), on=GROUP_KEYS, how="left")

for c in ALL_FEATURES:
    raw_z = (F.col(c) - F.col(f"{c}__center")) / F.col(f"{c}__sigma")
    df_z = df_z.withColumn(
        f"z_{c}",
        # 클리핑: 트롤 한 판이 분석 창 전체 프로필을 뒤집지 못하게 합니다
        F.least(F.greatest(raw_z, F.lit(-Z_CLIP)), F.lit(Z_CLIP)),
    )

df_z = df_z.drop(*[f"{c}__{s}" for c in ALL_FEATURES for s in ("center", "sigma")])

Z_ALL   = [f"z_{c}" for c in ALL_FEATURES]
Z_STYLE = [f"z_{c}" for c in STYLE_FEATURES]
Z_PERF  = [f"z_{c}" for c in PERF_FEATURES]

print("Z 적용 행:", df_z.count())
display(df_z.select("team_position", "champion_name", *Z_STYLE).limit(10))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. 지표 신뢰도 (ICC)
# MAGIC
# MAGIC 최근 `ANALYSIS_GAMES`(현재 10)경기의 Z-score 중앙값으로 강점과 개선점을 판단합니다. 지표마다 경기 간 흔들림이
# MAGIC 다르므로, 보정 없이 순위를 매기면 실제 약점이 아니라 분산이 큰 지표가 선택될 수 있습니다.
# MAGIC
# MAGIC 급내상관 ICC를 구해 `ANALYSIS_GAMES`경기 분석의 신뢰도로 변환한 뒤 점수에 반영합니다.
# MAGIC
# MAGIC ```
# MAGIC reliability(n) = n * ICC / (1 + (n-1) * ICC)      # Spearman-Brown, n = ANALYSIS_GAMES
# MAGIC ```
# MAGIC

# COMMAND ----------

player_stats = (
    df_z.groupBy("player_id")
    .agg(
        F.count("*").alias("n"),
        *[F.avg(c).alias(f"{c}__m") for c in Z_ALL],
        *[F.var_samp(c).alias(f"{c}__v") for c in Z_ALL],
    )
    .filter(F.col("n") >= MIN_GAMES)
    .toPandas()
)

print("신뢰도 산출 대상 플레이어:", len(player_stats))

REL_COL = f"reliability_{ANALYSIS_GAMES}"
reliability = {}
icc_table = []

if len(player_stats) >= 30:
    n = player_stats["n"].to_numpy(dtype=float)
    m = len(n)
    N = n.sum()
    # 불균형 설계 보정 계수 (단순 평균보다 정확)
    n0 = (N - (n ** 2).sum() / N) / (m - 1)

    for c in ALL_FEATURES:
        within = player_stats[f"z_{c}__v"].mean()
        between = max(player_stats[f"z_{c}__m"].var() - within / n0, 0.0)
        icc = between / (between + within) if (between + within) > 0 else 0.0
        rel = (ANALYSIS_GAMES * icc) / (1 + (ANALYSIS_GAMES - 1) * icc) if icc > 0 else 0.0
        reliability[c] = float(rel)
        icc_table.append({
            "feature": c, "icc": round(icc, 4), REL_COL: round(rel, 4),
            "within_var": round(within, 4), "between_var": round(between, 4),
        })
    print(f"보정 계수 n0 = {n0:.2f} (평균 경기 수 {n.mean():.2f})")
else:
    print("[경고] 다경기 플레이어가 부족해 신뢰도를 1.0 으로 둡니다.")
    reliability = {c: 1.0 for c in ALL_FEATURES}
    icc_table = [{"feature": c, "icc": None, REL_COL: 1.0} for c in ALL_FEATURES]

icc_pdf = pd.DataFrame(icc_table).sort_values(REL_COL, ascending=False)
display(icc_pdf)

# COMMAND ----------

# MAGIC %md
# MAGIC 신뢰도가 낮게 나온 지표는 점수 수축(`median_z × reliability`)으로 순위에서 뒤로 밀리고,
# MAGIC `MIN_RELIABILITY` 미만이면 아예 강점/개선점 후보에서 빠집니다 (아래 07-b 참조).
# MAGIC
# MAGIC `kda` 는 보통 0.5 안팎, `cs_per_min` 은 0.8 이상이 나옵니다. 이 표 자체가
# MAGIC 발표에서 "왜 이 지표를 쓰는가"에 대한 근거가 됩니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ### 07-b. 신뢰도 하한 점검  *(v9 신규)*
# MAGIC
# MAGIC `MIN_RELIABILITY` 는 **강점/개선점 목록에 올릴 자격**을 정한다. 점수 수축과는 다른 층이다.
# MAGIC
# MAGIC - 수축: `score = median_z × reliability` — 낮은 지표를 **뒤로 민다**
# MAGIC - 하한: `reliability < MIN_RELIABILITY` — 낮은 지표를 **목록에서 뺀다**
# MAGIC
# MAGIC 수축만으로는 부족하다. `kda` 는 reliability 0.452 지만 median_z 가 -1.0 이면
# MAGIC 수축 후에도 -0.45 라 `IMPROVEMENT_THRESHOLD(0.30)` 를 넘어 그대로 목록에 올라간다.
# MAGIC
# MAGIC 아래 셀은 문턱별로 **몇 개 지표가 살아남는지**와 **계열이 통째로 사라지는지**를 출력한다.
# MAGIC 생존 지표가 2개 이하면 `LOW_RELIABILITY_MODE = "flag"` 를 검토한다.

# COMMAND ----------

# ============================================================
# 07-b. 신뢰도 하한 점검 (v9)
# ============================================================
# MIN_RELIABILITY 를 확정하기 전에 실제 ICC 표로 생존 지표 수를 확인한다.

rel_table = (
    pd.DataFrame([{"feature": f, "reliability": round(reliability.get(f, 1.0), 3)}
                  for f in PERF_FEATURES])
    .sort_values("reliability", ascending=False)
    .reset_index(drop=True)
)
rel_table["MIN_RELIABILITY 통과"] = rel_table["reliability"] >= MIN_RELIABILITY

# 어느 계열에 속하는지 함께 본다. 계열이 통째로 사라지면 그 축의 피드백 자체가 없어진다.
feature_family = {f: fam for fam, members in PERF_FAMILIES.items() for f in members}
rel_table["계열"] = rel_table["feature"].map(feature_family)

print(f"MIN_RELIABILITY = {MIN_RELIABILITY:.2f} / LOW_RELIABILITY_MODE = '{LOW_RELIABILITY_MODE}'")
print(f"성과 축 {len(PERF_FEATURES)}개 중 통과 {int(rel_table['MIN_RELIABILITY 통과'].sum())}개\n")
display(rel_table)

survivors = rel_table.loc[rel_table["MIN_RELIABILITY 통과"], "feature"].tolist()
dropped = rel_table.loc[~rel_table["MIN_RELIABILITY 통과"], "feature"].tolist()
if dropped:
    print("후보에서 제외" if LOW_RELIABILITY_MODE == "exclude" else "판단 유보 표시가 붙는 지표:")
    for feature in dropped:
        value = float(rel_table.loc[rel_table["feature"] == feature, "reliability"].iloc[0])
        print(f"  - {feature} (reliability {value:.3f})")

# ------------------------------------------------------------
# 계열 소멸 점검 — 모드에 따라 결과가 다르다
# ------------------------------------------------------------
# exclude: 미달 지표가 후보에서 빠지므로, 구성원이 전부 미달인 계열은 통째로 사라진다.
# flag   : 후보 자격은 유지되므로 계열은 살아남는다. 대신 그 계열에서 나오는 항목은
#          전부 low_reliability 표시가 붙어 '확신 있는' 발견으로 집계되지 않는다.
starved = [
    family for family, members in PERF_FAMILIES.items()
    if not any(f in survivors for f in members)
]
dead_families = starved if LOW_RELIABILITY_MODE == "exclude" else []

if starved and LOW_RELIABILITY_MODE == "exclude":
    print(f"\n[주의] 통째로 사라지는 계열: {starved}")
    print("       이 계열의 지표는 강점/개선점에 절대 나오지 않는다.")
    for family in starved:
        print(f"       {family}: {PERF_FAMILIES[family]}")
elif starved:
    print(f"\n[알림] 구성원이 전부 문턱 미달인 계열: {starved}")
    print(f"       LOW_RELIABILITY_MODE='{LOW_RELIABILITY_MODE}' 이므로 계열은 유지된다.")
    print("       다만 이 계열에서 나오는 항목은 low_reliability 표시가 붙고")
    print("       has_strengths / has_improvements 집계에서는 빠진다.")
    for family in starved:
        print(f"       {family}: {PERF_FAMILIES[family]}")

# ------------------------------------------------------------
# 문턱별 생존 수 — 어디서 급격히 줄어드는지 본다
# ------------------------------------------------------------
print("\n[문턱별 생존 지표 수]")
for threshold in [0.3, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7]:
    alive = [f for f in PERF_FEATURES if reliability.get(f, 1.0) >= threshold]
    families_alive = len({feature_family.get(f) for f in alive} - {None})
    mark = "  ← 현재" if abs(threshold - MIN_RELIABILITY) < 1e-9 else ""
    print(f"  {threshold:.2f}: {len(alive)}개 / 계열 {families_alive}개  "
          f"{alive}{mark}")

# ------------------------------------------------------------
# 권고
# ------------------------------------------------------------
# 강점/개선점은 계열당 1개씩만 뽑는다. 따라서 살아남은 계열 수가
# "한 번에 나올 수 있는 항목 수" 의 상한이다. 지표 수보다 이쪽이 실질적인 제약이다.
if LOW_RELIABILITY_MODE == "exclude":
    alive_families = {feature_family.get(f) for f in survivors} - {None}
else:
    alive_families = {feature_family.get(f) for f in PERF_FEATURES} - {None}

print(f"\n[상한] 후보 계열 {len(alive_families)}개 → 강점·개선점은 각각 최대 "
      f"{len(alive_families)}개까지만 나올 수 있다. {sorted(alive_families)}")
if LOW_RELIABILITY_MODE == "flag":
    confident_families = {feature_family.get(f) for f in survivors} - {None}
    if confident_families != alive_families:
        print(f"       이 중 '확신 있는' 발견이 가능한 계열은 {len(confident_families)}개 "
              f"{sorted(confident_families)} — 나머지는 판단 유보 표시가 붙는다.")
    else:
        print("       전 지표가 문턱을 넘어 판단 유보 표시가 붙는 항목은 없다 "
              "(flag 모드가 사실상 무동작).")

print("\n" + "-" * 62)
if LOW_RELIABILITY_MODE == "flag" and starved:
    print(f"[정상] flag 모드로 계열 {len(alive_families)}개를 모두 유지한다.")
    print(f"       문턱 미달 지표 {dropped} 는 목록에 남되 다음이 적용된다.")
    print("         - low_reliability=True 와 완곡한 prompt_hint")
    print("         - priority 를 3 이하로 밀어 신뢰 가능한 지표보다 뒤에 배치")
    print("         - has_strengths / has_improvements 에서 제외")
    print("       즉 '확신에 찬 문장' 은 막으면서 지표 자체는 계속 노출된다.")
    print("\n       더 근본적으로 풀려면 ANALYSIS_GAMES 를 늘려 reliability 를 올린다.")
    icc_map = {row["feature"]: row["icc"] for _, row in icc_pdf.iterrows()}
    windows = (ANALYSIS_GAMES, ANALYSIS_GAMES + 5, ANALYSIS_GAMES + 10)
    print(f"\n       [경기 수별 reliability]  문턱 {MIN_RELIABILITY:.2f} / 현재 {ANALYSIS_GAMES}경기")
    header = "".join(f"{str(n) + '경기':>7s}" for n in windows)
    print(f"       {'feature':28s} {'ICC':>6s}{header}")
    for feature in PERF_FEATURES:
        icc = icc_map.get(feature)
        if icc is None:
            continue
        values = [(n * icc) / (1 + (n - 1) * icc) for n in windows]
        marks = "".join(f"{v:7.3f}" for v in values)
        need = next((n for n, v in zip(windows, values) if v >= MIN_RELIABILITY), None)
        note = ("" if need == windows[0]
                else (f"  ← {need}경기부터 통과" if need else f"  ← {windows[-1]}경기로도 미달"))
        print(f"       {feature:28s} {icc:6.3f}{marks}{note}")
elif len(survivors) <= 2 or len(alive_families) <= 2:
    if len(survivors) <= 2:
        print(f"[권고] 생존 지표가 {len(survivors)}개뿐이다. 문턱이 너무 높다.")
    else:
        print(f"[권고] 생존 지표는 {len(survivors)}개지만 계열이 {len(alive_families)}개뿐이다.")
        print("       계열당 1개 제한 때문에 실제로 뽑히는 항목은 그만큼으로 묶인다.")
    print("       이대로면 강점/개선점이 대부분 비고, ALWAYS_SHOW_* 폴백이 만든")
    print("       is_relative 항목만 남아 피드백이 공허해진다. 둘 중 하나를 고른다.")
    print()
    print("       (a) Cell 00 에서 모드를 바꾼다 — 제외 대신 표시만 붙인다")
    print('           LOW_RELIABILITY_MODE = "flag"')
    print("           → 목록에는 남되 low_reliability=True 와 완곡 힌트가 붙어")
    print(f"             LLM 이 '{ANALYSIS_GAMES}경기로는 판단이 이른 지표' 로 서술한다.")
    print()
    print("       (b) 문턱을 낮춘다 — 위 '문턱별 생존 지표 수' 에서")
    print("           계열 3개 이상이 살아남는 가장 높은 값을 고른다.")
    print()
    print("       (c) ANALYSIS_GAMES 를 늘린다 — 근본 해결책이다.")
    print(f"           reliability 는 경기 수의 함수다. 현재 {ANALYSIS_GAMES}경기 기준.")
    icc_map = {row["feature"]: row["icc"] for _, row in icc_pdf.iterrows()}
    windows = (ANALYSIS_GAMES, ANALYSIS_GAMES + 5, ANALYSIS_GAMES + 10)
    header = "".join(f"{str(n) + '경기':>7s}" for n in windows)
    print(f"\n           [경기 수별 reliability]")
    print(f"           {'feature':28s} {'ICC':>6s}{header}")
    for feature in PERF_FEATURES:
        icc = icc_map.get(feature)
        if icc is None:
            continue
        cells = "".join(f"{(n * icc) / (1 + (n - 1) * icc):7.3f}" for n in windows)
        print(f"           {feature:28s} {icc:6.3f}{cells}")
elif dead_families:
    print("[권고] 생존 지표 수와 계열 수는 충분하지만 사라지는 계열이 있다.")
    print("       그 계열의 피드백이 필요하면 문턱을 낮추거나 flag 모드를 쓴다.")
else:
    print(f"[정상] 생존 지표 {len(survivors)}개 / 계열 {len(alive_families)}개. "
          "현재 문턱을 그대로 쓴다.")
print("-" * 62)
print("MIN_RELIABILITY / LOW_RELIABILITY_MODE 는 style_model.json 의 config 에 들어간다.")
print("→ 바꾸면 Cell 18 을 다시 실행해야 추론에 반영된다.")

# COMMAND ----------

stats_out = stats_group.merge(
    icc_pdf[["feature", "icc", REL_COL]].rename(columns={REL_COL: "reliability"}),
    on="feature", how="left",
)
stats_out["analysis_games"] = ANALYSIS_GAMES
stats_out["z_clip"] = Z_CLIP
stats_out["created_at"] = pd.Timestamp.utcnow().isoformat()

(
    spark.createDataFrame(stats_out)
    .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    .save(PATH_POS_STATS)
)
print("position_feature_stats 저장 완료:", len(stats_out), "행")
display(stats_out.head(12))

# COMMAND ----------

chk = spark.read.format("delta").load(PATH_POS_STATS)
print("행:", chk.count(), "/ 컬럼:", len(chk.columns))
display(chk.filter(F.col("team_position") == "TOP").orderBy(F.desc("reliability")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. 스타일 축 준비 — 전반적 성과 제거
# MAGIC
# MAGIC 기존 노트북의 핵심 실패 원인입니다.
# MAGIC
# MAGIC Cell 24 출력을 보면 Cluster 0 은 13개 피처가 **전부 음수**, Cluster 1 은 **전부 양수**였습니다.
# MAGIC 13개 지표가 서로 양의 상관이라 첫 번째 주성분이 "전반적 실력"이 되고, K-Means 는 그 축을
# MAGIC 위아래로 자른 것뿐입니다. 스타일이 아니라 성적표죠.
# MAGIC
# MAGIC 두 단계로 고칩니다.
# MAGIC
# MAGIC 1. 방향 중립적인 **스타일 축 6개만** 사용 (성과 축은 강약점 계산으로 분리)
# MAGIC 2. 각 경기 행에서 **자기 행의 평균 Z 를 빼기** (프로파일 센터링)
# MAGIC
# MAGIC 2번이 결정적입니다. "모든 지표가 +0.5" 인 잘하는 유저와 "모든 지표가 -0.5" 인 못하는 유저는
# MAGIC 센터링 후 둘 다 0 벡터가 되어 같은 스타일로 묶입니다. 남는 건 **어느 지표가 상대적으로 높은가**,
# MAGIC 즉 플레이의 형태뿐입니다.

# COMMAND ----------

# ============================================================
# 09. 스타일 행렬 구성 + 프로파일 센터링
# ============================================================
pdf = (
    df_z.select(
        "match_id", "player_id", "game_start_datetime",
        "team_position", "tier_bucket", "champion_name", "win",
        *Z_ALL,
    )
    .toPandas()
)
print("pandas 로 수집:", pdf.shape)

S = pdf[Z_STYLE].to_numpy(dtype=float)

# 행 단위 센터링 — 전반적 성과 수준을 제거하고 프로파일의 형태만 남깁니다
S_centered = S - S.mean(axis=1, keepdims=True)

for i, c in enumerate(STYLE_FEATURES):
    pdf[f"s_{c}"] = S_centered[:, i]

S_COLS = [f"s_{c}" for c in STYLE_FEATURES]

# 센터링 효과 확인: 원본은 서로 강하게 양의 상관, 센터링 후에는 상관이 흩어집니다
print("\n[센터링 전] 스타일 피처 간 평균 상관:",
      round(pd.DataFrame(S, columns=STYLE_FEATURES).corr().values[np.triu_indices(len(STYLE_FEATURES), 1)].mean(), 3))
print("[센터링 후] 스타일 피처 간 평균 상관:",
      round(pd.DataFrame(S_centered, columns=STYLE_FEATURES).corr().values[np.triu_indices(len(STYLE_FEATURES), 1)].mean(), 3))

# COMMAND ----------

# MAGIC %md
# MAGIC ### 09-a. 잔차화 공통 함수  *(v9 신규)*
# MAGIC
# MAGIC 09-b(스타일 축)와 09-c(성과 축)가 같은 계산을 한다. 한쪽만 고치면 학습과 추론이
# MAGIC 조용히 어긋나므로 계산 본체를 함수 하나로 묶는다.
# MAGIC
# MAGIC ```
# MAGIC 잔차 = y - design @ beta,   design = [1, 통제변수...]
# MAGIC ```
# MAGIC
# MAGIC 두 곳의 차이는 **무엇을 통제하는가** 뿐이다.
# MAGIC
# MAGIC | | 대상 | 통제변수 | 합이 0 제약 |
# MAGIC |---|---|---|---|
# MAGIC | 09-b | 스타일 축 `s_*` | `win` + `perf_level` | 복원함 (프로파일 센터링) |
# MAGIC | 09-c | 성과 축 `z_*` | `win` 만 | 복원 안 함 |
# MAGIC
# MAGIC 성과 축에 `perf_level`을 넣으면 **자기 자신을 설명 변수로 쓰는 셈**이라 잔차가
# MAGIC 무의미해진다. 그래서 `win` 만 통제한다.

# COMMAND ----------

# ============================================================
# 09-a. 잔차화 공통 함수 (v9)
# ============================================================

def win_flag_series(series) -> pd.Series:
    """Spark/pandas bool 과 문자열 bool 을 모두 0.0/1.0 으로 통일."""
    if str(series.dtype) == "bool":
        return series.astype(float)
    return series.astype(str).str.lower().isin(["true", "1", "1.0"]).astype(float)


def fit_residual(Y: np.ndarray, design: np.ndarray):
    """최소제곱 잔차와 계수. beta shape = (설명변수 수, 대상 축 수)."""
    beta, *_ = np.linalg.lstsq(design, Y, rcond=None)
    return Y - design @ beta, beta


def build_design(frame: pd.DataFrame, control_cols: list, mask: np.ndarray) -> np.ndarray:
    """절편 + 통제변수. 추론 쪽 design 순서와 반드시 같아야 한다."""
    columns = [np.ones(int(mask.sum()))]
    for column in control_cols:
        columns.append(frame.loc[mask, column].to_numpy(dtype=float))
    return np.column_stack(columns)


def snapshot_or_restore(frame: pd.DataFrame, columns: list, prefix: str = "raw_") -> None:
    """첫 실행에는 원본을 백업하고, 재실행에는 백업에서 되돌린다.

    이미 잔차화된 값 위에 또 잔차화하면 계수가 0 에 가까워져
    서빙 JSON 에 사실상 '변환 없음' 이 실려 나간다. 그 사고를 막는다.
    """
    for column in columns:
        backup = f"{prefix}{column}"
        if backup in frame.columns:
            frame[column] = frame[backup]
        else:
            frame[backup] = frame[column]


def residualize_by_position(frame: pd.DataFrame, target_cols: list, control_cols: list,
                            positions: list, zero_sum: bool = False,
                            feature_names: list | None = None):
    """포지션별로 target_cols 를 control_cols 로 잔차화하고 frame 을 제자리 갱신.

    zero_sum=True 면 잔차의 행 평균을 다시 빼서 '합이 0' 제약을 복원한다.
    프로파일 센터링을 거친 스타일 축 전용이고, 성과 축에는 쓰면 안 된다.

    반환: ({position: beta}, 승패차 비교 표)
    """
    names = feature_names or target_cols
    models, rows = {}, []

    for position in positions:
        mask = (frame["team_position"] == position).to_numpy()
        if mask.sum() == 0:
            continue

        Y = frame.loc[mask, target_cols].to_numpy(dtype=float)
        design = build_design(frame, control_cols, mask)
        resid, beta = fit_residual(Y, design)
        if zero_sum:
            resid = resid - resid.mean(axis=1, keepdims=True)

        frame.loc[mask, target_cols] = resid
        models[position] = beta

        if "_win_flag" not in frame.columns:
            continue
        win = frame.loc[mask, "_win_flag"].to_numpy() > 0.5
        if not (win.any() and (~win).any()):
            continue
        for i, name in enumerate(names):
            rows.append({
                "position": position,
                "feature": name,
                "승패차_전": round(float(np.median(Y[win, i]) - np.median(Y[~win, i])), 3),
                "승패차_후": round(float(np.median(resid[win, i]) - np.median(resid[~win, i])), 3),
            })

    report = pd.DataFrame(rows)
    if len(report):
        report["개선"] = (report["승패차_전"].abs() - report["승패차_후"].abs()).round(3)
    return models, report


def median_win_gap(frame: pd.DataFrame, features: list, prefix: str = "z_") -> pd.DataFrame:
    """각 축의 승리/패배 median 차이. '결과 지표' 탐지에 쓴다."""
    win = frame["_win_flag"].to_numpy() > 0.5
    rows = []
    for feature in features:
        column = f"{prefix}{feature}"
        if column not in frame.columns:
            continue
        win_median = float(frame.loc[win, column].median())
        loss_median = float(frame.loc[~win, column].median())
        rows.append({
            "feature": feature,
            "승리_median_z": round(win_median, 3),
            "패배_median_z": round(loss_median, 3),
            "승패차": round(win_median - loss_median, 3),
        })
    gap = pd.DataFrame(rows)
    if len(gap):
        gap["abs차"] = gap["승패차"].abs().round(3)
        gap = gap.sort_values("abs차", ascending=False).reset_index(drop=True)
    return gap


print("잔차화 공통 함수 등록 완료 — 09-b(스타일 축)와 09-c(성과 축)가 함께 사용합니다.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 09-b. 스타일 축 잔차화 — 승패·실력 성분 제거
# MAGIC
# MAGIC 행 평균을 빼는 센터링은 "모든 축이 같이 오르내리는" 공통 성분만 지웁니다.
# MAGIC 그런데 실제 데이터에서는 실력이 오를 때 `damage_share`·`gold_share` 는 오르고
# MAGIC `damage_taken_per_min` 은 내려갑니다. **부호가 엇갈린 이 대비는 센터링으로 지워지지 않고**,
# MAGIC 그대로 군집을 가르는 축이 됩니다 (v7 에서 eta² 0.13~0.25).
# MAGIC
# MAGIC 그래서 승패와 성과 수준을 설명 변수로 두고 회귀한 뒤 잔차만 남깁니다.
# MAGIC 스타일이 진짜 스타일이라면 승패와 무관해야 하므로 이 성분은 버려도 됩니다.
# MAGIC
# MAGIC 합성 데이터 검증 결과: eta² 0.129 → 0.0001, 군집별 승률 폭 0.181 → 0.009,
# MAGIC 애매 경기 15.0% → 5.9%, **원형 복원 ARI 0.640 → 0.937**.
# MAGIC 실력 성분을 빼면 스타일 신호까지 깎일까 우려했지만 오히려 군집이 선명해집니다.

# COMMAND ----------

# ============================================================
# 09-b. 스타일 축 잔차화
# ============================================================
# 반드시 Cell 09(프로파일 센터링) 뒤, Cell 10(k 평가) 앞이어야 한다.
# 학습이 끝난 뒤에 실행하면 아무 효과가 없다.
# 계산 본체는 09-a 의 residualize_by_position 이다.

RESIDUAL_CONTROLS = ["win", "perf_level"]

pdf["_win_flag"] = win_flag_series(pdf["win"])

# 재실행 안전장치: 이미 잔차화된 s_ 위에 또 잔차화하지 않도록 원본에서 다시 시작한다.
snapshot_or_restore(pdf, S_COLS)

# 성과 수준 = 성과 축 z 의 행 평균. 이 경기에서 이 유저가 전반적으로 어땠는가.
# 09-c 를 이미 돌린 뒤라면 z_ 는 잔차값이므로 원본(raw_z_)을 써야 통제 변수가 흔들리지 않는다.
perf_z_cols = [
    f"raw_z_{feature}" if f"raw_z_{feature}" in pdf.columns else f"z_{feature}"
    for feature in PERF_FEATURES
    if f"raw_z_{feature}" in pdf.columns or f"z_{feature}" in pdf.columns
]
pdf["_perf_level"] = pdf[perf_z_cols].mean(axis=1)

residual_models, residual_check = residualize_by_position(
    frame=pdf,
    target_cols=S_COLS,
    control_cols=["_win_flag", "_perf_level"],
    positions=POSITIONS,
    zero_sum=True,                      # 센터링으로 생긴 '합이 0' 제약 복원
    feature_names=STYLE_FEATURES,
)

display(residual_check.sort_values("승패차_전", key=abs, ascending=False))

worst = residual_check["승패차_후"].abs().max()
print(f"잔차화 후 최대 승패차: {worst:.3f}")
if worst > 0.15:
    print("[주의] 아직 승패가 남아 있는 축이 있습니다. 위 표에서 해당 축을 확인하세요.")

corr = (pd.DataFrame(pdf[S_COLS].to_numpy(), columns=STYLE_FEATURES)
        .corr().values[np.triu_indices(len(STYLE_FEATURES), 1)].mean())
print(f"\n[잔차화 후] 스타일 피처 간 평균 상관: {corr:.3f}")
print(f"참고: 합이 0 인 제약 때문에 이론적 하한은 {-1 / (len(STYLE_FEATURES) - 1):.3f} 입니다.")

# _win_flag / _perf_level 은 09-c 에서 다시 쓰므로 여기서 지우지 않는다.
print("\n스타일 축 잔차화 완료 — 다음은 09-c(성과 축)입니다.")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 09-c. 성과 축 잔차화 — 결과 지표에서 승패 성분 제거  *(v9 신규)*
# MAGIC
# MAGIC `objective_damage_per_min` 은 승리 경기 median z **+0.579**, 패배 **-0.47** 로 승패차가
# MAGIC **+1.05** 다. 오브젝트 딜은 이겨서 억제기·넥서스를 칠 기회가 생겨야 올라가는 **결과 지표**다.
# MAGIC
# MAGIC v8 에서 이 축을 `STYLE_FEATURES` 에서 빼 `PERF_FEATURES` 로 옮겼지만, 성과 축은 아무 보정
# MAGIC 없이 그대로 쓰이므로 Cell 24 출력에 **"오브젝트 기여도 +0.48"이 강점**으로 찍힌다.
# MAGIC "이겨서 높다"가 "잘해서 높다"로 포장되어 사용자에게 나가는 상태다.
# MAGIC
# MAGIC 여기서 09-b 와 **같은 잔차화**를 성과 축에도 건다. 통제 변수만 다르다.
# MAGIC
# MAGIC - 09-b (스타일 축): `win` + `perf_level`
# MAGIC - 09-c (성과 축): **`win` 만** — `perf_level` 은 성과 축들의 평균이라 자기 자신을 빼는 꼴이 된다
# MAGIC
# MAGIC 잔차화 뒤의 z 는 **"같은 승패 조건에서 얼마나 했는가"** 를 뜻한다.
# MAGIC 이긴 경기끼리, 진 경기끼리 비교한 값이라 "이겨서 높다"가 강점으로 둔갑하지 않는다.
# MAGIC
# MAGIC **대상은 `objective_damage_per_min` 하나로 고정돼 있다** (Cell 00 의 `PERF_RESIDUAL_FEATURES`).
# MAGIC
# MAGIC 자동 탐지 자체는 그대로 돌아가고 결과도 출력된다. `kda`·`gold_per_min` 처럼 원래 승패와
# MAGIC 강하게 붙어 있는 축도 탐지에는 걸리지만, 잔차화는 하지 않는다. 그 축들까지 보정하면
# MAGIC "이겨서 좋은 수치"가 전반적으로 사라지는 대신 제품 동작 변화도 크기 때문이다.
# MAGIC 09-c 는 **탐지됐지만 제외한 축** 목록을 매 실행마다 경고로 출력하므로, 범위를 넓힐지는
# MAGIC 그 출력을 보고 판단하면 된다. 넓히려면 `PERF_RESIDUAL_FEATURES = None` 으로 되돌린다.

# COMMAND ----------

# ============================================================
# 09-c. 성과 축 잔차화 (v9)
# ============================================================
# 반드시 09-b 뒤, Cell 16(스타일 기준선) 앞이어야 한다.
# 기준선은 이 z 값의 군집별 중앙값이므로 순서가 바뀌면 보정이 반영되지 않는다.

if "_win_flag" not in pdf.columns:            # 09-b 를 건너뛴 경우 대비
    pdf["_win_flag"] = win_flag_series(pdf["win"])

# ------------------------------------------------------------
# (0) 원본 복원 — 탐지보다 먼저 해야 한다
# ------------------------------------------------------------
# 이 셀을 두 번째로 실행하면 z_ 는 이미 잔차값이라 승패차가 0 에 가깝다.
# 그 상태로 탐지하면 "대상 없음" 이 나오고, 학습 데이터는 잔차화돼 있는데
# 서빙 JSON 의 perf_residual 만 비어 나가 추론이 조용히 어긋난다.
# 그래서 탐지 전에 항상 원본(raw_z_*)에서 다시 시작한다.
snapshot_or_restore(pdf, [f"z_{feature}" for feature in PERF_FEATURES])

# ------------------------------------------------------------
# (1) 승패차가 큰 성과 축 자동 탐지
# ------------------------------------------------------------
perf_gap = median_win_gap(pdf, PERF_FEATURES)
print("[성과 축의 승패 민감도 — 절댓값이 클수록 '결과 지표'에 가깝다]")
display(perf_gap)

auto_detected = perf_gap.loc[perf_gap["abs차"] >= PERF_RESIDUAL_CUT, "feature"].tolist()
print(f"|승패차| >= {PERF_RESIDUAL_CUT:.2f} 인 성과 축: {auto_detected or '없음'}")

if PERF_RESIDUAL_FEATURES is None:
    perf_residual_features = auto_detected
else:
    perf_residual_features = [f for f in PERF_RESIDUAL_FEATURES if f in PERF_FEATURES]
    unknown = [f for f in PERF_RESIDUAL_FEATURES if f not in PERF_FEATURES]
    if unknown:
        raise ValueError(f"PERF_FEATURES 에 없는 축을 지정했습니다: {unknown}")
    print(f"[수동 지정] PERF_RESIDUAL_FEATURES 사용: {perf_residual_features}")
    skipped = [f for f in auto_detected if f not in perf_residual_features]
    if skipped:
        print(f"[주의] 자동 탐지됐지만 제외한 축: {skipped}")
        print("       이 축들은 승패 정보를 그대로 들고 강점/개선점에 반영됩니다.")

# ------------------------------------------------------------
# (2) 포지션별 잔차화 — win 만 통제
# ------------------------------------------------------------
perf_residual_models = {}
perf_residual_check = pd.DataFrame()

if not perf_residual_features:
    print("\n잔차화할 성과 축이 없습니다. 이후 셀은 그대로 진행됩니다.")
else:
    perf_target_cols = [f"z_{feature}" for feature in perf_residual_features]

    perf_residual_models, perf_residual_check = residualize_by_position(
        frame=pdf,
        target_cols=perf_target_cols,
        control_cols=["_win_flag"],     # win 만! perf_level 을 넣으면 자기 자신을 뺀다
        positions=POSITIONS,
        zero_sum=False,                 # 성과 축에는 '합이 0' 제약이 없다
        feature_names=perf_residual_features,
    )

    print(f"\n[포지션별 잔차화 결과] 대상 {len(perf_residual_features)}축 "
          f"× {len(perf_residual_models)}포지션")
    display(perf_residual_check.sort_values("승패차_전", key=abs, ascending=False))

    after_gap = median_win_gap(pdf, perf_residual_features)
    before_gap = perf_gap.set_index("feature")["승패차"]
    after_gap["승패차_전"] = after_gap["feature"].map(before_gap).round(3)
    print("\n[전체 기준 승패차 — 전 vs 후]")
    print(after_gap[["feature", "승패차_전", "승패차"]]
          .rename(columns={"승패차": "승패차_후"}).to_string(index=False))

    worst_perf = after_gap["abs차"].max()
    print(f"\n잔차화 후 최대 승패차: {worst_perf:.3f}")
    if worst_perf > 0.15:
        print("[주의] 승패가 남아 있는 성과 축이 있습니다. win 이 선형으로만 설명되지 않는 축입니다.")

    print("\n원본 z 는 raw_z_* 컬럼에 남아 있습니다 (진단·비교용).")
    print("이 시점부터 z_* 는 '같은 승패 조건에서의 성과' 를 뜻합니다.")

# _win_flag / _perf_level 은 여기까지만 쓰고 정리한다.
pdf = pdf.drop(columns=[c for c in ("_win_flag", "_perf_level") if c in pdf.columns])
print("\n성과 축 잔차화 완료 — 이 상태로 k 탐색·학습·기준선 산출을 진행합니다.")

# COMMAND ----------

# MAGIC %md
# MAGIC > 센터링을 하면 6개 피처의 합이 항상 0 이 되어 한 차원이 중복됩니다.
# MAGIC > K-Means 동작에는 문제가 없지만, 중심점을 해석할 때 "합이 0"이라는 제약을 감안하세요.
# MAGIC > 한 지표가 높으면 나머지가 낮게 나오는 건 정상입니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. 포지션별 K-Means — k 선택

# COMMAND ----------

# ============================================================
# 10. k 평가 함수
# ============================================================
# 기존: silhouette 만 봄 → 구형 데이터에서는 거의 항상 k=2 가 최고로 나옴
#       (실제로 45k 행에서 k=2 가 0.576 으로 압도적이었음)
# 수정: 세 가지를 함께 봅니다.
#   1) silhouette     : 분리도
#   2) min_share      : 가장 작은 군집 비중 (5% 미만이면 서비스에서 쓸 수 없음)
#   3) bootstrap ARI  : 재표본으로 다시 학습했을 때 같은 군집이 나오는가 (안정성)
#
# 주의: silhouette 최대로 k 를 고르면 안 됩니다. 구조적으로 작은 k 를 선호해서
#       항상 k=2 가 나옵니다. 실제 선택 규칙은 Cell 12 참조.

from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score, adjusted_rand_score

def evaluate_k(X, k, n_boot=5, sil_sample=4000, seed=SEED):
    base = KMeans(n_clusters=k, n_init=20, random_state=seed).fit(X)

    rng = np.random.RandomState(seed)
    idx = rng.choice(len(X), min(sil_sample, len(X)), replace=False)
    sil = silhouette_score(X[idx], base.labels_[idx])

    shares = np.bincount(base.labels_, minlength=k) / len(X)

    aris = []
    for b in range(n_boot):
        bi = rng.choice(len(X), len(X), replace=True)
        boot = KMeans(n_clusters=k, n_init=10, random_state=seed + b + 1).fit(X[bi])
        aris.append(adjusted_rand_score(base.predict(X[bi]), boot.labels_))

    return {
        "k": k,
        "silhouette": round(float(sil), 4),
        "min_share": round(float(shares.min()), 4),
        "max_share": round(float(shares.max()), 4),
        "ari_mean": round(float(np.mean(aris)), 4),
        "ari_min": round(float(np.min(aris)), 4),
        "inertia": round(float(base.inertia_), 1),
    }

# COMMAND ----------

# ============================================================
# 11. 포지션별 k 탐색
# ============================================================
# 축이 5개(센터링 후 유효 4차원)로 줄었고 프로토타입이 포지션당 3개다.
K_RANGE = range(2, 6)
k_search = {}

for pos in POSITIONS:
    mask = pdf["team_position"] == pos
    X = pdf.loc[mask, S_COLS].to_numpy(dtype=float)
    if len(X) < 500:
        print(f"[건너뜀] {pos}: 표본 {len(X)}행")
        continue
    rows = [evaluate_k(X, k) for k in K_RANGE]
    k_search[pos] = pd.DataFrame(rows)
    print(f"\n===== {pos} (n={len(X)}) =====")
    print(k_search[pos].to_string(index=False))

# 재실행 방지용 저장
all_k = pd.concat([res.assign(position=p) for p, res in k_search.items()])

(
    spark.createDataFrame(all_k)
    .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    .save(BASE + "gold/_tmp_k_search")
)
print("k 탐색 결과 저장 완료")

# COMMAND ----------

# ============================================================
# 12. k 선택 규칙
# ============================================================
ARI_MIN, SHARE_MIN, SIL_FLOOR_RATIO = 0.50, 0.19, 0.50

def choose_k(res: pd.DataFrame, pos: str) -> int:
    sil_floor = res["silhouette"].max() * SIL_FLOOR_RATIO
    ok = res[
        (res["ari_min"] >= ARI_MIN)
        & (res["min_share"] >= SHARE_MIN)
        & (res["silhouette"] >= sil_floor)
    ]
    if len(ok):
        return int(ok["k"].max())
    ok = res[(res["min_share"] >= SHARE_MIN) & (res["silhouette"] >= sil_floor)]
    if len(ok):
        return int(ok.loc[ok["ari_min"].idxmax(), "k"])
    print(f"[경고] {pos}: 모든 기준 미달 → k=3")
    return 3

auto_k = {pos: choose_k(res, pos) for pos, res in k_search.items()}
print("자동 선택:", auto_k)

# v8: objective 축을 뺀 뒤 프로토타입이 포지션당 3개뿐이므로 k 는 3 이 상한이다.
# k=4 를 쓰면 assign_style_labels 가 "라벨이 부족하다"며 ValueError 를 낸다.
chosen_k = {"TOP": 3, "JUNGLE": 3, "MIDDLE": 3, "BOTTOM": 3, "UTILITY": 3}
chosen_k = {position: k for position, k in chosen_k.items() if position in k_search}

print("최종 확정:", chosen_k)
for position, k in chosen_k.items():
    row = k_search[position][k_search[position]["k"] == k].iloc[0]
    print(
        f"  {position}: k={k} sil={row['silhouette']} "
        f"ari_min={row['ari_min']} min_share={row['min_share']}"
    )
    if row["ari_min"] < ARI_MIN or row["min_share"] < 0.15:
        print(f"  [재검토 필요] {position}의 안정성/군집 비중이 기준보다 낮습니다.")


# COMMAND ----------

# MAGIC %md
# MAGIC ### k=2 가 계속 선택된다면
# MAGIC
# MAGIC 센터링 후에도 k=2 가 ARI·silhouette 모두에서 최고라면, 그 포지션에는 실제로
# MAGIC 두 가지 플레이 형태밖에 없다는 뜻일 수 있습니다. 하지만 중심점을 찍어봤을 때
# MAGIC 여전히 모든 값이 한쪽 부호로 몰려 있다면 센터링이 덜 된 것이므로,
# MAGIC `STYLE_FEATURES` 에서 성과와 상관이 가장 높은 지표(보통 `gold_share`)를 빼고
# MAGIC 다시 돌려보세요.

# COMMAND ----------

# ============================================================
# 13. 최종 모델 학습 + 라벨 부여
# ============================================================
style_models = {}
pdf["cluster"] = -1

# ------------------------------------------------------------
# 인덱스 고정 (canonical relabeling)
# ------------------------------------------------------------
# random_state 만으로는 부족합니다. 데이터가 조금만 바뀌어도
# (필터 추가, 행 수 변화) KMeans 가 같은 군집에 다른 번호를 붙여
# 스타일 이름 매핑이 통째로 어긋납니다. 실제로 이 노트북에서
# 필터를 추가할 때마다 매핑을 다시 만들어야 했습니다.
#
# 그래서 학습 후 군집 번호를 "그 군집을 지배하는 축" 기준으로
# 다시 매깁니다. STYLE_FEATURES 순서가 곧 번호 순서가 되므로
# 데이터가 바뀌어도 같은 성격의 군집은 같은 번호를 받습니다.
#   0 = kill_participation 축   1 = damage_share 축
#   2 = gold_share 축           3 = damage_taken 축
#   4 = vision 축               5 = objective 축
# (k=4 면 실제로 나타난 축 4개만 0~3 으로 압축됩니다)

def canonical_order(centers):
    """군집을 지배 축 순서로 정렬한 인덱스 배열을 돌려준다.

    지배 축 = |중심값| 이 가장 큰 피처. 같은 축이 겹치면 그 축의
    값이 큰 쪽을 앞에 둔다.
    """
    keys = []
    for ci, c in enumerate(centers):
        dom = int(np.argmax(np.abs(c)))
        keys.append((dom, -c[dom], ci))
    return [k[2] for k in sorted(keys)]


for pos, k in chosen_k.items():
    mask = pdf["team_position"] == pos
    X = pdf.loc[mask, S_COLS].to_numpy(dtype=float)
    km = KMeans(n_clusters=k, n_init=50, random_state=SEED).fit(X)

    # 지배 축 기준으로 군집 번호를 다시 매긴다
    order = canonical_order(km.cluster_centers_)
    remap = {old_i: new_i for new_i, old_i in enumerate(order)}

    km.cluster_centers_ = km.cluster_centers_[order]
    km.labels_ = np.array([remap[l] for l in km.labels_])

    style_models[pos] = km
    pdf.loc[mask, "cluster"] = km.labels_

print(pdf.groupby(["team_position", "cluster"]).size().to_string())

# 재실행 시 번호가 유지되는지 확인용 지문.
# 이 값이 그대로면 기존 스타일 이름 매핑을 계속 써도 됩니다.
print("\n[인덱스 지문] 재실행 후 이 값이 같아야 매핑이 유효합니다")
for pos in sorted(style_models):
    cen = style_models[pos].cluster_centers_
    dom = [STYLE_FEATURES[int(np.argmax(np.abs(c)))] for c in cen]
    print(f"  {pos:8s} {dom}")

# COMMAND ----------

# ============================================================
# 14. 중심점 해석 테이블
# ============================================================
# 이 표를 직접 읽고 다음 셀에서 사람이 이름을 붙입니다.
# 자동 명명은 하지 마세요 — 서비스에 그대로 노출되는 문구입니다.

for pos, km in style_models.items():
    cen = pd.DataFrame(km.cluster_centers_, columns=STYLE_FEATURES).round(2)
    cen.index.name = "cluster"
    share = (pdf[pdf["team_position"] == pos]["cluster"]
             .value_counts(normalize=True).sort_index().round(3))
    cen["share"] = share.values
    print(f"\n===== {pos} =====")
    print(cen.to_string())

# COMMAND ----------

# ============================================================
# 15. 중심점 기반 스타일 라벨 자동 배치 및 검증
# ============================================================
# v8: objective_damage_per_min 을 STYLE_FEATURES 에서 뺐으므로 그 축에 기대던
#     프로토타입 4개를 제거했다.
#       TOP     스플릿 푸셔형   삭제
#       JUNGLE  오브젝트 중심형 삭제
#       MIDDLE  오브젝트 중심형 삭제
#       BOTTOM  오브젝트 중심형 삭제
#       UTILITY 오브젝트 중심형 → 이니시에이팅형 으로 대체
#     결과적으로 전 포지션이 프로토타입 3개이므로 chosen_k 도 3 이 상한이다.

from itertools import permutations
import numpy as np
import pandas as pd


STYLE_PROTOTYPES = {
    "TOP": {
        "딜 캐리형": (
            {"damage_share": 0.8, "gold_share": 0.6, "kill_participation": 0.5},
            "딜·자원 비중과 교전 관여도가 높은 캐리형",
        ),
        "앞라인 탱커형": (
            {"damage_taken_per_min": 1.5, "gold_share": -0.3, "damage_share": -0.2},
            "받은 피해가 크게 높고 자원·딜 비중은 낮은 앞라인형",
        ),
        "시야 운영형": (
            {"vision_score_per_min": 1.4, "gold_share": -0.3},
            "시야 기여가 높고 자원·딜 비중은 낮은 운영형",
        ),
    },

    "JUNGLE": {
        "캐리 정글러형": (
            {"damage_share": 0.8, "gold_share": 0.8, "kill_participation": 0.4},
            "딜·자원 비중과 교전 관여도가 높은 캐리형",
        ),
        "탱커 정글러형": (
            {"damage_taken_per_min": 1.5, "gold_share": -0.3},
            "받은 피해가 크게 높고 자원·딜 비중은 낮은 탱커형",
        ),
        "시야 운영형": (
            {"vision_score_per_min": 1.5, "gold_share": -0.2},
            "시야 기여가 높고 자원·딜 비중은 낮은 운영형",
        ),
    },

    "MIDDLE": {
        "딜 캐리형": (
            {"damage_share": 0.8, "gold_share": 0.6, "kill_participation": 0.5},
            "딜·자원 비중과 교전 관여도가 높은 캐리형",
        ),
        "전방 압박형": (
            {"damage_taken_per_min": 1.5, "gold_share": -0.2},
            "받은 피해가 크게 높고 자원·딜 비중은 낮은 압박형",
        ),
        "시야 운영형": (
            {"vision_score_per_min": 1.5, "gold_share": -0.3},
            "시야 기여가 높고 자원·딜 비중은 낮은 운영형",
        ),
    },

    "BOTTOM": {
        "전방 압박형": (
            {"damage_taken_per_min": 1.5, "gold_share": -0.2},
            "받은 피해가 높고 자원·딜 비중은 낮은 전방 압박형",
        ),
        "시야 운영형": (
            {"vision_score_per_min": 1.5, "gold_share": -0.2},
            "시야 기여가 높고 자원·딜 비중은 낮은 운영형",
        ),
        "딜 캐리형": (
            {"damage_share": 0.8, "gold_share": 0.7, "kill_participation": 0.5},
            "딜·자원 비중과 교전 관여도가 높은 캐리형",
        ),
    },

    "UTILITY": {
        "교전·시야 기여형": (
            {"kill_participation": 0.8, "vision_score_per_min": 0.8,
             "damage_share": -0.2, "gold_share": -0.2},
            "교전 합류와 시야 기여가 높고 딜·자원 비중은 낮은 서포터",
        ),
        "딜 서포터형": (
            {"damage_share": 0.9, "gold_share": 0.7, "vision_score_per_min": -0.4},
            "딜·자원 비중이 높지만 시야 기여는 낮은 서포터",
        ),
        "이니시에이팅형": (
            {"damage_taken_per_min": 1.3, "damage_share": -0.2, "gold_share": -0.2},
            "받은 피해가 높고 딜·자원 비중은 낮은 선제 진입형",
        ),
    },
}


# 프로토타입이 STYLE_FEATURES 에 없는 축을 참조하면 즉시 잡아낸다.
# (prototype_score 가 조용히 건너뛰면 점수가 0 으로 계산되어 오배정이 숨는다)
unknown = {
    (position, name, feature)
    for position, prototypes in STYLE_PROTOTYPES.items()
    for name, (weights, _) in prototypes.items()
    for feature in weights
    if feature not in STYLE_FEATURES
}
if unknown:
    raise ValueError(f"STYLE_FEATURES 에 없는 축을 참조하는 프로토타입: {sorted(unknown)}")


feature_index = {feature: index for index, feature in enumerate(STYLE_FEATURES)}


def prototype_score(center, weights):
    return float(
        sum(
            center[feature_index[feature]] * weight
            for feature, weight in weights.items()
            if feature in feature_index
        )
    )


def assign_style_labels(position, centers):
    prototypes = STYLE_PROTOTYPES[position]
    names = list(prototypes.keys())

    if len(centers) > len(names):
        raise ValueError(
            f"{position}: 군집 {len(centers)}개에 사용할 원형 라벨은 {len(names)}개뿐입니다. "
            f"chosen_k['{position}'] 을 {len(names)} 이하로 낮추세요."
        )

    best_total = -float("inf")
    best_assignment = None

    # 같은 포지션 안에서 라벨이 중복되지 않도록 일대일 배치
    for assignment in permutations(names, len(centers)):
        total = sum(
            prototype_score(center, prototypes[name][0])
            for center, name in zip(centers, assignment)
        )
        if total > best_total:
            best_total = total
            best_assignment = assignment

    labels = {}
    diagnostics = []

    for cluster, (center, name) in enumerate(zip(centers, best_assignment)):
        weights, description = prototypes[name]

        candidate_scores = sorted(
            [(candidate_name, prototype_score(center, candidate_value[0]))
             for candidate_name, candidate_value in prototypes.items()],
            key=lambda item: item[1],
            reverse=True,
        )
        assigned_score = prototype_score(center, weights)
        alternative_scores = [score for candidate_name, score in candidate_scores
                              if candidate_name != name]
        alternative = max(alternative_scores) if alternative_scores else assigned_score

        labels[cluster] = {"name": name, "desc": description}
        diagnostics.append({
            "team_position": position,
            "cluster": cluster,
            "label": name,
            "score": round(assigned_score, 3),
            "margin": round(assigned_score - alternative, 3),
            "dominant_feature": STYLE_FEATURES[int(np.argmax(np.abs(center)))],
        })

    return labels, diagnostics


STYLE_LABELS = {}
label_diagnostics = []

for position, model in style_models.items():
    labels, diagnostics = assign_style_labels(position, model.cluster_centers_)
    STYLE_LABELS[position] = labels
    label_diagnostics.extend(diagnostics)


label_check = pd.DataFrame(label_diagnostics)
display(label_check.sort_values(["team_position", "cluster"]))

# k 와 프로토타입 개수가 같으면 일대일 제약 때문에 모든 라벨이 반드시 쓰인다.
# 중심점이 그 라벨과 안 맞아도 남은 자리에 배정되므로 score 를 반드시 확인한다.
for position, model in style_models.items():
    if model.n_clusters == len(STYLE_PROTOTYPES[position]):
        weak = label_check[(label_check["team_position"] == position)
                           & (label_check["score"] < 0.3)]
        for _, row in weak.iterrows():
            print(f"[확인] {position} cluster {row['cluster']} → '{row['label']}' "
                  f"배정 점수 {row['score']} (지배축 {row['dominant_feature']}) "
                  f"— 라벨이 중심점을 설명하는지 직접 보세요.")

if (label_check["margin"] < -0.15).any():
    print("[경고] 일부 군집은 일대일 제약으로 개별 점수 2순위 라벨에 배치됐습니다.")
    display(label_check[label_check["margin"] < -0.15]
            .sort_values(["team_position", "cluster"]))


pdf["play_style"] = [
    STYLE_LABELS[position][int(cluster)]["name"]
    for position, cluster in zip(pdf["team_position"], pdf["cluster"])
]

mapping_check = pdf.groupby(["team_position", "cluster"])["play_style"].nunique()
if (mapping_check != 1).any():
    raise ValueError(f"스타일 매핑 불일치:\n{mapping_check[mapping_check != 1]}")

print(f"중심점 기반 라벨 배치 완료 — {len(mapping_check)}개 조합")

display(
    pdf.groupby(["team_position", "cluster", "play_style"])
    .size().reset_index(name="count")
    .sort_values(["team_position", "cluster"])
)


# COMMAND ----------

# MAGIC %md
# MAGIC ### 15-b. 군집 진단 — 이 군집을 신뢰해도 되는가
# MAGIC
# MAGIC | 지표 | 읽는 법 |
# MAGIC |---|---|
# MAGIC | 군집별 승률 | 폭이 0.10 을 넘으면 스타일이 아니라 승패로 갈린 것 |
# MAGIC | 애매경기비율 | 1·2순위 중심점 거리가 거의 같은 경기 비중. 20% 초과면 k 과다 |
# MAGIC | 실력누수 eta² | 0.15 초과면 군집이 실력 등급. 괄호 안은 파생 지표 포함 값 |
# MAGIC | 혼동 행렬 | 한 쌍이 0.8 을 넘으면 두 군집을 합칠 근거 |
# MAGIC
# MAGIC `resource_efficiency`(= damage_share / gold_share)와 `damage_per_min` 은 스타일 축으로
# MAGIC 만들어진 지표라 누수 판정에서 기본 제외합니다. 넣으면 순환 논리가 됩니다.

# COMMAND ----------

# ============================================================
# 15-b. 군집 진단 리포트
# ============================================================
cluster_reports = build_cluster_report(
    pdf=pdf,
    style_models=style_models,
    style_features=STYLE_FEATURES,
    perf_features=PERF_FEATURES,
    s_cols=S_COLS,
    style_labels=STYLE_LABELS,
    verbose=True,
)

cluster_overview = pd.concat(
    [report["cluster_profile"].assign(position=position)
     for position, report in cluster_reports.items()]
)
display(cluster_overview)

for position, report in cluster_reports.items():
    leak = report["skill_leak"]
    if leak["eta_squared"] is not None and leak["eta_squared"] > 0.15:
        print(f"[경고] {position}: 군집이 실력 등급에 가깝습니다 (eta^2={leak['eta_squared']})")
    if report["ambiguous_rate"] > 0.20:
        print(f"[경고] {position}: 경계 경기 {report['ambiguous_rate']:.1%} "
              f"→ chosen_k['{position}'] 을 1 줄이는 것을 검토")
    win_rates = report["cluster_profile"]["승률"].dropna()
    if len(win_rates) and (win_rates.max() - win_rates.min()) > 0.10:
        print(f"[경고] {position}: 군집별 승률 폭 {win_rates.max() - win_rates.min():.3f} "
              f"→ 스타일이 아니라 승패로 갈렸을 수 있습니다")

# COMMAND ----------

# ============================================================
# 15-c. 승패 교락 점검 + 지배축 규칙 비교
# ============================================================
win_mask = pdf["win"]
if str(win_mask.dtype) != "bool":
    win_mask = win_mask.astype(str).str.lower().isin(["true", "1", "1.0"])

print("[1] 스타일 축의 승패 민감도 — 절댓값이 클수록 결과 지표에 가깝다")
gap_rows = []
for feature in STYLE_FEATURES:
    col = f"z_{feature}"
    win_med = float(pdf.loc[win_mask, col].median())
    loss_med = float(pdf.loc[~win_mask, col].median())
    gap_rows.append({"feature": feature,
                     "승리_median_z": round(win_med, 3),
                     "패배_median_z": round(loss_med, 3),
                     "승패차": round(win_med - loss_med, 3)})
gap_df = pd.DataFrame(gap_rows)
gap_df["abs차"] = gap_df["승패차"].abs()
display(gap_df.sort_values("abs차", ascending=False).drop(columns="abs차"))

flagged = gap_df.loc[gap_df["abs차"] >= 0.40, "feature"].tolist()
if flagged:
    print(f"→ 승패차 0.40 이상: {flagged}")
    print("  스타일이 아니라 경기 결과를 반영합니다. STYLE_FEATURES 에서 제외를 검토하세요.")
    print("  (주의: 이 표는 원본 z 기준입니다. 09-b 잔차화는 s_ 열에만 적용됩니다)")

print("\n[2] K-Means 라벨 vs 지배축 규칙 일치율")
S_all = pdf[S_COLS].to_numpy(dtype=float)
game_axis = np.argmax(np.abs(S_all), axis=1)

agree_rows = []
for position, model in style_models.items():
    mask = (pdf["team_position"] == position).to_numpy()
    center_axis = np.array([int(np.argmax(np.abs(c))) for c in model.cluster_centers_])
    predicted_axis = center_axis[pdf.loc[mask, "cluster"].to_numpy()]
    agree_rows.append({
        "position": position,
        "k": int(model.n_clusters),
        "지배축_일치율": round(float((predicted_axis == game_axis[mask]).mean()), 3),
        "중심점_지배축": ", ".join(STYLE_FEATURES[i] for i in center_axis),
    })
agree_df = pd.DataFrame(agree_rows)
display(agree_df)

mean_agreement = agree_df["지배축_일치율"].mean()
print(f"평균 일치율 {mean_agreement:.1%}")
if mean_agreement >= 0.70:
    print("→ K-Means 가 사실상 '가장 튄 축 고르기'와 같습니다. 규칙으로 대체 가능합니다.")
else:
    print("→ K-Means 가 축 조합을 보고 있습니다. 단순 규칙으로 대체할 수 없습니다.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. 스타일별 성과 기준선
# MAGIC
# MAGIC 탱커형 유저에게 "딜량이 부족합니다"라고 코칭하면 안 됩니다. 스타일에 따라
# MAGIC 구조적으로 낮게 나오는 성과 지표가 있기 때문입니다.
# MAGIC
# MAGIC 각 (포지션, 군집) 안에서 성과 지표의 중앙값을 구해 두고, 그 값이 `-0.3` 아래인 지표는
# MAGIC 포지션 기준 대신 **같은 스타일 안에서** 다시 평가합니다. 수동 예외 목록을 관리할 필요 없이
# MAGIC 데이터에서 자동으로 잡힙니다.

# COMMAND ----------

# ============================================================
# 16. 스타일별 성과 기준선 산출
# ============================================================
style_baseline = {}

for pos in style_models:
    style_baseline[pos] = {}
    sub = pdf[pdf["team_position"] == pos]
    for c, g in sub.groupby("cluster"):
        med = {f: float(g[f"z_{f}"].median()) for f in PERF_FEATURES}
        # 스타일 내부 분산도 저장 — 스타일 안에서 다시 Z 를 뜰 때 사용
        sig = {}
        for f in PERF_FEATURES:
            iqr = g[f"z_{f}"].quantile(0.75) - g[f"z_{f}"].quantile(0.25)
            sig[f] = float(iqr / 1.349) if iqr > 0 else 1.0
        style_baseline[pos][int(c)] = {"median": med, "sigma": sig}

# 구조적으로 억눌린 지표 확인
for pos, cl in style_baseline.items():
    for c, v in cl.items():
        low = [f for f, m in v["median"].items() if m < -STYLE_BASELINE_CUT]
        if low:
            label = STYLE_LABELS[pos][c]["name"]
            print(f"{pos} / {label}: 구조적으로 낮은 지표 → {low}")

# COMMAND ----------

# MAGIC %md
# MAGIC ### 16-b / 16-c. 기준선 분포와 컷 자동 산출  *(v9 신규)*
# MAGIC
# MAGIC **현상**: Cell 24 의 `finding_tag` 가 전부 `neutral`, Cell 16 의 "구조적으로 낮은 지표"도 비어 있다.
# MAGIC
# MAGIC **원인**: 태그는 군집별 성과 중앙값 `b` 와 `STYLE_BASELINE_CUT` 의 비교로만 결정된다.
# MAGIC
# MAGIC ```
# MAGIC b >  CUT  → core_strength / core_weakness
# MAGIC b < -CUT  → structural / below_style
# MAGIC 그 사이     → neutral
# MAGIC ```
# MAGIC
# MAGIC 15-b 진단표의 군집별 성과 중앙값은 -0.29 ~ +0.34 범위인데 컷이 0.30 이라
# MAGIC **넘는 조합이 거의 없다**. 09-b 잔차화로 스타일 축에서 실력 성분을 빼면서 군집 간
# MAGIC 성과 차이도 함께 줄어든 부작용이다.
# MAGIC
# MAGIC 컷을 감으로 낮추면 표본 잡음이 "구조적 특성"으로 둔갑한다. 그래서 두 가지를 같이 본다.
# MAGIC
# MAGIC 1. **실제 분포** — 분위수 p10/p25/p50/p75/p90
# MAGIC 2. **잡음 바닥** — 군집 중앙값 자체의 표준오차 `1.2533 × σ / √n`. 컷이 이보다 낮으면
# MAGIC    표본이 흔들린 것을 구조로 읽는 것이다.

# COMMAND ----------

# ============================================================
# 16-b. 스타일 기준선 분포 (v9)
# ============================================================
# 컷을 정하려면 먼저 "무엇을 자르는지" 의 분포를 봐야 한다.

baseline_rows = []
for position, clusters in style_baseline.items():
    cluster_sizes = pdf[pdf["team_position"] == position]["cluster"].value_counts()
    for cluster, value in clusters.items():
        n = int(cluster_sizes.get(cluster, 0))
        for feature in PERF_FEATURES:
            median = float(value["median"][feature])
            sigma = float(value["sigma"][feature])
            # 중앙값의 표준오차 ≈ 1.2533 × σ / √n (정규 근사).
            # 이 값의 몇 배 아래로 컷을 내리면 잡음을 구조로 읽게 된다.
            standard_error = 1.2533 * sigma / max(np.sqrt(n), 1.0)
            baseline_rows.append({
                "position": position,
                "cluster": int(cluster),
                "style": STYLE_LABELS[position][int(cluster)]["name"],
                "feature": feature,
                "median_z": round(median, 3),
                "abs_median": round(abs(median), 3),
                "n": n,
                "median_se": round(standard_error, 4),
            })

baseline_dist = pd.DataFrame(baseline_rows)
print(f"군집·지표 조합: {len(baseline_dist)}개 "
      f"({baseline_dist['position'].nunique()}포지션 × 군집 × {len(PERF_FEATURES)}지표)")

QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
distribution = pd.DataFrame({
    "부호 있는 median_z": baseline_dist["median_z"].quantile(QUANTILES).round(3).to_numpy(),
    "절댓값 |median_z|": baseline_dist["abs_median"].quantile(QUANTILES).round(3).to_numpy(),
}, index=[f"p{int(q * 100)}" for q in QUANTILES])

print("\n[군집별 성과 중앙값 분포]")
print(distribution.to_string())
print(f"\n범위: {baseline_dist['median_z'].min():+.3f} ~ {baseline_dist['median_z'].max():+.3f}")
print(f"중앙값 표준오차(잡음 바닥) p50={baseline_dist['median_se'].median():.4f} "
      f"/ p90={baseline_dist['median_se'].quantile(0.90):.4f}")

current_share = float((baseline_dist["abs_median"] > STYLE_BASELINE_CUT).mean())
print(f"\n현재 STYLE_BASELINE_CUT = {STYLE_BASELINE_CUT:.2f} "
      f"→ 컷을 넘는 조합 {current_share:.1%} "
      f"({int((baseline_dist['abs_median'] > STYLE_BASELINE_CUT).sum())}/{len(baseline_dist)}개)")

print("\n[지표별 |median_z| — 어떤 성과 지표가 스타일을 타는가]")
print(baseline_dist.groupby("feature")["abs_median"]
      .agg(["median", "max"]).round(3)
      .sort_values("median", ascending=False).to_string())

display(baseline_dist.sort_values("abs_median", ascending=False).head(20))

# COMMAND ----------

# ============================================================
# 16-c. STYLE_BASELINE_CUT 권고값 자동 계산 (v9)
# ============================================================
# 목표: 군집·지표 조합의 15~25% 가 core_strength / core_weakness / structural /
#       below_style 중 하나로 분류되는 수준.
#
# 주의: 여기서 세는 것은 "그 조합이 neutral 이 아닐 자격을 갖는가" 이다.
#       core_strength 와 core_weakness 의 구분, structural 과 below_style 의 구분은
#       유저의 raw_median 에 달려 있어 학습 시점에는 정할 수 없다.
#       컷이 직접 통제하는 값은 이 '분류 자격 비율' 이므로 이 값으로 정한다.

TAG_TARGET_LOW, TAG_TARGET_HIGH = 0.15, 0.25
TAG_TARGET_MID = (TAG_TARGET_LOW + TAG_TARGET_HIGH) / 2
NOISE_SIGMA_MULTIPLE = 3.0      # 잡음 바닥 = 중앙값 표준오차 p90 의 3배

cut_grid = np.round(np.arange(0.05, 0.61, 0.01), 2)
cut_rows = []
for cut in cut_grid:
    core = float((baseline_dist["median_z"] > cut).mean())        # core_* 후보
    structural = float((baseline_dist["median_z"] < -cut).mean()) # structural/below_style 후보
    cut_rows.append({
        "cut": cut,
        "core_비율": round(core, 3),
        "structural_비율": round(structural, 3),
        "분류_비율": round(core + structural, 3),
        "목표범위": TAG_TARGET_LOW <= core + structural <= TAG_TARGET_HIGH,
    })
cut_table = pd.DataFrame(cut_rows)

print("[컷별 분류 비율] (0.05 간격만 표시)")
print(cut_table[(cut_table["cut"] * 100).round().astype(int) % 5 == 0].to_string(index=False))

# ------------------------------------------------------------
# 권고값 = max(목표 비율을 만드는 분위수, 잡음 바닥)
# ------------------------------------------------------------
quantile_cut = float(baseline_dist["abs_median"].quantile(1 - TAG_TARGET_MID))
noise_floor = float(NOISE_SIGMA_MULTIPLE * baseline_dist["median_se"].quantile(0.90))
recommended_cut = round(max(quantile_cut, noise_floor), 2)

achieved = float((baseline_dist["abs_median"] > recommended_cut).mean())
achieved_n = int((baseline_dist["abs_median"] > recommended_cut).sum())

print(f"\n목표 {TAG_TARGET_LOW:.0%}~{TAG_TARGET_HIGH:.0%} 분류를 만드는 컷 "
      f"(|median_z| 의 p{int((1 - TAG_TARGET_MID) * 100)}): {quantile_cut:.3f}")
print(f"잡음 바닥 (중앙값 표준오차 p90 × {NOISE_SIGMA_MULTIPLE:.0f}): {noise_floor:.3f}")

if noise_floor > quantile_cut:
    print("\n[경고] 목표 비율을 맞추려면 잡음 바닥보다 낮은 컷이 필요합니다.")
    print("       군집 간 성과 차이가 실제로 작다는 뜻이므로, 컷을 더 내리는 대신")
    print("       잡음 바닥을 권고값으로 씁니다. 분류 비율은 목표보다 낮게 나옵니다.")
    print("       (이 경우 STYLE_FEATURES 재검토나 k 축소가 더 본질적인 해법입니다)")

print("\n" + "=" * 62)
print(f"  권고: STYLE_BASELINE_CUT = {recommended_cut:.2f}")
print(f"        (현재 {STYLE_BASELINE_CUT:.2f} → 분류 비율 {current_share:.1%} "
      f"→ {achieved:.1%}, {achieved_n}/{len(baseline_dist)}개 조합)")
print("=" * 62)

in_band = cut_table[cut_table["목표범위"]]
if len(in_band):
    print(f"목표 범위를 만족하는 컷 구간: {in_band['cut'].min():.2f} ~ {in_band['cut'].max():.2f}")
else:
    print("목표 범위(15~25%)를 정확히 만족하는 컷이 없습니다. 위 표에서 직접 고르세요.")

print("\n[권고 컷에서 분류될 조합]")
flagged = baseline_dist[baseline_dist["abs_median"] > recommended_cut].copy()
flagged["예상_태그군"] = np.where(flagged["median_z"] > 0,
                                "core_strength / core_weakness",
                                "structural / below_style")
if len(flagged):
    display(flagged.sort_values("abs_median", ascending=False)
            [["position", "style", "feature", "median_z", "median_se", "n", "예상_태그군"]])
else:
    print("없음 — 이 컷에서는 여전히 전부 neutral 입니다.")

# ------------------------------------------------------------
# 반영 방법
# ------------------------------------------------------------
print("\n" + "-" * 62)
print("[Cell 00 설정에 반영하는 방법]")
print("  Cell 00 의 STYLE_BASELINE_CUT 줄을 아래로 바꾼다.")
print()
print(f"      STYLE_BASELINE_CUT = {recommended_cut:.2f}   "
      f"# 16-c 권고 (분류 비율 {achieved:.0%}, 잡음 바닥 {noise_floor:.3f})")
print()
print("  STYLE_BASELINE_CUT 은 style_model.json 의 config 에도 들어간다.")
print("  → 값을 바꾸면 Cell 05 부터 재실행해야 JSON 과 추론 결과에 반영된다.")
print("     (최소로는 Cell 18 만 다시 돌려도 JSON 은 갱신되지만, 기준 통계·군집·기준선까지")
print("      같은 설정으로 만들어진 것을 보장하려면 Cell 05 부터 전체를 다시 돌린다)")
print("  → 재실행 후 Cell 24 의 finding_tag 와 Cell 16 의 '구조적으로 낮은 지표' 를 확인한다.")
print("-" * 62)

# COMMAND ----------

# ============================================================
# 17. gold/play_style_clusters 저장
# ============================================================
out_cols = (
    ["match_id", "player_id", "game_start_datetime", "team_position", "tier_bucket",
     "champion_name", "win", "cluster", "play_style"] + Z_ALL + S_COLS
)
(
    spark.createDataFrame(pdf[out_cols])
    .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    .save(PATH_CLUSTERS)
)

# 스타일 정의 테이블 (서비스 화면·프롬프트에서 참조)
def_rows = [
    {
        "team_position": pos, "cluster": int(c),
        "style_name": v["name"], "style_description": v["desc"],
        "centroid": json.dumps(dict(zip(STYLE_FEATURES,
                     np.round(style_models[pos].cluster_centers_[c], 4).tolist())), ensure_ascii=False),
        "share": float((pdf[(pdf.team_position == pos)]["cluster"] == c).mean()),
    }
    for pos, cl in STYLE_LABELS.items() if pos in style_models
    for c, v in cl.items() if c < style_models[pos].n_clusters
]
(
    spark.createDataFrame(pd.DataFrame(def_rows))
    .write.format("delta").mode("overwrite").option("overwriteSchema", "true")
    .save(PATH_STYLE_DEF)
)
print("클러스터 결과 및 스타일 정의 저장 완료")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. 서빙 아티팩트 export
# MAGIC
# MAGIC 기존 노트북은 Spark ML 의 `StandardScalerModel` 과 `KMeansModel` 을 저장하고,
# MAGIC 추론할 때 `assembler → scaler → kmeans` 를 Spark DataFrame 으로 태웠습니다.
# MAGIC 사용자 1명당 10행을 처리하려고 Spark 를 띄우는 구조라, 웹 요청당 수십 초가 걸립니다.
# MAGIC
# MAGIC K-Means 예측은 중심점과의 거리 계산일 뿐이므로 NumPy 만 있으면 됩니다.
# MAGIC 기준 통계·중심점·신뢰도·기준선을 **JSON 하나**로 내보내면 FastAPI 프로세스에
# MAGIC 그대로 올릴 수 있습니다. Databricks 는 학습까지만 담당하게 됩니다.

# COMMAND ----------

# ============================================================
# 18. 서빙용 JSON 아티팩트
# ============================================================
k_metrics = {
    position: {
        key: float(row[key]) if key != "k" else int(row[key])
        for key in ["k", "silhouette", "min_share", "max_share", "ari_mean", "ari_min", "inertia"]
    }
    for position, k in chosen_k.items()
    for _, row in k_search[position][k_search[position]["k"] == k].iterrows()
}

serving = {
    "version": pd.Timestamp.utcnow().strftime("%Y%m%d_%H%M%S"),
    "artifact_type": "explainable_playstyle",
    "authoritative_source": "ml-training/04_ml_training_v8.py",
    "analysis_games": ANALYSIS_GAMES,
    "queue_filter": 420,
    "config": {
        "style_features": STYLE_FEATURES,
        "perf_features": PERF_FEATURES,
        "perf_families": PERF_FAMILIES,
        "z_clip": Z_CLIP,
        "analysis_games": ANALYSIS_GAMES,
        "min_games": MIN_GAMES,
        "min_position_games": MIN_POSITION_GAMES,
        "min_duration": MIN_DURATION,
        "strength_threshold": STRENGTH_THRESHOLD,
        "improvement_threshold": IMPROVEMENT_THRESHOLD,
        "always_show_strength": ALWAYS_SHOW_STRENGTH,
        "always_show_improvement": ALWAYS_SHOW_IMPROVEMENT,
        "min_reliability": MIN_RELIABILITY,
        "low_reliability_mode": LOW_RELIABILITY_MODE,
        "trend_threshold": TREND_THRESHOLD,
        "trend_min_games": TREND_MIN_GAMES,
        "dominant_share": DOMINANT_SHARE,
        "mixed_share": MIXED_SHARE,
        "mixed_top2": MIXED_TOP2,
        "style_baseline_cut": STYLE_BASELINE_CUT,
        "has_tier": bool(HAS_TIER),
        "queue_filter": 420,
    },
    "model_diagnostics": {
        "chosen_k": chosen_k,
        "k_metrics": k_metrics,
        "tier_coverage": round(float(tier_coverage), 4),
        "label_assignment": label_diagnostics,
    },
    "reliability": {key: round(value, 4) for key, value in reliability.items()},
    "stats": {
        f"{position}|{bucket}": {
            row["feature"]: {"center": float(row["center"]), "sigma": float(row["sigma"])}
            for _, row in group.iterrows()
        }
        for (position, bucket), group in stats_group.groupby(GROUP_KEYS)
    },
    "fallback_stats": {
        position: {
            row["feature"]: {"center": float(row["center"]), "sigma": float(row["sigma"])}
            for _, row in group.iterrows()
        }
        for position, group in stats_pos.groupby("team_position")
    },
    "centroids": {
        position: np.round(model.cluster_centers_, 6).tolist()
        for position, model in style_models.items()
    },
    "labels": {
        position: {str(cluster): value for cluster, value in labels.items()}
        for position, labels in STYLE_LABELS.items()
    },
    "style_baseline": {
        position: {str(cluster): value for cluster, value in clusters.items()}
        for position, clusters in style_baseline.items()
    },
    "feature_labels": {
        "kda": "KDA", "kill_participation": "전투 관여도",
        "cs_per_min": "CS 수급", "gold_per_min": "골드 수급",
        "damage_per_min": "챔피언 피해량", "damage_taken_per_min": "피해 감수",
        "vision_score_per_min": "시야 기여도", "objective_damage_per_min": "오브젝트 기여도",
        "damage_share": "팀 내 딜 기여도", "gold_share": "팀 내 골드 비중",
        "resource_efficiency": "자원 대비 딜 효율",
        "vision_wards_bought_in_game": "제어 와드 활용",
    },
}

missing_labels = [
    feature for feature in PERF_FEATURES + STYLE_FEATURES
    if feature not in serving["feature_labels"]
]
if missing_labels:
    raise ValueError(f"feature_labels 누락: {missing_labels}")

# 군집 배정 근거·신뢰도 계산용 (없으면 추론이 폴백값으로 돈다)
serving["explain"] = build_explain_artifact(cluster_reports, STYLE_FEATURES)

# 추론 시에도 학습과 동일한 잔차화를 해야 하므로 회귀 계수를 함께 내보낸다
#   residual      : 09-b 스타일 축 (design = [1, win, perf_level])
#   perf_residual : 09-c 성과 축   (design = [1, win])
serving["residual"] = {
    "controls": RESIDUAL_CONTROLS,
    "perf_features": PERF_FEATURES,
    "positions": {
        position: {"beta": np.round(beta, 6).tolist()}
        for position, beta in residual_models.items()
    },
}

# 09-c 를 건너뛰고 이 셀만 돌린 경우에도 JSON 구조는 유지한다 (빈 블록 = 변환 없음).
_perf_residual_features = globals().get("perf_residual_features", [])
_perf_residual_models = globals().get("perf_residual_models", {})

serving["perf_residual"] = {
    "controls": ["win"],
    # beta 의 열 순서가 이 리스트 순서다. 추론 쪽은 이 순서로만 읽어야 한다.
    "features": list(_perf_residual_features),
    "detect_cut": float(globals().get("PERF_RESIDUAL_CUT", 0.40)),
    "positions": {
        position: {"beta": np.round(beta, 6).tolist()}
        for position, beta in _perf_residual_models.items()
    },
}

# 추론 쪽이 z 를 클리핑한 뒤에 잔차를 빼야 학습과 순서가 같다. 그 사실을 명시해 둔다.
serving["perf_residual"]["apply_order"] = "clip_then_residual"

if not _perf_residual_features:
    print("[알림] perf_residual 이 비어 있습니다 (09-c 미실행 또는 탐지된 축 없음).")

payload = json.dumps(serving, ensure_ascii=False, indent=2)
dbutils.fs.put(PATH_SERVING, payload, overwrite=True)
print(f"서빙 아티팩트 저장: {PATH_SERVING} ({len(payload) / 1024:.1f} KB)")
print("explain 포지션:", len(serving["explain"]["positions"]),
      "/ style residual 포지션:", len(serving["residual"]["positions"]),
      "/ perf residual 포지션:", len(serving["perf_residual"]["positions"]))
print("perf_residual 대상 축:", serving["perf_residual"]["features"] or "없음")
print("FastAPI에는 style_model.json, playstyle_analyzer.py, playstyle_explain_v2.py 를 복사합니다.")


# COMMAND ----------

# ============================================================
# 18-b. 서빙 아티팩트 점검 (v9)
# ============================================================
# 방금 저장한 style_model.json 을 "다시 읽어" 내용을 확인한다.
# 메모리의 serving 변수가 아니라 실제 파일을 보는 것이 요점이다 —
# 저장이 실패했거나 옛 파일이 남아 있어도 serving 변수는 멀쩡하기 때문이다.
# FastAPI 로 복사하기 전에 이 셀이 [정상] 을 찍어야 한다.

import json

# dbutils.fs.head 는 크기 제한이 있어 파일이 잘린다. 전체를 읽는다.
raw = spark.read.text(PATH_SERVING, wholetext=True).collect()[0]["value"]
saved = json.loads(raw)


print(f"경로: {PATH_SERVING}")
print(f"크기: {len(raw) / 1024:.1f} KB / version: {saved['version']}")
print(f"최상위 키: {sorted(saved)}\n")

cfg = saved["config"]
print("[config — 추론이 그대로 읽는 값]")
for key in ("analysis_games", "min_games", "min_position_games", "z_clip",
            "min_reliability", "low_reliability_mode", "style_baseline_cut",
            "strength_threshold", "improvement_threshold", "dominant_share", "has_tier"):
    print(f"  {key:22s} {cfg.get(key)}")

tier_buckets = sorted({k.split("|")[1] for k in saved["stats"]})
print(f"\n[기준 통계] {len(saved['stats'])}개 (포지션 × 티어버킷)")
print(f"  티어 버킷: {tier_buckets}")
print(f"  폴백(포지션 전체): {len(saved['fallback_stats'])}개")

print(f"\n[군집] 포지션 {len(saved['centroids'])}개")
for position in sorted(saved["centroids"]):
    names = [v["name"] for v in saved["labels"][position].values()]
    print(f"  {position:8s} k={len(saved['centroids'][position])}  {names}")

print(f"\n[잔차화]")
print(f"  스타일 축(09-b) 포지션 {len(saved['residual']['positions'])}개 "
      f"/ 통제 {saved['residual']['controls']}")
perf = saved.get("perf_residual", {})
print(f"  성과 축(09-c) 포지션 {len(perf.get('positions', {}))}개 "
      f"/ 통제 {perf.get('controls')} / 대상 {perf.get('features') or '없음'}")

print(f"\n[신뢰도]  문턱 {cfg.get('min_reliability')} / 모드 {cfg.get('low_reliability_mode')}")
for feature, value in sorted(saved["reliability"].items(), key=lambda x: -x[1]):
    if feature in cfg["perf_features"]:
        mark = "" if value >= float(cfg.get("min_reliability", 0)) else "  ← 미달"
        print(f"  {feature:26s} {value:.3f}{mark}")

# ------------------------------------------------------------
problems = []
if not saved.get("explain", {}).get("positions"):
    problems.append("explain 블록이 비었다 — 추론 신뢰도가 폴백값으로 돈다 (15-b 를 먼저 실행)")
if not saved["residual"]["positions"]:
    problems.append("residual 이 비었다 — 09-b 를 실행하지 않았다")
if not perf.get("features"):
    problems.append("perf_residual 이 비었다 — 09-c 미실행이거나 탐지된 축이 없다")
if tier_buckets == ["ALL"]:
    problems.append("티어 버킷이 ALL 뿐이다 — 01 의 티어 전파가 반영되지 않았다")
if not cfg.get("has_tier"):
    problems.append("has_tier=False — silver 에 tier 가 없다")

print("\n" + "-" * 58)
if problems:
    print("[확인 필요]")
    for p in problems:
        print(f"  - {p}")
else:
    print("[정상] FastAPI 로 복사해도 되는 상태입니다.")
print("-" * 58)


# COMMAND ----------

# ============================================================
# 19. MLflow 로깅  (선택)
# ============================================================
# 서버리스에서 느리고 circular import 가 자주 나서 기본 OFF 입니다.
# 실험 비교가 필요할 때만 True 로 바꾸세요. 모델 학습에는 영향이 없습니다.
USE_MLFLOW = False

if not USE_MLFLOW:
    print("MLflow 로깅 건너뜀 (USE_MLFLOW=False)")
else:
    import mlflow, tempfile, os

    mlflow.set_experiment("/Shared/lol_insight_coach_play_style")

    with mlflow.start_run(run_name=f"style_v{serving['version']}") as run:
        mlflow.log_params({
            "n_style_features": len(STYLE_FEATURES),
            "n_perf_features": len(PERF_FEATURES),
            "z_clip": Z_CLIP,
            "profile_centering": True,
            "robust_z": True,
            "per_position_model": True,
            "has_tier": HAS_TIER,
            "chosen_k": json.dumps(chosen_k),
            "n_rows": len(pdf),
        })
        for pos, res in k_search.items():
            k = chosen_k[pos]
            row = res[res["k"] == k].iloc[0]
            mlflow.log_metrics({
                f"{pos}_k": k,
                f"{pos}_silhouette": row["silhouette"],
                f"{pos}_ari_mean": row["ari_mean"],
                f"{pos}_min_share": row["min_share"],
            })
        for f, r in reliability.items():
            mlflow.log_metric(f"reliability_{f}", r)

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "style_model.json")
            with open(p, "w", encoding="utf-8") as fp:
                fp.write(payload)
            mlflow.log_artifact(p)
            icc_pdf.to_csv(os.path.join(d, "reliability.csv"), index=False)
            mlflow.log_artifact(os.path.join(d, "reliability.csv"))

        print("MLflow run:", run.info.run_id)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. 추론 로직 — 순수 Python
# MAGIC
# MAGIC 여기부터는 Spark 를 쓰지 않습니다. 아래 `PlayStyleAnalyzer` 클래스를 그대로 복사해
# MAGIC FastAPI 모듈로 옮기면 됩니다. 입력은 경기 10개짜리 `list[dict]` 하나입니다.

# COMMAND ----------

# ============================================================
# 20. PlayStyleAnalyzer — FastAPI 로 그대로 이식 가능
# ============================================================
class PlayStyleAnalyzer:
    """서빙 JSON 하나만 있으면 동작하는 추론기. Spark 의존 없음."""

    def __init__(self, artifact: dict):
        self.a = artifact
        self.cfg = artifact["config"]
        self.style_features = self.cfg["style_features"]
        self.perf_features = self.cfg["perf_features"]
        self.centroids = {p: np.array(v) for p, v in artifact["centroids"].items()}

    # ---------- 기본 변환 ----------
    def _stats(self, position, tier_bucket):
        key = f"{position}|{tier_bucket}"
        if key in self.a["stats"]:
            return self.a["stats"][key]
        return self.a["fallback_stats"].get(position)

    def _z(self, game: dict) -> dict:
        st = self._stats(game["team_position"], game.get("tier_bucket", "ALL"))
        if st is None:
            return {}
        clip = self.cfg["z_clip"]
        out = {}
        for f, s in st.items():
            v = game.get(f)
            if v is None:
                continue
            z = (v - s["center"]) / s["sigma"]
            out[f] = max(-clip, min(clip, z))
        return out

    # ---------- 성과 축 승패 잔차화 (학습 09-c 와 동일) ----------
    @staticmethod
    def _win_value(game: dict) -> float:
        value = game.get("win")
        if isinstance(value, bool):
            return float(value)
        return 1.0 if str(value).strip().lower() in ("true", "1", "1.0", "win", "w") else 0.0

    def _perf_residual_delta(self, game: dict) -> dict:
        """z 에서 빼야 할 승패 성분. 학습 09-c 의 design @ beta 와 같은 값이다.

        design = [1, win] 이고 beta 는 (2, 축 수). 축 순서는 features 리스트 순서.
        블록이 없으면(구버전 아티팩트) 빈 dict 를 돌려 아무 변환도 하지 않는다.
        """
        block = self.a.get("perf_residual") or {}
        features = block.get("features") or []
        entry = (block.get("positions") or {}).get(game.get("team_position"))
        if not features or not entry:
            return {}
        beta = np.asarray(entry["beta"], dtype=float)
        design = np.array([1.0, self._win_value(game)], dtype=float)
        delta = design @ beta
        return {feature: float(delta[i]) for i, feature in enumerate(features)}

    def style_vector(self, z: dict, game: dict = None, position: str = None):
        """학습과 동일한 스타일 벡터: 프로파일 센터링(09) + 승패·실력 잔차화(09-b).

        잔차화를 학습에서만 하고 추론에서 빼먹으면 중심점과 다른 좌표계에서 거리를
        재게 되어 경계 경기의 군집이 조용히 뒤바뀐다. 그러면 틀린 스타일 기준선이 붙어
        finding_tag 까지 어긋난다.
        """
        base = (game or {}).get("z_raw") or z
        vec = np.array([base.get(f, 0.0) for f in self.style_features], dtype=float)
        vec = vec - vec.mean()                      # 학습과 동일한 프로파일 센터링

        residual = self.a.get("residual")
        positions = (residual or {}).get("positions") or {}
        if position is None and game is not None:
            position = game.get("team_position")
        if not residual or game is None or position not in positions:
            return vec

        beta = np.asarray(positions[position]["beta"], dtype=float)
        perf_features = residual.get("perf_features") or self.perf_features
        values = [base[f] for f in perf_features if f in base]
        design = np.array([
            1.0,
            self._win_value(game),
            float(np.mean(values)) if values else 0.0,
        ], dtype=float)
        vec = vec - design @ beta
        return vec - vec.mean()                     # 합이 0 인 제약 복원

    def _assign_style(self, position, z: dict, game: dict = None):
        """가장 가까운 중심점과, 전체 중심점까지의 거리를 함께 반환합니다.
        거리는 대표 스타일 동률을 결정적으로 깨는 데 사용합니다."""
        if position not in self.centroids or not z:
            return None, None
        vec = self.style_vector(z, game, position)
        d = np.linalg.norm(self.centroids[position] - vec, axis=1)
        return int(d.argmin()), {int(i): float(v) for i, v in enumerate(d)}

    # ---------- 경기 단위 ----------
    def analyze_games(self, games: list) -> list:
        rows = []
        for g in games:
            # z_raw : 클리핑까지만 끝난 원본. 스타일 축 계산은 학습과 같이 이 값에서 출발한다.
            # z     : 성과 축에서 승패 성분을 뺀 값. 강점/개선점·추세는 이걸 쓴다.
            z_raw = self._z(g)
            z = dict(z_raw)
            for feature, delta in self._perf_residual_delta(g).items():
                if feature in z:
                    z[feature] = z[feature] - delta
            row = {**g, "z": z, "z_raw": z_raw}
            c, dist = self._assign_style(g["team_position"], z_raw, row)
            row.update(cluster=c, dist=dist)
            rows.append(row)
        return rows

    # ---------- 대표 스타일 ----------
    def dominant_style(self, rows: list, main_position: str) -> dict:
        sub = [r for r in rows
               if r["team_position"] == main_position and r["cluster"] is not None]

        if len(sub) < self.cfg["min_position_games"]:
            return {
                "status": "insufficient", "games": len(sub), "styles": [],
                "consistency": None, "distribution": [],
                "message": f"{main_position} 경기가 {len(sub)}판이라 대표 스타일을 판정할 수 없습니다.",
            }

        from collections import Counter
        cnt = Counter(r["cluster"] for r in sub)
        total = sum(cnt.values())

        # 동률은 클러스터 번호가 아니라 중심점까지의 평균 거리로 깹니다.
        # (기존 코드처럼 동률 전부를 반환하면 모순된 스타일 쌍이 그대로 나갑니다)
        def tiebreak(c):
            ds = [r["dist"][c] for r in sub if r["dist"] and c in r["dist"]]
            return (-cnt[c], float(np.mean(ds)) if ds else 0.0)

        ranked = sorted(cnt.keys(), key=tiebreak)
        top_share = cnt[ranked[0]] / total
        top2_share = sum(cnt[c] for c in ranked[:2]) / total
        second_share = cnt[ranked[1]] / total if len(ranked) > 1 else 0.0

        label = lambda c: self.a["labels"][main_position][str(c)]

        if top_share >= self.cfg["dominant_share"]:
            status, picked = "single", ranked[:1]
        elif second_share >= self.cfg["mixed_share"] and top2_share >= self.cfg["mixed_top2"]:
            # 두 스타일이 실제로 지배적일 때만 mixed. k 가 커도 정상 동작합니다.
            status, picked = "mixed", ranked[:2]
        else:
            # 스타일이 흩어진 것 자체가 유효한 진단입니다
            status, picked = "unstable", ranked[:2]

        return {
            "status": status,
            "games": len(sub),
            "consistency": round(top_share, 2),
            "styles": [
                {"cluster": c, "ratio": round(100 * cnt[c] / total, 1), **label(c)}
                for c in picked
            ],
            "distribution": [
                {"style": label(c)["name"], "count": cnt[c],
                 "ratio": round(100 * cnt[c] / total, 1)}
                for c in ranked
            ],
        }

# COMMAND ----------

# ============================================================
# 21. 강점 / 개선점  (+ finding_tag)
# ============================================================
# 기존: Z 평균을 정렬해 상위 3 / 하위 3 을 무조건 추출
#       → 평균 실력 유저도 순전히 노이즈로 ±0.45 짜리 "강점/개선점"을 받음
#       → 실제 테스트에서 개선점 3개가 전부 시야 계열로 채워짐
#
# 수정 5단계
#   (1) 평균 대신 중앙값        : 이상치 1판이 평균을 크게 움직임
#   (2) 신뢰도 수축             : z_adj = median * reliability
#   (3) 스타일 기준선 보정       : 그 스타일에서 구조적으로 낮은 지표는 스타일 내부에서 재평가
#   (4) finding_tag 분류        : structural 은 개선점에서 아예 제외
#   (5) 계열 중복 제거 + 임계값  : 같은 계열 1개만, 강점/개선점 문턱을 분리
#
# finding_tag 의미
#   core_strength : 이 스타일의 핵심 축이고 실제로 높음      → 강점으로 노출
#   core_weakness : 이 스타일의 핵심 축인데 낮음            → 최우선 개선 목표
#   below_style   : 스타일상 낮은 지표인데 같은 스타일 중에서도 낮음
#   structural    : 스타일상 원래 낮은 지표 (탱커의 딜량 등) → 개선점에서 제외
#   neutral       : 스타일과 무관한 지표

def _build_profile(self, rows: list, main_position: str, main_cluster):
    rel = self.a["reliability"]
    cut = self.cfg["style_baseline_cut"]
    clip = self.cfg["z_clip"]
    # 신뢰도 하한. config 에 없는 구버전 아티팩트에서는 0.0 = 필터 없음(기존 동작 유지).
    min_rel = float(self.cfg.get("min_reliability", 0.0))
    low_mode = str(self.cfg.get("low_reliability_mode", "exclude")).lower()
    if low_mode not in ("exclude", "flag"):
        low_mode = "exclude"

    base = None
    if main_cluster is not None:
        base = self.a["style_baseline"].get(main_position, {}).get(str(main_cluster))

    scores, detail = {}, {}
    for f in self.perf_features:
        vals = [r["z"][f] for r in rows if f in r["z"]]
        if len(vals) < self.cfg["min_games"]:
            continue

        raw_med = float(np.median(vals))
        med, corrected, tag = raw_med, False, "neutral"
        b = base["median"].get(f, 0.0) if base else 0.0

        if b < -cut:
            # 스타일상 구조적으로 낮은 지표 → 같은 스타일 안에서 다시 평가
            sig = max(base["sigma"].get(f, 1.0), 1e-6)
            med = max(-clip, min(clip, (raw_med - b) / sig))
            corrected = True
            tag = "below_style" if med <= -0.5 else "structural"
        elif b > cut:
            # 이 스타일의 핵심 축
            tag = "core_weakness" if raw_med < 0 else "core_strength"

        f_rel = float(rel.get(f, 1.0))
        adj = med * f_rel
        scores[f] = adj
        detail[f] = {
            "median_z": round(med, 3),
            "raw_median_z": round(raw_med, 3),
            "reliability": round(f_rel, 3),
            "adjusted": round(adj, 3),
            "style_corrected": corrected,
            "finding_tag": tag,
            # (6) 신뢰도 하한 — 분석 창 경기 수로 판단하기엔 편차가 큰 지표
            "low_reliability": f_rel < min_rel,
        }

    # 신뢰도 미달 지표는 계산은 그대로 두고 '후보 자격' 만 뺀다.
    # 수축(med × rel)만으로는 부족하다. rel 0.45 짜리도 med 가 크면 문턱을 넘어
    # 그대로 목록에 올라가고, 사용자에게는 확신에 찬 문장으로 나간다.
    low_rel_keys = [f for f in scores if detail[f]["low_reliability"]]
    eligible = {f for f in scores if low_mode == "flag" or not detail[f]["low_reliability"]}

    # 계열당 절댓값 최대 1개만 남김.
    # 후보 자격이 있는 것들 중에서 고른다 — 미달 지표가 계열 대표를 차지한 뒤 빠지면
    # 그 계열이 통째로 사라지기 때문이다.
    keep = set()
    for fam, members in self.cfg["perf_families"].items():
        avail = [f for f in members if f in eligible]
        if avail:
            keep.add(max(avail, key=lambda f: abs(scores[f])))

    ranked = sorted(((f, s) for f, s in scores.items() if f in keep),
                    key=lambda x: x[1], reverse=True)
    s_th = self.cfg["strength_threshold"]
    i_th = self.cfg["improvement_threshold"]
    fl = self.a["feature_labels"]

    def item(f, s):
        return {"metric": fl.get(f, f), "key": f, "score": round(s, 2), **detail[f]}

    strengths = [item(f, s) for f, s in ranked if s >= s_th][:3]

    # structural 은 개선점에서 제외합니다.
    # 프롬프트에 넣고 "언급하지 마라"고 하면 LLM 이 종종 언급합니다.
    improvements = [
        item(f, s) for f, s in reversed(ranked)
        if s <= -i_th and detail[f]["finding_tag"] != "structural"
    ][:3]

    # --- 폴백: 임계값을 넘는 게 없을 때 상대적 최상/최하를 1개 채웁니다.
    # 빈 결과를 그대로 넘기면 LLM 이 없는 강점/약점을 지어내므로,
    # 대신 is_relative=True 를 붙여 "실제 강약점이 아니라 상대 순위" 임을
    # 프롬프트가 구분할 수 있게 합니다.
    if not strengths and self.cfg.get("always_show_strength"):
        for f, sc in ranked:
            if detail[f]["finding_tag"] == "structural":
                continue
            if sc <= 0:          # 평균 이하를 "강점" 으로 내보내지 않습니다
                break
            strengths.append({**item(f, sc), "is_relative": True})
            break

    if not improvements and self.cfg.get("always_show_improvement"):
        for f, sc in reversed(ranked):
            # core_strength 는 이 스타일의 핵심 강점이라 개선 목표로 나가면
            # 사용자가 바로 모순을 느낍니다. structural 과 함께 제외합니다.
            if detail[f]["finding_tag"] in ("structural", "core_strength"):
                continue
            # 평균 이상(양수)인 지표를 "개선점" 으로 내보내지 않습니다.
            # 폴백이 부호를 안 보던 탓에 KDA +0.44 가 강점과 개선점에
            # 동시에 실리는 일이 있었습니다. 모든 지표가 평균 이상이면
            # 개선점을 비우고 has_improvements=False 로 프롬프트를 분기합니다.
            if sc >= 0:
                break
            improvements.append({**item(f, sc), "is_relative": True})
            break

    for lst in (strengths, improvements):
        for it in lst:
            it.setdefault("is_relative", False)

    excluded = [fl.get(f, f) for f, s in ranked
                if s <= -i_th and detail[f]["finding_tag"] == "structural"]

    # 최우선 목표: 단순 최저점이 아니라 "이 스타일의 핵심인데 부족한 것"
    #
    # 폴백(is_relative=True)은 임계값을 못 넘은 상대 순위일 뿐인데
    # finding_tag 는 그대로 붙어 있습니다. 그대로 두면
    # "KDA -0.20*" 에 core_weakness 가 달려 "핵심 개선점" 으로 나갑니다.
    # 그래서 진짜 개선점을 먼저 보고, 없을 때만 폴백에서 고릅니다.
    primary_goal = None
    if improvements:
        real = [x for x in improvements
                if not x.get("is_relative") and not x.get("low_reliability")]
        pool = real or improvements
        # 신뢰할 수 있는 항목이 하나라도 있으면 그쪽을 최우선 목표로 삼는다.
        confident = [x for x in pool if not x.get("low_reliability")]
        pool = confident or pool
        core = [x for x in pool if x["finding_tag"] == "core_weakness"]
        primary_goal = core[0] if core else pool[0]

        # 폴백이 목표가 된 경우에는 태그의 강한 의미를 지웁니다.
        # 프롬프트가 tag 를 먼저 보고 단정 문장을 쓰는 것을 막습니다.
        if primary_goal.get("is_relative"):
            primary_goal = {**primary_goal,
                            "finding_tag_raw": primary_goal["finding_tag"],
                            "finding_tag": "relative"}

    # 어떤 지표가 왜 빠졌는지 기록에 남긴다. 값이 없어서가 아니라 못 믿어서 빠진 것이다.
    low_reliability_excluded = [
        {"metric": fl.get(f, f), "key": f,
         "reliability": detail[f]["reliability"],
         "median_z": detail[f]["median_z"],
         "score": round(scores[f], 2),
         "dropped": low_mode == "exclude"}
        for f in low_rel_keys
    ]

    return {
        "strengths": strengths,
        "improvements": improvements,
        "excluded_structural": excluded,
        "low_reliability_excluded": low_reliability_excluded,
        "min_reliability": min_rel,
        "low_reliability_mode": low_mode,
        # has_* 는 "임계값을 넘는 진짜 강점/약점이 있는가" 를 뜻합니다.
        # 폴백으로 채워진 항목은 is_relative=True 이므로 여기서 제외합니다.
        # 프롬프트는 이 플래그로 단정 표현과 완곡 표현을 갈라야 합니다.
        "has_strengths": any(
            not x["is_relative"] and not x.get("low_reliability") for x in strengths),
        "has_improvements": any(
            not x["is_relative"] and not x.get("low_reliability") for x in improvements),
        "strength_is_relative": bool(strengths) and all(x["is_relative"] for x in strengths),
        "improvement_is_relative": bool(improvements) and all(x["is_relative"] for x in improvements),
        "primary_goal": primary_goal,
        "all_scores": detail,
    }

PlayStyleAnalyzer.build_profile = _build_profile
print("build_profile 등록 완료")

# ============================================================
# 21-b. finding_tag 별 코칭 톤
# ============================================================
# 같은 -0.4 라도 그 지표가 이 스타일에서 어떤 의미인지에 따라
# 코칭 문장의 강도가 달라져야 합니다. 태그를 문장 톤으로 옮깁니다.
#
#   core_weakness : 이 스타일의 핵심 축인데 낮음 → 최우선, 단정적으로
#   below_style   : 스타일상 원래 낮은 지표인데 같은 스타일 중에서도 낮음
#                   → "같은 스타일 유저와 비교해도" 라는 기준을 반드시 명시
#   core_strength : 이 스타일의 핵심 축이면서 높음 → 강점으로만 노출
#   structural    : 스타일상 당연히 낮음 → 아예 노출하지 않음
#   neutral       : 스타일과 무관 → 담담하게 사실만
#
# is_relative=True 는 임계값 미달 폴백입니다. 실제 강약점이 아니라
# 상대 순위이므로 어떤 태그든 단정 표현을 쓰면 안 됩니다.

COACHING_TONE = {
    "core_weakness": {
        "priority": 1,
        "label": "핵심 개선점",
        "template": "{metric}은(는) {style}의 핵심 지표인데 평균을 밑돕니다. 가장 먼저 개선할 부분입니다.",
        "prompt_hint": "이 스타일의 핵심 축이 부족한 상태다. 최우선 과제로 단정적으로 서술하라.",
    },
    "below_style": {
        "priority": 2,
        "label": "스타일 내 하위",
        "template": "{metric}은(는) {style} 특성상 낮게 나오는 지표지만, 같은 스타일 유저들과 비교해도 낮은 편입니다.",
        "prompt_hint": ("이 스타일에서는 원래 낮은 지표다. 반드시 '같은 스타일 유저와 비교해도' 라는 "
                        "기준을 밝혀라. 전체 평균과 비교해 지적하면 안 된다."),
    },
    "core_strength": {
        "priority": 1,
        "label": "핵심 강점",
        "template": "{metric}은(는) {style}의 핵심 지표이고, 이 부분을 잘 해내고 있습니다.",
        "prompt_hint": "이 스타일의 핵심 축을 잘 수행하고 있다. 강점으로만 언급하고 개선 요구를 붙이지 마라.",
    },
    "structural": {
        "priority": 99,
        "label": "스타일상 제외",
        "template": "",
        "prompt_hint": "언급하지 마라.",
    },
    "neutral": {
        "priority": 3,
        "label": "일반",
        "template": "{metric} 지표가 평균과 차이를 보입니다.",
        "prompt_hint": "스타일과 무관한 지표다. 사실만 담담하게 서술하라.",
    },
    # 폴백이 최우선 목표가 된 경우. primary_goal 에서만 쓰입니다.
    "relative": {
        "priority": 99,
        "label": "상대적 하위",
        "template": "{metric}이(가) 다른 지표에 비해 낮은 편입니다.",
        "prompt_hint": ("뚜렷한 약점이 없는 유저다. 지적하지 말고 "
                        "'굳이 꼽자면' 정도의 어조로 한 가지만 제안하라."),
    },
}

RELATIVE_HINT = ("임계값을 넘지 않아 실제 강약점이라 보기 어렵다. "
                 "'부족하다' 같은 단정 대신 '상대적으로 낮은 편' 정도로만 표현하라.")

# low_reliability_mode="flag" 일 때만 쓰인다. exclude 에서는 목록에 오지 않는다.
# 이 문장은 LLM 프롬프트로 그대로 나가므로 경기 수를 하드코딩하지 않는다.
LOW_RELIABILITY_HINT = (f"경기 간 편차가 커서 {ANALYSIS_GAMES}경기로는 확정하기 이른 지표다. "
                        "'~한 편' 정도로만 말하고 단정하거나 최우선 과제로 삼지 마라.")


def coaching_line(x: dict, style_name: str = "") -> str:
    """항목 하나를 코칭 문장으로. 폴백 항목은 완곡한 문장으로 바꿉니다."""
    tone = COACHING_TONE.get(x.get("finding_tag", "neutral"), COACHING_TONE["neutral"])
    if not tone["template"]:
        return ""
    if x.get("is_relative"):
        direction = "높은" if x["score"] > 0 else "낮은"
        return f"{x['metric']}이(가) 상대적으로 {direction} 편입니다."
    return tone["template"].format(metric=x["metric"], style=style_name or "이 스타일")


def annotate_tone(profile: dict, style_name: str = "") -> dict:
    """strengths / improvements 각 항목에 톤 정보를 붙이고 우선순위로 정렬."""
    for key in ("strengths", "improvements"):
        for x in profile.get(key) or []:
            tone = COACHING_TONE.get(x.get("finding_tag", "neutral"),
                                     COACHING_TONE["neutral"])
            x.setdefault("low_reliability", False)
            x["tone_label"] = tone["label"]
            x["priority"] = 99 if x.get("is_relative") else tone["priority"]
            x["prompt_hint"] = RELATIVE_HINT if x.get("is_relative") else tone["prompt_hint"]
            x["coaching_line"] = coaching_line(x, style_name)
            # flag 모드에서만 도달한다. 단정 표현을 막고 우선순위를 뒤로 민다.
            if x["low_reliability"]:
                x["tone_label"] = f"{tone['label']} · 판단 유보"
                x["prompt_hint"] = LOW_RELIABILITY_HINT
                x["priority"] = max(x["priority"], 3)
        if profile.get(key):
            profile[key].sort(key=lambda y: (y["priority"], -abs(y["score"])))
    return profile


print("코칭 톤 테이블 등록 완료")

# COMMAND ----------

# ============================================================
# 22. 최근 추세 (최근 절반 vs 이전 절반)
# ============================================================
# 최소 5경기부터 추세를 계산합니다. 5경기면 이전 2경기와 최근 3경기를 비교하므로
# 변동성이 큽니다. |dz| >= TREND_THRESHOLD(0.7)인 큰 변화만 보조 신호로 사용합니다.
# 프롬프트 제약만으로는 LLM 이 작은 숫자를 서사로 만들어내는 걸 못 막으므로
# 코드에서 잘라내고 "변화 없음"을 명시적으로 넘깁니다.

def _recent_trend(self, rows: list):
    ordered = sorted(rows, key=lambda r: r["game_start_datetime"])
    need = self.cfg["trend_min_games"]
    if len(ordered) < need:
        return {
            "status": "insufficient", "changes": [], "window": None,
            "note": f"추세 비교에는 주 포지션 {need}경기가 필요합니다 (현재 {len(ordered)}경기).",
        }

    half = len(ordered) // 2
    prev, recent = ordered[:half], ordered[half:]
    th = self.cfg["trend_threshold"]
    fl = self.a["feature_labels"]
    rel = self.a["reliability"]

    changes = []
    for f in self.perf_features:
        a = [r["z"][f] for r in prev if f in r["z"]]
        b = [r["z"][f] for r in recent if f in r["z"]]
        if not a or not b:
            continue
        d = (float(np.median(b)) - float(np.median(a))) * rel.get(f, 1.0)
        changes.append({
            "metric": fl.get(f, f), "key": f, "delta": round(d, 2),
            "direction": "up" if d >= th else ("down" if d <= -th else "flat"),
            "significant": abs(d) >= th,
        })

    sig = [c for c in changes if c["significant"]]
    return {
        "status": "changed" if sig else "stable",
        "window": {"previous": len(prev), "recent": len(recent)},
        "changes": sorted(sig, key=lambda c: abs(c["delta"]), reverse=True)[:3],
        "note": None if sig else
                f"최근 {len(recent)}경기와 이전 {len(prev)}경기 사이에 통계적으로 유의미한 변화가 없습니다.",
    }

PlayStyleAnalyzer.recent_trend = _recent_trend
print("recent_trend 등록 완료")

# COMMAND ----------

# ============================================================
# 23. 최종 코칭 컨텍스트 조립
# ============================================================
def _analyze(self, games: list) -> dict:
    if len(games) < self.cfg["min_games"]:
        return {"status": "insufficient_games", "games": len(games),
                "message": f"분석에는 최소 {self.cfg['min_games']}경기가 필요합니다."}

    rows = self.analyze_games(games)
    valid = [row for row in rows if row["z"]]
    if not valid:
        return {"status": "unsupported_position", "games": len(games),
                "message": "분석 기준이 없는 포지션 또는 티어입니다."}

    from collections import Counter
    position_counts = Counter(row["team_position"] for row in valid)
    main_position, main_games = position_counts.most_common(1)[0]
    style = self.dominant_style(rows, main_position)

    # unstable이면 특정 스타일 기준선 보정 자체가 서사를 만들 수 있으므로 사용하지 않는다.
    main_cluster = None
    if style["status"] in ("single", "mixed") and style.get("styles"):
        main_cluster = style["styles"][0]["cluster"]

    main_rows = [row for row in valid if row["team_position"] == main_position]
    profile = self.build_profile(main_rows, main_position, main_cluster)
    style_name = style["styles"][0]["name"] if main_cluster is not None else ""
    profile = annotate_tone(profile, style_name)
    trend = self.recent_trend(main_rows)

    wins = sum(1 for row in valid if str(row.get("win")).lower() in ("true", "1", "1.0"))
    tier_counts = Counter(row.get("tier_bucket", "ALL") for row in valid)

    return {
        "status": "ok",
        "games_analyzed": len(valid),
        "main_position": main_position,
        "main_position_games": main_games,
        "position_mix": dict(position_counts),
        "position_focus_ratio": round(main_games / len(valid), 2),
        "tier_buckets": dict(tier_counts),
        "win_rate": round(100 * wins / len(valid), 1),
        "coaching_mode": "consistency" if style["status"] == "unstable" else "style",
        "play_style": style,
        "strengths": profile["strengths"],
        "improvements": profile["improvements"],
        "primary_goal": profile["primary_goal"],
        "has_strengths": profile["has_strengths"],
        "has_improvements": profile["has_improvements"],
        # 기록용 필드. 06의 LLM allow-list에는 넣지 않는다.
        "excluded_structural": profile["excluded_structural"],
        "low_reliability_excluded": profile["low_reliability_excluded"],
        "reliability_filter": {
            "min_reliability": profile["min_reliability"],
            "mode": profile["low_reliability_mode"],
        },
        "recent_trend": trend,
        "_debug_scores": profile["all_scores"],
    }

PlayStyleAnalyzer.analyze = _analyze
print("analyze 등록 완료")


# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. 테스트

# COMMAND ----------

# ============================================================
# 24. 실제 유저로 검증 — 최근 ANALYSIS_GAMES 경기
# ============================================================
analyzer = PlayStyleAnalyzer(serving)

game_counts = df_z.groupBy("player_id").count()
n_full = game_counts.filter(F.col("count") >= ANALYSIS_GAMES).count()

# ANALYSIS_GAMES 를 올리면 reliability 는 오르지만 분석 대상 유저가 줄어든다.
# 그 대가를 숫자로 확인하고 넘어간다.
n_total = game_counts.count()
print(f"[분석 대상 커버리지] 기준 {ANALYSIS_GAMES}경기")
for threshold in sorted({5, 10, 15, 20, ANALYSIS_GAMES}):
    eligible = game_counts.filter(F.col("count") >= threshold).count()
    mark = "  ← 현재" if threshold == ANALYSIS_GAMES else ""
    print(f"  {threshold:>2}경기 이상: {eligible:>5}명 "
          f"({100 * eligible / max(n_total, 1):5.1f}%){mark}")

if n_full == 0:
    raise ValueError(f"최근 {ANALYSIS_GAMES}경기를 보유한 테스트 유저가 없습니다.")

EVAL_GAMES = ANALYSIS_GAMES
test_player_id = (
    game_counts.filter(F.col("count") >= EVAL_GAMES)
    .orderBy(F.desc("count"))
    .first()["player_id"]
)

recent = (
    df_z.filter(F.col("player_id") == test_player_id)
    .orderBy(F.desc("game_start_datetime"))
    .limit(EVAL_GAMES)
    .select("match_id", "game_start_datetime", "team_position", "tier_bucket",
            "champion_name", "win", "kills", "deaths", "assists", *ALL_FEATURES)
    .toPandas()
)
recent["game_start_datetime"] = recent["game_start_datetime"].astype(str)

result = analyzer.analyze(recent.to_dict("records"))
print(json.dumps({key: value for key, value in result.items() if key != "_debug_scores"},
                 ensure_ascii=False, indent=2, default=str))


# COMMAND ----------

# ============================================================
# 25. 여러 유저 스모크 테스트 + 자동 튜닝 권고
# ============================================================
# 많이 수집된 유저만 고르면 활동량/실력이 편향될 수 있어 무작위 표본을 사용한다.

eligible_players = (
    df_z.groupBy("player_id")
    .agg(F.count("*").alias("n_games"))
    .filter(F.col("n_games") >= EVAL_GAMES)
    .orderBy(F.rand(SEED))
    .limit(100)
)
sample_ids = [row["player_id"] for row in eligible_players.collect()]

def fmt_items(items):
    if not items:
        return "-"
    return ", ".join(
        f"{item['metric']} {item['score']:+.2f}{'*' if item.get('is_relative') else ''}"
        for item in items
    )

summary = []
for player_id in sample_ids:
    games = (
        df_z.filter(F.col("player_id") == player_id)
        .orderBy(F.desc("game_start_datetime"))
        .limit(EVAL_GAMES)
        .select("match_id", "game_start_datetime", "team_position", "tier_bucket",
                "champion_name", "win", *ALL_FEATURES)
        .toPandas()
    )
    games["game_start_datetime"] = games["game_start_datetime"].astype(str)
    result_i = analyzer.analyze(games.to_dict("records"))
    if result_i["status"] != "ok":
        continue

    real_strengths = sum(not item.get("is_relative") for item in result_i["strengths"])
    real_improvements = sum(not item.get("is_relative") for item in result_i["improvements"])
    summary.append({
        "player": player_id[:8],
        "position": result_i["main_position"],
        "tier": max(result_i.get("tier_buckets", {"ALL": 1}), key=result_i.get("tier_buckets", {"ALL": 1}).get),
        "style_status": result_i["play_style"]["status"],
        "style": result_i["play_style"]["styles"][0]["name"] if result_i["play_style"]["styles"] else None,
        "consistency": result_i["play_style"]["consistency"],
        "has_strengths": result_i["has_strengths"],
        "has_improvements": result_i["has_improvements"],
        "n_real_strength": real_strengths,
        "n_real_improve": real_improvements,
        "강점": fmt_items(result_i["strengths"]),
        "개선점": fmt_items(result_i["improvements"]),
        "goal": result_i["primary_goal"]["metric"] if result_i["primary_goal"] else None,
        "goal_tag": result_i["primary_goal"]["finding_tag"] if result_i["primary_goal"] else None,
        "excluded_log_only": ", ".join(result_i["excluded_structural"]) or None,
    })

smoke = pd.DataFrame(summary)
display(smoke)

def threshold_recommendation(name, current, zero_rate, full_rate):
    if zero_rate > 0.30:
        recommended = max(0.10, round(current - 0.05, 2))
        reason = f"0개 비율 {zero_rate:.0%} > 30%"
    elif full_rate >= 0.70:
        recommended = round(current + 0.05, 2)
        reason = f"3개 충족 비율 {full_rate:.0%} >= 70%"
    else:
        recommended = current
        reason = "현재 분포가 목표 범위"
    print(f"{name}: {current:.2f} → 권고 {recommended:.2f} ({reason})")
    return recommended

if len(smoke):
    strength_zero = 1 - smoke["has_strengths"].mean()
    improvement_zero = 1 - smoke["has_improvements"].mean()
    strength_full = (smoke["n_real_strength"] >= 3).mean()
    improvement_full = (smoke["n_real_improve"] >= 3).mean()
    unstable_rate = (smoke["style_status"] == "unstable").mean()

    print(f"\n분석 성공: {len(smoke)}명")
    print(f"실제 강점 0개 비율: {strength_zero:.1%}")
    print(f"실제 개선점 0개 비율: {improvement_zero:.1%}")
    print(f"unstable 비율: {unstable_rate:.1%}")
    print("\n[다음 실행 권고]")
    recommended_strength_threshold = threshold_recommendation(
        "STRENGTH_THRESHOLD", STRENGTH_THRESHOLD, strength_zero, strength_full
    )
    recommended_improvement_threshold = threshold_recommendation(
        "IMPROVEMENT_THRESHOLD", IMPROVEMENT_THRESHOLD, improvement_zero, improvement_full
    )
    recommended_dominant_share = 0.35 if unstable_rate > 0.40 else DOMINANT_SHARE
    print(f"DOMINANT_SHARE: {DOMINANT_SHARE:.2f} → 권고 {recommended_dominant_share:.2f}")

    print("\n[포지션별 unstable 비율]")
    print(smoke.groupby("position")["style_status"].apply(lambda x: (x == "unstable").mean()).to_string())
    print("\n주의: 권고값을 설정 셀에 반영한 뒤 Cell 05부터 재실행해야 JSON에도 적용됩니다.")


# COMMAND ----------

# MAGIC %md
# MAGIC ### 25-b. 설명 계층 검증
# MAGIC
# MAGIC 기존 최빈값 방식과 소속 확률 방식을 같은 유저에 돌려 비교합니다.
# MAGIC `flipped` 가 높다면 기존 대표 스타일이 경계 경기 한두 판에 좌우되고 있었다는 뜻입니다.
# MAGIC
# MAGIC 참고: k=3 에서 분석 창 경기를 완전히 무작위로 배정해도 top_share 가 0.40 을 넘을 확률은 사실상 100% 입니다.
# MAGIC `DOMINANT_SHARE=0.40` 은 아무도 거르지 못하는 문턱이므로, low 신뢰도 비율이 높게 나오면
# MAGIC 0.55 근처로 올리는 것을 검토하세요.

# COMMAND ----------

# ============================================================
# 25-b. 기존 방식 vs 소속 확률 방식 비교
# ============================================================
explain_analyzer = make_explainable(PlayStyleAnalyzer)(serving)
analyzer = globals().get("analyzer") or PlayStyleAnalyzer(serving)

rows_out = []
for player_id in sample_ids[:50]:
    games = (
        df_z.filter(F.col("player_id") == player_id)
        .orderBy(F.desc("game_start_datetime"))
        .limit(EVAL_GAMES)
        .select("match_id", "game_start_datetime", "team_position", "tier_bucket",
                "champion_name", "win", *ALL_FEATURES)
        .toPandas()
    )
    games["game_start_datetime"] = games["game_start_datetime"].astype(str)
    records = games.to_dict("records")

    old = analyzer.analyze(records)
    new = explain_analyzer.analyze(records)
    if old["status"] != "ok" or new["status"] != "ok":
        continue

    old_style = old["play_style"]["styles"][0]["name"] if old["play_style"]["styles"] else None
    new_style = new["play_style"]["styles"][0]["name"] if new["play_style"]["styles"] else None
    evidence = new["style_evidence"] or {}
    confidence = new.get("style_confidence") or {}

    rows_out.append({
        "player": player_id[:8],
        "position": new["main_position"],
        "old_status": old["play_style"]["status"],
        "new_status": new["play_style"]["status"],
        "old_style": old_style,
        "new_style": new_style,
        "flipped": old_style != new_style,
        "confidence": confidence.get("level", "low"),
        "score": confidence.get("score", 0.0),
        "애매경기": new["play_style"].get("ambiguous_games", 0),
        "근거축": ", ".join(e["metric"] for e in evidence.get("matched_axes", [])),
        "미달축": ", ".join(e["metric"] for e in evidence.get("off_style_axes", [])),
    })

explain_smoke = pd.DataFrame(rows_out)
display(explain_smoke)

if len(explain_smoke):
    print("[신뢰도 분포]")
    print(explain_smoke["confidence"].value_counts(normalize=True).round(3).to_string())
    print(f"\n스타일이 바뀐 유저: {explain_smoke['flipped'].mean():.1%}")
    print(f"근거 축을 못 찾은 유저: {(explain_smoke['근거축'] == '').mean():.1%}")
    print("\n[포지션별 평균 신뢰도]")
    print(explain_smoke.groupby("position")["score"].mean().round(3).to_string())

    empty_rate = (explain_smoke["근거축"] == "").mean()
    if empty_rate > 0.25:
        print("\n[권고] 중심점이 흐릿합니다. k 를 줄이거나 축 임계값을 0.15 로 낮추세요.")
    low_rate = (explain_smoke["confidence"] == "low").mean()
    if low_rate > 0.35:
        print(f"[권고] low 신뢰도 {low_rate:.0%} — DOMINANT_SHARE 를 0.55 로 올리는 것을 검토하세요.")

# COMMAND ----------

# ============================================================
# 25-c. 유저 1명 최종 확인 — 이 출력이 그대로 발표 근거가 된다
# ============================================================
sample = explain_analyzer.analyze(recent.to_dict("records"))

print("=" * 60)
print("판정:", sample["play_style"]["styles"][0]["name"] if sample["play_style"]["styles"] else "-")
print("신뢰도:", sample["style_confidence"]["level"], sample["style_confidence"]["score"])
print("코칭 모드:", sample["coaching_mode"])
print("-" * 60)
for line in sample["style_explanation_ko"]:
    print("•", line)
print("-" * 60)
display(pd.DataFrame(sample["per_game_style"]))

print("\n[LLM 프롬프트 미리보기]")
print(build_coaching_prompt_v2(sample, "몬도치")[:1500])

# COMMAND ----------

print(type(result))
print(list(result.keys())[:5])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 적용 완료 및 재실행 순서
# MAGIC
# MAGIC 이번 버전(v9)에서 코드로 반영한 항목은 상단 표의 11~15번입니다. 이전 버전 항목은 그대로 유지됩니다.
# MAGIC
# MAGIC ### 재실행 순서
# MAGIC
# MAGIC 1. **`01_bronze_to_silver_v6`** 를 처음부터 끝까지 실행한다.
# MAGIC    - Cell 07 에서 티어 커버리지를, Cell 07-b 에서 04 가 만들 기준 통계 행 수를 확인한다.
# MAGIC    - Cell 08 이 `PATH_SILVER` 에 `tier` 포함 silver 를 저장한다. **이 노트북의 `BASE` 와
# MAGIC      04 Cell 00 의 `BASE` 가 같은 값인지 반드시 확인한다.**
# MAGIC 2. 이 노트북을 **Cell 00 부터 순서대로** 실행한다. 확인 지점:
# MAGIC    - Cell 01 `tier 컬럼 보존: True`
# MAGIC    - Cell 04 `티어 보정 적용: True / 커버리지: xx%`
# MAGIC    - Cell 05 `기준 통계 행` 이 55 → 01 의 07-b 예상치로 증가
# MAGIC    - Cell 09-c `|승패차| >= 0.40 인 성과 축` 목록과 잔차화 후 승패차
# MAGIC    - Cell 16-c `권고: STYLE_BASELINE_CUT = x.xx`
# MAGIC 3. 16-c 가 권고한 `STYLE_BASELINE_CUT` 을 **Cell 00 에 반영**한다.
# MAGIC 4. **Cell 05 부터 다시 실행**한다. 이 값은 `style_model.json` 의 config 에도 들어가므로
# MAGIC    재실행하지 않으면 JSON 과 추론 결과에 반영되지 않는다.
# MAGIC 5. Cell 24 에서 `finding_tag` 가 `neutral` 이외의 값으로 붙는지, Cell 16 의
# MAGIC    "구조적으로 낮은 지표" 목록이 채워지는지 확인한다.
# MAGIC 6. Cell 25 의 자동 튜닝 권고(`STRENGTH_THRESHOLD` / `IMPROVEMENT_THRESHOLD`)를 확인하고,
# MAGIC    바꿨다면 다시 4번으로 돌아간다.
# MAGIC 7. 생성된 `style_model.json` 과 `playstyle_analyzer.py`, `playstyle_explain_v2.py` 를
# MAGIC    FastAPI 프로젝트에 복사한다. **세 파일은 반드시 같이 배포한다** — 추론 쪽 잔차화 코드가
# MAGIC    JSON 의 `residual` / `perf_residual` 블록과 짝이다.
# MAGIC
# MAGIC ### 셀 순서에 대한 제약
# MAGIC
# MAGIC `09-a → 09-b → 09-c` 는 이 순서를 지켜야 한다. 그 뒤에 k 탐색·학습·기준선이 온다.
# MAGIC
# MAGIC - 09-c 가 Cell 16(기준선) 뒤로 가면 기준선이 잔차화 전 값으로 만들어져 보정이 반영되지 않는다.
# MAGIC - 09-b 와 09-c 는 각각 재실행해도 안전하다(`raw_*` 백업에서 다시 시작). 다만 순서는 유지한다.
# MAGIC
# MAGIC ### 남은 한계
# MAGIC
# MAGIC - 시드 티어를 같은 매치 참가자에게 전파한 값은 실제 개인 티어가 아니라 **매치 수준 근사치**다.
# MAGIC   `tier_source` 컬럼으로 `seed_self`(시드 본인) 와 `match_seed`(전파) 를 구분해 두었다.
# MAGIC   실서비스 수집에서는 League-v4 로 대상 사용자의 현재 티어를 직접 조회해 요청 데이터의
# MAGIC   `tier` 또는 `tier_bucket` 에 넣는 것이 가장 정확하다.
# MAGIC - 09-c 의 잔차화 대상은 `objective_damage_per_min` 하나로 고정돼 있다.
# MAGIC   자동 탐지는 `kda` 처럼 원래 승패와 강하게 붙어 있는 축도 함께 잡지만 잔차화하지 않고,
# MAGIC   "탐지됐지만 제외한 축" 으로 출력만 한다. 범위를 넓히려면 Cell 00 의
# MAGIC   `PERF_RESIDUAL_FEATURES` 를 `None` 으로 되돌린다.
# MAGIC - 잔차화는 `win` 에 대한 **선형** 보정이다. 승패차가 비선형으로 남는 축은 09-c 출력의
# MAGIC   "잔차화 후 최대 승패차" 경고로 드러난다.

# COMMAND ----------

# ============================================================
# 25. 자연어 롤 피드백 리포트 생성
# ============================================================
# 이 셀은 Cell 24(실제 유저 검증)를 실행하지 않아도 동작합니다.
# 단, 학습/추론 셀(00~23)은 먼저 실행되어 serving, df_z, ALL_FEATURES가 있어야 합니다.

import pandas as pd

def _mean_or_zero(df, column):
    return float(df[column].mean()) if column in df.columns and len(df) else 0.0


def _win_mask(series):
    '''Spark/Pandas bool과 문자열 bool 모두 안전하게 처리한다.'''
    if str(series.dtype) == "bool":
        return series
    return series.astype(str).str.lower().isin(["true", "1", "1.0"])


def _format_metric_list(items, limit=2):
    if not items:
        return "뚜렷한 수치 강점은 아직 확인되지 않았습니다"
    names = [x.get("metric", x.get("key", "지표")) for x in items[:limit]]
    return "·".join(names)


def build_korean_feedback(analyzer_result, games, player_name="플레이어"):
    '''ML 분석 결과 + 최근 경기 원본으로 사용자에게 보여줄 코칭 근거를 만든다.'''
    if analyzer_result.get("status") != "ok":
        return {
            "status": analyzer_result.get("status", "error"),
            "message": analyzer_result.get("message", "분석할 수 없습니다."),
        }

    all_games = pd.DataFrame(games).copy()
    main_position = analyzer_result["main_position"]
    games = all_games[all_games["team_position"] == main_position].copy()
    if games.empty:
        return {"status": "error", "message": f"{main_position} 경기 데이터가 없습니다."}

    win_mask = _win_mask(games["win"]) if "win" in games else pd.Series(False, index=games.index)
    win_games, loss_games = games[win_mask], games[~win_mask]
    wins, losses = int(win_mask.sum()), int((~win_mask).sum())
    win_rate = 100 * wins / len(games)

    style = analyzer_result.get("play_style", {})
    style_names = [x.get("name", "분석된 스타일") for x in style.get("styles", [])]
    style_name = "·".join(style_names) if style_names else "혼합 플레이형"

    champion_counts = games["champion_name"].value_counts() if "champion_name" in games else pd.Series(dtype=int)
    main_champion = str(champion_counts.index[0]) if len(champion_counts) else None
    main_champion_games = int(champion_counts.iloc[0]) if len(champion_counts) else 0

    strengths = analyzer_result.get("strengths", [])
    improvements = analyzer_result.get("improvements", [])
    primary_goal = analyzer_result.get("primary_goal") or (improvements[0] if improvements else None)

    # 패배 패턴은 전체 평균이 아니라 승리/패배 그룹 간 차이로만 판단한다.
    death_gap = _mean_or_zero(loss_games, "deaths") - _mean_or_zero(win_games, "deaths")
    kda_gap = _mean_or_zero(win_games, "kda") - _mean_or_zero(loss_games, "kda")
    kp_gap = _mean_or_zero(win_games, "kill_participation") - _mean_or_zero(loss_games, "kill_participation")
    vision_gap = _mean_or_zero(win_games, "vision_score_per_min") - _mean_or_zero(loss_games, "vision_score_per_min")

    loss_patterns = []
    if len(loss_games) >= 2 and len(win_games) >= 2:
        if death_gap >= 0.7:
            loss_patterns.append({
                "key": "death_gap",
                "text": f"패배할 때 평균 사망이 승리보다 {death_gap:.1f}회 많아 한타 전에 전투력을 잃는 패턴",
            })
        if kp_gap >= 0.08:
            loss_patterns.append({
                "key": "kp_gap",
                "text": "패배 경기에서 킬 관여율이 낮아져 한타 기여가 줄어드는 패턴",
            })
        if vision_gap >= 0.15:
            loss_patterns.append({
                "key": "vision_gap",
                "text": "패배 경기에서 분당 시야 점수가 낮아 오브젝트 전 준비가 약해지는 패턴",
            })
        if kda_gap >= 1.0 and not loss_patterns:
            loss_patterns.append({
                "key": "kda_gap",
                "text": "패배 경기에서 KDA가 크게 낮아져 전투 손실이 누적되는 패턴",
            })

    if not loss_patterns:
        loss_patterns.append({
            "key": "insufficient_pattern",
            "text": "승패 그룹 간 뚜렷한 단일 패턴은 아직 부족해, 다음 경기에서도 사망·시야·한타 관여를 함께 관찰할 필요",
        })

    primary_metric = primary_goal.get("metric", primary_goal.get("key", "생존과 한타 참여")) if primary_goal else "생존과 한타 참여"
    primary_message = (primary_goal.get("coaching_line") if primary_goal else None) or f"{primary_metric}을(를) 다음 경기의 우선 목표로 두세요."

    if any(x["key"] == "death_gap" for x in loss_patterns):
        action = "시야를 잡거나 오브젝트를 준비할 때 혼자 깊게 들어가기보다, 아군 위치와 합류 가능 여부를 먼저 확인해 생존을 우선하세요."
    elif primary_goal:
        action = f"다음 5경기 동안 {primary_metric}을(를) 한 가지 행동 목표로 정하고, 매 경기 종료 후 수치를 확인하세요."
    else:
        action = "현재 강점을 유지하면서 불필요한 사망과 무리한 진입을 줄이는 데 집중하세요."

    return {
        "status": "ok",
        "player_name": player_name,
        "position": main_position,
        "games": len(games),
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "kda": round(_mean_or_zero(games, "kda"), 2),
        "style_name": style_name,
        "main_champion": main_champion,
        "main_champion_games": main_champion_games,
        "strengths": strengths,
        "loss_patterns": loss_patterns,
        "primary_goal": {
            "metric": primary_metric,
            "message": primary_message,
            "action": action,
        },
        "one_line_summary": f"{style_name} 강점은 살리고, {primary_metric} 개선과 불필요한 사망 감소에 집중하면 승률 개선 가능성이 가장 큽니다.",
    }


def render_korean_feedback(feedback):
    '''요청한 '몬도치님 피드백' 형태의 사용자 노출용 Markdown을 만든다.'''
    if feedback.get("status") != "ok":
        return feedback.get("message", "분석할 수 없습니다.")

    champion_sentence = ""
    if feedback["main_champion"]:
        champion_sentence = f" 특히 **{feedback['main_champion']}**을 {feedback['main_champion_games']}게임에서 가장 많이 선택했습니다."

    strength_text = _format_metric_list(feedback["strengths"])
    pattern_text = "\n".join(f"- {x['text']}" for x in feedback["loss_patterns"])
    return f'''{feedback['player_name']}님의 최근 플레이 스타일은 **{feedback['style_name']}**, 특히 **{feedback['position']} 포지션 중심의 플레이**에 가깝습니다.{champion_sentence}

최근 정상적으로 종료된 {feedback['games']}게임 기준 승률은 **{feedback['win_rate']:.1f}%**, KDA는 **{feedback['kda']:.2f}**입니다. 강점으로는 **{strength_text}**가 확인됐습니다. 이 지표들은 단순 평균 비교가 아니라 현재 플레이 스타일과 같은 포지션 기준으로 산출했습니다.

반대로 패배할 때는 다음 패턴이 확인됩니다.
{pattern_text}

🎯 **가장 중요한 개선 포인트: {feedback['primary_goal']['metric']}**

{feedback['primary_goal']['message']} {feedback['primary_goal']['action']}

한 줄로 요약하면, **“{feedback['one_line_summary']}”**입니다.'''


# Cell 24를 건너뛰고 이 셀만 실행한 경우에도 result/recent를 자동 준비한다.
if "result" in globals() and "recent" in globals():
    feedback_result = result
    feedback_recent = recent.copy()
else:
    required = [name for name in ("serving", "df_z", "ALL_FEATURES", "PlayStyleAnalyzer") if name not in globals()]
    if required:
        raise RuntimeError(
            "이 셀만 단독 실행하려면 먼저 학습/추론 셀을 실행해야 합니다. "
            f"현재 없는 변수: {', '.join(required)}"
        )

    analyzer = globals().get("analyzer") or PlayStyleAnalyzer(serving)
    feedback_game_count = int(globals().get("EVAL_GAMES", globals().get("ANALYSIS_GAMES", 10)))
    feedback_player_id = globals().get("test_player_id")
    if feedback_player_id is None:
        feedback_player_id = (
            df_z.groupBy("player_id").count()
            .orderBy(F.desc("count"))
            .first()["player_id"]
        )

    feedback_columns = list(dict.fromkeys([
        "match_id", "game_start_datetime", "team_position", "tier_bucket", "champion_name", "win",
        "kills", "deaths", "assists", *ALL_FEATURES,
    ]))
    feedback_columns = [c for c in feedback_columns if c in df_z.columns]
    feedback_recent = (
        df_z.filter(F.col("player_id") == feedback_player_id)
        .orderBy(F.desc("game_start_datetime"))
        .limit(feedback_game_count)
        .select(*feedback_columns)
        .toPandas()
    )
    feedback_recent["game_start_datetime"] = feedback_recent["game_start_datetime"].astype(str)
    feedback_result = analyzer.analyze(feedback_recent.to_dict("records"))


PLAYER_NAME = "몬도치"  # 실제 Riot ID로 바꿔도 됩니다.
natural_feedback = build_korean_feedback(
    feedback_result,
    feedback_recent.to_dict("records"),
    player_name=PLAYER_NAME,
)

print(render_korean_feedback(natural_feedback))
print("\n[프론트엔드/RAG 전달용 JSON]")
print(json.dumps(natural_feedback, ensure_ascii=False, indent=2, default=str))