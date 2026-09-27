# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
"""Build data-driven champion tactical-role and power-curve profiles."""

import collections

import pyspark.sql.functions as F
from pyspark.sql.types import StringType
from pyspark.sql.window import Window

CATALOG = "lol_insight"
PARTICIPANT_SOURCE = (
    "abfss://lol-data@5dt2ndteam3.dfs.core.windows.net/"
    "raw/ml_match_participants.csv"
)
ITEM_ROLE_TABLE = f"{CATALOG}.gold.item_role_profile"
TARGET_TABLE = f"{CATALOG}.gold.champion_profile_data_driven"

# Read the historical source directly so profile construction does not depend
# on the later Bronze-to-Silver notebook.
participants_df = (
    spark.read.option("header", True).option("inferSchema", True).csv(PARTICIPANT_SOURCE)
)
item_role_pd = spark.table(ITEM_ROLE_TABLE).toPandas()
ITEM_ROLE_MAP = dict(zip(item_role_pd["item_id"], item_role_pd["roles"]))


@F.udf(returnType=StringType())
def get_pure_item_role(item0, item1, item2, item3, item4, item5):
    roles = []
    for item in (item0, item1, item2, item3, item4, item5):
        if item is None:
            continue
        try:
            mapped = ITEM_ROLE_MAP.get(int(item))
        except (TypeError, ValueError):
            continue
        if mapped:
            roles.extend(role.strip() for role in mapped.split(",") if role.strip())
    if not roles:
        return None
    counts = collections.Counter(roles)
    max_count = max(counts.values())
    winners = [role for role, count in counts.items() if count == max_count]
    return winners[0] if len(winners) == 1 else None


role_df = (
    participants_df.withColumn(
        "inferred_role",
        get_pure_item_role("item0", "item1", "item2", "item3", "item4", "item5"),
    )
    .filter(F.col("inferred_role").isNotNull())
)
role_window = Window.partitionBy("champion_name").orderBy(F.desc("count"), F.asc("inferred_role"))
data_driven_roles = (
    role_df.groupBy("champion_name", "inferred_role").count()
    .withColumn("rank", F.row_number().over(role_window))
    .filter(F.col("rank") == 1)
    .select("champion_name", F.col("inferred_role").alias("default_tactical_role"))
)

time_df = (
    participants_df.filter(F.col("game_duration_seconds") >= 900)
    .withColumn("game_min", F.col("game_duration_seconds") / 60.0)
    .withColumn("win_int", F.col("win").cast("int"))
)
quantiles = time_df.stat.approxQuantile("game_min", [0.2, 0.4, 0.6, 0.8], 0.01)
if len(quantiles) != 4:
    raise ValueError("Unable to calculate four game-duration quantiles")
q1, q2, q3, q4 = quantiles
time_df = time_df.withColumn(
    "time_bin",
    F.when(F.col("game_min") <= q1, "1_Early")
    .when(F.col("game_min") <= q2, "2_Early-Mid")
    .when(F.col("game_min") <= q3, "3_Mid")
    .when(F.col("game_min") <= q4, "4_Mid-Late")
    .otherwise("5_Late"),
)
power_window = Window.partitionBy("champion_name").orderBy(
    F.desc("win_rate"), F.asc("time_bin")
)
peak_power_df = (
    time_df.groupBy("champion_name", "time_bin")
    .agg(F.mean("win_int").alias("win_rate"), F.count("*").alias("games"))
    .filter(F.col("games") >= 30)
    .withColumn("rank", F.row_number().over(power_window))
    .filter(F.col("rank") == 1)
    .select("champion_name", F.substring(F.col("time_bin"), 3, 20).alias("power_curve"))
)

# Full outer preserves a known role even when power-curve samples are thin and
# vice versa. Missing defaults remain null and become UNKNOWN only at inference.
final_profile = data_driven_roles.join(peak_power_df, on="champion_name", how="full")
(
    final_profile.write.format("delta").mode("overwrite")
    .option("overwriteSchema", "true").saveAsTable(TARGET_TABLE)
)
print(f"Created {TARGET_TABLE} ({final_profile.count()} champions)")