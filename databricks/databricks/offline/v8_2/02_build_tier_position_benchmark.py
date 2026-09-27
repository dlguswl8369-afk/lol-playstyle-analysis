# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
"""Build four deterministic v8.2 benchmark fallback scopes."""

import pyspark.sql.functions as F

CATALOG = "lol_insight"
SOURCE_TABLE = f"{CATALOG}.gold.benchmark_source"
TARGET_TABLE = f"{CATALOG}.gold.tier_position_benchmark"
MIN_GROUP_N = 5
COACH_FEATURES = [
    "kda", "kill_participation", "cs_per_min", "gold_per_min", "gold_share",
    "damage_per_min", "damage_taken_per_min", "damage_share", "damage_efficiency",
    "vision_score_per_min", "wards_placed_per_min", "wards_killed_per_min",
    "vision_wards_bought_per_min", "objective_damage_per_min",
]
# Combat Context only. These statistics are not RF/FDR coaching features and
# never become Priority candidates.
COMBAT_CONTEXT_FEATURES = ["deaths"]

benchmark_source = spark.table(SOURCE_TABLE)
aggregations = [F.count("*").alias("n")]
for feature in COACH_FEATURES + COMBAT_CONTEXT_FEATURES:
    aggregations.extend(
        [F.mean(feature).alias(f"{feature}_mean"), F.stddev(feature).alias(f"{feature}_std")]
    )


def aggregate_scope(group_columns, tier_value=None, curve_value=None, scope=""):
    frame = benchmark_source.groupBy(*group_columns).agg(*aggregations).filter(F.col("n") >= MIN_GROUP_N)
    if tier_value is not None:
        frame = frame.withColumn("tier", F.lit(tier_value))
    if curve_value is not None:
        frame = frame.withColumn("power_curve", F.lit(curve_value))
    return frame.withColumn("benchmark_scope", F.lit(scope))


exact = aggregate_scope(
    ["team_position", "tier", "power_curve"], scope="EXACT"
)
tier_position = aggregate_scope(
    ["team_position", "tier"], curve_value="ALL", scope="TIER_POSITION"
)
all_tier_curve = aggregate_scope(
    ["team_position", "power_curve"], tier_value="ALL_TIER", scope="ALL_TIER_POWER_CURVE"
)
position_only = aggregate_scope(
    ["team_position"], tier_value="ALL_TIER", curve_value="ALL", scope="POSITION_ONLY"
)

final_benchmark = (
    exact.unionByName(tier_position)
    .unionByName(all_tier_curve)
    .unionByName(position_only)
)
(
    final_benchmark.write.format("delta").mode("overwrite")
    .option("overwriteSchema", "true").saveAsTable(TARGET_TABLE)
)
print(f"Created {TARGET_TABLE} ({final_benchmark.count()} groups)")