# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
# MAGIC %md
# MAGIC # 03. 플레이 스타일 클러스터링 (v8.2 - PySpark Native)
# MAGIC - Spark `Bucketizer`를 활용해 백분위수 정규화를 분산 처리합니다.

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 1. 라이브러리 및 하이퍼파라미터 설정

# COMMAND ----------

import mlflow
from pyspark.sql import functions as F
from pyspark.ml.feature import VectorAssembler, StandardScaler, Bucketizer
from pyspark.ml.clustering import KMeans
from pyspark.ml.evaluation import ClusteringEvaluator
from functools import reduce

CATALOG = "lol_insight"
STYLE_FEATURES = ["damage_share", "gold_share", "vision_score_per_min", "damage_taken_share"]
POSITIONS = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]
SIGNIFICANT_Z = 0.35

POSITION_LABELS = {
    "TOP": {"damage_share": "무력 중심형", "gold_share": "이기적 파밍형", "vision_score_per_min": "영향력 합류형", "damage_taken_share": "퓨어 탱커형"},
    "JUNGLE": {"damage_share": "무력 암살형", "gold_share": "동선 성장형", "vision_score_per_min": "시야 서포팅형", "damage_taken_share": "하드 이니시형"},
    "MIDDLE": {"damage_share": "폭딜 누킹형", "gold_share": "라인전 주도형", "vision_score_per_min": "전역 로머형", "damage_taken_share": "근접 인파이팅형"},
    "BOTTOM": {"damage_share": "하이퍼 캐리형", "gold_share": "성장 집착형", "vision_score_per_min": "유틸 지원형", "damage_taken_share": "초공격 인파이터형"},
    "UTILITY": {"damage_share": "견제 딜포터형", "gold_share": "킬캐치형", "vision_score_per_min": "시야 마에스트로", "damage_taken_share": "희생형 탱커"},
}

def label_cluster(center: dict, position: str) -> str:
    vocab = POSITION_LABELS[position]
    top_feat = max(center, key=center.get)
    if center[top_feat] >= SIGNIFICANT_Z: return vocab[top_feat]
    return "균형형"

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2. 포지션별 분산 클러스터링 (K-Means) 학습 루프

# COMMAND ----------

mlflow.set_experiment("/Shared/lol_insight_coach/playstyle_clustering_v8")
natural_df = spark.table(f"{CATALOG}.gold.natural_distribution")
all_results = []

for position in POSITIONS:
    pos_df = natural_df.filter(F.col("team_position") == position).dropna(subset=STYLE_FEATURES)
    n = pos_df.count()
    if n < 10: continue

    # a. Bucketizer로 백분위(Percentile) 분산 매핑
    for f in STYLE_FEATURES:
        quantiles = pos_df.stat.approxQuantile(f, [i/100.0 for i in range(1, 101)], 0.01)
        splits = [-float("inf")] + sorted(list(set(quantiles))) + [float("inf")]
        bucketizer = Bucketizer(splits=splits, inputCol=f, outputCol=f"{f}_bucket")
        pos_df = bucketizer.transform(pos_df)
        max_bucket = len(splits) - 2
        pos_df = pos_df.withColumn(f"{f}_pct", F.col(f"{f}_bucket") / max_bucket)

    # b. 플레이스타일 비중(Mix) 계산
    pct_sum_expr = sum(F.col(f"{f}_pct") for f in STYLE_FEATURES)
    pos_df = pos_df.withColumn("pct_sum", pct_sum_expr)
    for f in STYLE_FEATURES:
        pos_df = pos_df.withColumn(f"{f}_mix", F.col(f"{f}_pct") / F.when(F.col("pct_sum") == 0, 1).otherwise(F.col("pct_sum")))

    # c. ML 파이프라인
    assembler = VectorAssembler(inputCols=[f"{f}_mix" for f in STYLE_FEATURES], outputCol="raw_features")
    scaler = StandardScaler(inputCol="raw_features", outputCol="features", withMean=True, withStd=True)
    vec_df = assembler.transform(pos_df)
    scaled_df = scaler.fit(vec_df).transform(vec_df)

    # d. 최적의 K 탐색
    best_k, best_score, best_model = None, -1, None
    evaluator = ClusteringEvaluator(featuresCol="features", metricName="silhouette")
    for k in [3, 4, 5]:
        m = KMeans(k=k, seed=42, featuresCol="features").fit(scaled_df)
        s = evaluator.evaluate(m.transform(scaled_df))
        if s > best_score: best_k, best_score, best_model = k, s, m

    # e. MLflow 기록 및 라벨링
    with mlflow.start_run(run_name=f"kmeans_v8_{position.lower()}"):
        mlflow.log_params({"position": position, "k": best_k, "n_samples": n})
        mlflow.log_metric("silhouette", best_score)
        mlflow.spark.log_model(spark_model=best_model, artifact_path=f"kmeans_v8_{position.lower()}", dfs_tmpdir=f"/Volumes/{CATALOG}/raw/mlflow_tmp")

        centers_named = [dict(zip(STYLE_FEATURES, c)) for c in best_model.clusterCenters()]
        style_names = {i: label_cluster(c, position) for i, c in enumerate(centers_named)}
        final_names, value_counts = {}, {}
        for i, name in style_names.items(): value_counts[name] = value_counts.get(name, 0) + 1
        for i, name in style_names.items(): final_names[i] = f"{name} #{i}" if value_counts[name] > 1 else name

    mapping_expr = F.create_map([F.lit(x) for pair in final_names.items() for x in pair])
    pred_df = best_model.transform(scaled_df).withColumn("playstyle", mapping_expr[F.col("prediction")])
    all_results.append(pred_df.select("match_id", "player_id", "team_position", "prediction", "playstyle"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3. 최종 클러스터 저장

# COMMAND ----------

final_cluster_df = reduce(lambda a, b: a.unionByName(b), all_results)
final_cluster_df.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CATALOG}.gold.playstyle_clusters")
print("✅ PySpark 분산 최적화 기반 K-Means 클러스터링 완료")