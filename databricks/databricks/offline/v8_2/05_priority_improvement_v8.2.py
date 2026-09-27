# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 05. 우선순위 통계 유의성 검증 (v8.2 - PySpark Native)
# MAGIC - 대용량 데이터를 드라이버로 내리지 않고, PySpark로 분산 집계 후 p-value를 계산합니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 1. 모듈 임포트 및 BH 보정 함수 정의

# COMMAND ----------

import numpy as np
import pandas as pd
from scipy import stats
from pyspark.sql import functions as F

CATALOG = "lol_insight"
TACTICAL_ROLES = ["FRONTLINE", "BRUISER", "BURST_CARRY", "DPS_CARRY", "UTILITY"]
COACH_FEATURES = [
    "kda", "kill_participation", "cs_per_min", "gold_per_min", "gold_share",
    "damage_per_min", "damage_taken_per_min", "damage_share", "damage_efficiency",
    "vision_score_per_min", "wards_placed_per_min", "wards_killed_per_min",
    "vision_wards_bought_per_min", "objective_damage_per_min"
]
MIN_EFFECT_SIZE = 0.05
FDR_ALPHA = 0.05

def bh_correction(pvals: np.ndarray, alpha: float = FDR_ALPHA) -> np.ndarray:
    m = len(pvals)
    order = np.argsort(pvals)
    sorted_p = np.asarray(pvals)[order]
    thresholds = alpha * (np.arange(1, m + 1) / m)
    passed = sorted_p <= thresholds
    reject = np.zeros(m, dtype=bool)
    if passed.any():
        max_i = np.max(np.where(passed))
        reject[order[:max_i + 1]] = True
    return reject

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2. PySpark 상관계수 분산 집계

# COMMAND ----------

natural_df = spark.table(f"{CATALOG}.gold.natural_distribution").dropna(subset=COACH_FEATURES + ["win", "tactical_role"])
natural_df = natural_df.withColumn("win_int", F.col("win").cast("int"))

agg_exprs = [F.count("*").alias("n")]
for f in COACH_FEATURES:
    agg_exprs.append(F.corr(f, "win_int").alias(f"{f}_corr"))

# 요약 통계(상관계수 및 표본 수)만 집계하여 Driver로 로드
stat_summary_df = natural_df.groupBy("tactical_role").agg(*agg_exprs).filter(F.col("n") >= 50)
stat_summary = stat_summary_df.collect()

rf_pd = spark.table(f"{CATALOG}.gold.role_metric_rf_importance").toPandas()
rf_map = {(r.tactical_role, r.feature): r.rf_importance for r in rf_pd.itertuples()}

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3. 유의성 검증(t-test) 및 가중치 결합 저장

# COMMAND ----------

rows = []
for row in stat_summary:
    role = row["tactical_role"]
    if role not in TACTICAL_ROLES: continue
    n = row["n"]

    corrs, pvals = [], []
    for f in COACH_FEATURES:
        r = row[f"{f}_corr"]
        r = float(r) if r is not None and not np.isnan(r) else 0.0
        corrs.append(r)
        
        # 피어슨 상관계수 통계량 환산
        if n <= 2 or abs(r) >= 1.0:
            p = 0.0
        else:
            t_stat = r * np.sqrt((n - 2) / (1 - r**2))
            p = 2 * stats.t.sf(np.abs(t_stat), n - 2)
        pvals.append(p)

    # Benjamini-Hochberg (BH) 보정 적용
    bh_pass = bh_correction(np.array(pvals))

    for f, r, p, passed_bh in zip(COACH_FEATURES, corrs, pvals, bh_pass):
        effect_ok = abs(r) >= MIN_EFFECT_SIZE
        sig = bool(passed_bh and effect_ok)
        rows.append({
            "tactical_role": role, "feature": f,
            "corr": round(float(r), 4), "p_value": round(float(p), 5),
            "bh_significant": bool(passed_bh), "effect_size_ok": bool(effect_ok),
            "significant": sig,
            "direction": int(np.sign(r)) if sig else 0,
            "weight": float(rf_map.get((role, f), 0.0) if sig else 0.0),
        })

direction_pd = pd.DataFrame(rows)
spark.createDataFrame(direction_pd).write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.gold.role_metric_direction")
print("✅ PySpark 기반 상관계수 산출 및 BH FDR 통계 검증 완료!")