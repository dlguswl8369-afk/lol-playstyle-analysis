# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "5"
# ///
"""Build v8.2 benchmark features after item and champion bootstrap."""

import collections

from pyspark.sql import functions as F
from pyspark.sql.types import StringType
from pyspark.sql.window import Window

CATALOG = "lol_insight"
SCHEMA = "bronze"

RAW_PATH = "abfss://lol-data@5dt2ndteam3.dfs.core.windows.net/raw"

ITEM_ROLE_TABLE = f"{CATALOG}.gold.item_role_profile"
PROFILE_TABLE = f"{CATALOG}.gold.champion_profile_data_driven"


def load_csv_to_bronze(file_name: str, table_name: str):
    frame = (
        spark.read.option("header", True).option("inferSchema", True).csv(f"{RAW_PATH}/{file_name}")
    )

    (
        frame.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{CATALOG}.{SCHEMA}.{table_name}")
    )

    return frame


seed_low_df = load_csv_to_bronze("ml_seed_players.csv", "seed_players")
seed_high_df = load_csv_to_bronze("ml_seed_players_high_tier.csv", "seed_players_high_tier")
participants_df = load_csv_to_bronze("ml_match_participants.csv", "match_participants")
seeds_df = seed_low_df.select("player_id", "tier").unionByName(
    seed_high_df.select("player_id", "tier")
)

# These tables must be produced by 00a and 00b. This notebook no longer creates
# either one and therefore cannot recreate the old dependency cycle.
item_roles_df = spark.table(ITEM_ROLE_TABLE)
profile_df = spark.table(PROFILE_TABLE)
item_pd = item_roles_df.toPandas()
ITEM_ROLE_MAP = dict(zip(item_pd["item_id"], item_pd["roles"], strict=False))
VALID_ROLES = {"FRONTLINE", "BRUISER", "BURST_CARRY", "DPS_CARRY", "UTILITY"}


@F.udf(returnType=StringType())
def infer_intent_role(item0, item1, item2, item3, item4, item5, default_role):
    default = str(default_role).strip().upper() if default_role is not None else "UNKNOWN"
    if default not in VALID_ROLES:
        default = "UNKNOWN"

    roles = []
    for item in (item0, item1, item2, item3, item4, item5):
        if item is None:
            continue
        try:
            mapped = ITEM_ROLE_MAP.get(int(item))
        except (TypeError, ValueError):
            continue
        if mapped:
            roles.extend(
                role
                for role in (value.strip().upper() for value in mapped.split(","))
                if role in VALID_ROLES
            )

    if not roles:
        return default
    counts = collections.Counter(roles)
    max_count = max(counts.values())
    winners = {role for role, count in counts.items() if count == max_count}
    if len(winners) == 1:
        return next(iter(winners))
    if default in winners:
        return default
    return "UNKNOWN"


silver_df = (
    participants_df.join(F.broadcast(profile_df), on="champion_name", how="left")
    .fillna({"power_curve": "ALL"})
    .filter(F.col("queue_id") == 420)
    .filter(F.col("game_duration_seconds") >= 900)
    .filter(F.col("team_position").isNotNull() & (F.col("team_position") != ""))
)

silver_df = silver_df.withColumn(
    "patch",
    F.concat_ws(
        ".", F.split(F.col("game_version"), r"\.")[0], F.split(F.col("game_version"), r"\.")[1]
    ),
)
top_patches = [
    row["patch"]
    for row in silver_df.groupBy("patch").count().orderBy(F.desc("count")).limit(4).collect()
]
silver_df = silver_df.filter(F.col("patch").isin(top_patches))

minutes = F.col("game_duration_seconds") / 60.0
team_window = Window.partitionBy("match_id", "team_id")
silver_df = (
    silver_df.withColumn(
        "tactical_role",
        infer_intent_role(
            "item0", "item1", "item2", "item3", "item4", "item5", "default_tactical_role"
        ),
    )
    .withColumn("role_fallback", F.col("tactical_role") == "UNKNOWN")
    .withColumn("team_total_kills", F.sum("kills").over(team_window))
    .withColumn("team_gold_earned", F.sum("gold_earned").over(team_window))
    .withColumn(
        "team_damage_to_champions", F.sum("total_damage_dealt_to_champions").over(team_window)
    )
    .withColumn("team_damage_taken", F.sum("total_damage_taken").over(team_window))
    .withColumn("total_cs", F.col("total_minions_killed") + F.col("neutral_minions_killed"))
    .withColumn("kda", (F.col("kills") + F.col("assists")) / F.greatest(F.col("deaths"), F.lit(1)))
    .withColumn(
        "kill_participation",
        (F.col("kills") + F.col("assists")) / F.greatest(F.col("team_total_kills"), F.lit(1)),
    )
    .withColumn("cs_per_min", F.col("total_cs") / minutes)
    .withColumn("gold_per_min", F.col("gold_earned") / minutes)
    .withColumn(
        "gold_share", F.col("gold_earned") / F.greatest(F.col("team_gold_earned"), F.lit(1))
    )
    .withColumn("damage_per_min", F.col("total_damage_dealt_to_champions") / minutes)
    .withColumn("damage_taken_per_min", F.col("total_damage_taken") / minutes)
    .withColumn(
        "damage_share",
        F.col("total_damage_dealt_to_champions")
        / F.greatest(F.col("team_damage_to_champions"), F.lit(1)),
    )
    .withColumn(
        "damage_taken_share",
        F.col("total_damage_taken") / F.greatest(F.col("team_damage_taken"), F.lit(1)),
    )
    .withColumn(
        "damage_efficiency",
        F.col("total_damage_dealt_to_champions")
        / F.greatest(F.col("total_damage_taken"), F.lit(1)),
    )
    .withColumn("vision_score_per_min", F.col("vision_score") / minutes)
    .withColumn("wards_placed_per_min", F.col("wards_placed") / minutes)
    .withColumn("wards_killed_per_min", F.col("wards_killed") / minutes)
    .withColumn("vision_wards_bought_per_min", F.col("vision_wards_bought_in_game") / minutes)
    .withColumn("objective_damage_per_min", F.col("damage_dealt_to_objectives") / minutes)
)

match_tier_df = (
    silver_df.select("match_id", "player_id")
    .join(seeds_df, on="player_id", how="inner")
    .groupBy("match_id")
    .agg(F.first("tier").alias("tier"))
)
gold_benchmark_df = silver_df.join(match_tier_df, on="match_id", how="inner")

for table_name in ("benchmark_source", "natural_distribution"):
    (
        gold_benchmark_df.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"{CATALOG}.gold.{table_name}")
    )

print(f"Created benchmark sources ({gold_benchmark_df.count()} rows)")
