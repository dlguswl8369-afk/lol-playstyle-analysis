# Databricks notebook source
# /// script
# [tool.databricks.environment]
# environment_version = "1"
# ///
"""Bootstrap the item-role lookup without depending on champion profiles."""

from pyspark.sql import functions as F

CATALOG = "lol_insight"

SOURCE_PATH = "abfss://lol-data@5dt2ndteam3.dfs.core.windows.net/reference/item_roles.csv"

TARGET_TABLE = f"{CATALOG}.gold.item_role_profile"

VALID_ROLES = ["FRONTLINE", "BRUISER", "BURST_CARRY", "DPS_CARRY", "UTILITY"]

item_roles_df = (
    spark.read.option("header", True)
    .option("inferSchema", True)
    .csv(SOURCE_PATH)
    .select(F.col("item_id").cast("int").alias("item_id"), F.trim("roles").alias("roles"))
    .filter(F.col("item_id").isNotNull() & F.col("roles").isNotNull())
    .dropDuplicates(["item_id"])
)

if item_roles_df.limit(1).count() == 0:
    raise ValueError(f"No valid item roles found at {SOURCE_PATH}")

invalid_roles = item_roles_df.filter(
    ~F.expr(
        "forall(transform(split(roles, ','), x -> trim(x)), "
        f"x -> x in ({','.join(repr(role) for role in VALID_ROLES)}))"
    )
)

if invalid_roles.limit(1).count() > 0:
    raise ValueError("item_roles.csv contains a role outside the five v8.2 tactical roles")

(
    item_roles_df.write.format("delta")
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(TARGET_TABLE)
)

print(f"Created {TARGET_TABLE} ({item_roles_df.count()} rows)")
