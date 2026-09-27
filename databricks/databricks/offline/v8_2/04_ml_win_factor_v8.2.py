# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 04. 승패 요인 분석 (Random Forest)
# MAGIC - 5대 전략 역할군(TACTICAL_ROLES) 기반으로 랜덤 포레스트를 훈련합니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 1. ML 설정 및 피처 어셈블러

# COMMAND ----------

import mlflow
from pyspark.ml import Pipeline
from pyspark.ml.classification import RandomForestClassifier
from pyspark.ml.evaluation import BinaryClassificationEvaluator
from pyspark.ml.feature import VectorAssembler
from pyspark.sql import functions as F

CATALOG = "lol_insight"
TACTICAL_ROLES = ["FRONTLINE", "BRUISER", "BURST_CARRY", "DPS_CARRY", "UTILITY"]
COACH_FEATURES = [
    "kda",
    "kill_participation",
    "cs_per_min",
    "gold_per_min",
    "gold_share",
    "damage_per_min",
    "damage_taken_per_min",
    "damage_share",
    "damage_efficiency",
    "vision_score_per_min",
    "wards_placed_per_min",
    "wards_killed_per_min",
    "vision_wards_bought_per_min",
    "objective_damage_per_min",
]

natural_df = (
    spark.table(f"{CATALOG}.gold.natural_distribution")
    .withColumn("label", F.col("win").cast("double"))
    .dropna(subset=COACH_FEATURES + ["label", "tactical_role"])
)
assembler = VectorAssembler(inputCols=COACH_FEATURES, outputCol="features")
mlflow.set_experiment("/Shared/lol_insight_coach/win_factor_analysis_v8")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2. 모델 학습 루프 및 Feature Importance 추출

# COMMAND ----------

importance_rows = []
for role in TACTICAL_ROLES:
    rg_df = natural_df.filter(F.col("tactical_role") == role)
    if rg_df.count() < 50:
        continue

    train_df, test_df = rg_df.randomSplit([0.8, 0.2], seed=42)
    rf_pipeline = Pipeline(
        stages=[
            assembler,
            RandomForestClassifier(
                featuresCol="features", labelCol="label", numTrees=300, maxDepth=6, seed=42
            ),
        ]
    )

    with mlflow.start_run(run_name=f"rf_win_factor_{role.lower()}"):
        rf_model = rf_pipeline.fit(train_df)
        rf_auc = BinaryClassificationEvaluator(
            labelCol="label", metricName="areaUnderROC"
        ).evaluate(rf_model.transform(test_df))

        for feat, imp in zip(
            COACH_FEATURES, rf_model.stages[-1].featureImportances.toArray(), strict=False
        ):
            importance_rows.append(
                {
                    "tactical_role": role,
                    "feature": feat,
                    "rf_importance": float(imp),
                    "auc": float(rf_auc),
                    "n": rg_df.count(),
                }
            )

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3. 학습 결과 Delta Table 저장

# COMMAND ----------

spark.createDataFrame(importance_rows).write.format("delta").mode("overwrite").option(
    "overwriteSchema", "true"
).saveAsTable(f"{CATALOG}.gold.role_metric_rf_importance")
print("✅ 랜덤 포레스트 역할군별 중요도 학습 완료!")
