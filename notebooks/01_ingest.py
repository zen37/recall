# Databricks notebook source
# MAGIC %md
# MAGIC # Step 1 · Ingest (US food recalls MVP)
# MAGIC
# MAGIC openFDA food + FSIS → **one table** `{catalog}.{schema}.unified`. No bronze/silver — fetch, map,
# MAGIC union, write. Overwrite on re-run. Import this file into Databricks as a notebook.
# MAGIC
# MAGIC **Phase 1 (current): openFDA only**, all the way through the pipeline. FSIS fetch is disabled
# MAGIC below — `fsis = []` — but `fetch_fsis`/`map_fsis` stay in the file untouched. Turning FSIS on in
# MAGIC phase 2 is a one-line change (see the fetch cell); nothing else in this notebook or downstream
# MAGIC needs to change, because the unified schema already has `source`/`category` for it.

# COMMAND ----------
# MAGIC %md ## Parameters

# COMMAND ----------
dbutils.widgets.text("catalog", "adw")
dbutils.widgets.text("schema", "recalls")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
print("target table:", f"{catalog}.{schema}.unified")

# COMMAND ----------
# MAGIC %md ## 1 · Fetch (plain requests, no auth)

# COMMAND ----------
import requests, json
from datetime import datetime, timezone

def http_get_json(url, params=None):
    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    return r.json()

def fetch_openfda_food(limit=1000):
    # 1000 most recent recalls — plenty for the MVP, no pagination needed.
    payload = http_get_json(
        "https://api.fda.gov/food/enforcement.json",
        {"sort": "report_date:desc", "limit": limit},
    )
    return payload.get("results", [])

def fetch_fsis():
    data = http_get_json("https://www.fsis.usda.gov/fsis/api/recall/v/1")
    return data if isinstance(data, list) else data.get("data", [])

openfda = fetch_openfda_food()
fsis = []          # fetch_fsis()  # PHASE 1: disabled. Re-enable in phase 2 once openFDA is
                    # proven end to end (unified -> Lakebase -> Vector Search -> agent -> app).
print("openFDA:", len(openfda), "| FSIS:", len(fsis))

# COMMAND ----------
# MAGIC %md ### Confirm FSIS field names (marked `?` below)
# MAGIC Eyeball this once, then fix `map_fsis` if a field name differs.
# MAGIC Phase 1: this will print "no FSIS records returned" since `fsis` is disabled above — expected.

# COMMAND ----------
print(json.dumps(fsis[0], indent=2)[:2500] if fsis else "no FSIS records returned")

# COMMAND ----------
# MAGIC %md ## 2 · Map both sources to the unified shape

# COMMAND ----------
def parse_date(val):
    if not val:
        return None
    s = str(val).strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None

def first(rec, *keys):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []):
            return v
    return None

def map_openfda(r):
    desc = r.get("product_description")
    return {
        "recall_id": f"openfda_food::{r.get('recall_number')}",
        "source": "openfda_food",
        "category": "food",
        "title": (desc or "")[:120] or None,
        "product_description": desc,
        "brand": r.get("recalling_firm"),
        "recall_date": parse_date(r.get("report_date")),
        "classification": r.get("classification"),
        "reason_hazard": r.get("reason_for_recall"),
        "source_url": None,  # openFDA has no per-record page
    }

def map_fsis(r):
    # Confirmed: field_recall_number, field_recall_date,
    # field_recall_classification, field_recall_reason.
    # `?` fields use fallbacks — adjust after checking the inspect cell.
    # Not called in phase 1 (fsis == []) — retained here so phase 2 is additive, not a rewrite.
    return {
        "recall_id": f"fsis::{r.get('field_recall_number')}",
        "source": "fsis",
        "category": "meat_poultry",
        "title": first(r, "field_title", "title"),
        "product_description": first(r, "field_summary", "field_product_items"),
        "brand": first(r, "field_establishment", "field_company_name"),
        "recall_date": parse_date(first(r, "field_recall_date")),
        "classification": r.get("field_recall_classification"),
        "reason_hazard": r.get("field_recall_reason"),
        "source_url": first(r, "field_press_release", "url"),
    }

ingested_at = datetime.now(timezone.utc)

records = [map_openfda(r) for r in openfda] + [map_fsis(r) for r in fsis]
for m in records:
    m["ingested_at"] = ingested_at

# drop rows whose natural key was missing
records = [m for m in records if m["recall_id"] and not m["recall_id"].endswith("::None")]
print("mapped rows:", len(records))

# COMMAND ----------
# MAGIC %md ## 3 · Write the unified table (overwrite)

# COMMAND ----------
from pyspark.sql.types import (
    StructType, StructField, StringType, DateType, TimestampType)

unified_schema = StructType([
    StructField("recall_id", StringType()),
    StructField("source", StringType()),
    StructField("category", StringType()),
    StructField("title", StringType()),
    StructField("product_description", StringType()),
    StructField("brand", StringType()),
    StructField("recall_date", DateType()),
    StructField("classification", StringType()),
    StructField("reason_hazard", StringType()),
    StructField("source_url", StringType()),
    StructField("ingested_at", TimestampType()),
])

cols = [f.name for f in unified_schema.fields]
tuples = [tuple(m.get(c) for c in cols) for m in records]

df = spark.createDataFrame(tuples, unified_schema).dropDuplicates(["recall_id"])
(df.write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(f"{catalog}.{schema}.unified"))

print("unified rows:", df.count())

# COMMAND ----------
# MAGIC %md ## Quick check

# COMMAND ----------
display(spark.table(f"{catalog}.{schema}.unified").groupBy("source", "category").count())

# COMMAND ----------
display(spark.table(f"{catalog}.{schema}.unified")
        .select("source", "recall_date", "classification", "brand", "title")
        .orderBy("recall_date", ascending=False).limit(10))

# COMMAND ----------
# MAGIC %md ## Post-write checks
# MAGIC
# MAGIC Runs on every ingest. Two hard asserts (things that break Step 3) and one soft warning.
# MAGIC
# MAGIC - `recall_id` must be unique — it's the primary key of the Vector Search index.
# MAGIC - Step 3 builds `search_text` from `product_description` + `reason_hazard`. A row null in
# MAGIC   both embeds as an empty string and is effectively invisible to semantic search. Not fatal,
# MAGIC   so it warns rather than fails — but you want to know the number.

# COMMAND ----------
from pyspark.sql import functions as F

t = spark.table(f"{catalog}.{schema}.unified")

stats = t.select(
    F.count("*").alias("rows"),
    F.countDistinct("recall_id").alias("distinct_ids"),
    F.min("recall_date").alias("earliest"),
    F.max("recall_date").alias("latest"),
    F.sum(F.col("product_description").isNull().cast("int")).alias("null_desc"),
    F.sum(F.col("reason_hazard").isNull().cast("int")).alias("null_reason"),
    F.sum((F.col("product_description").isNull() & F.col("reason_hazard").isNull())
          .cast("int")).alias("null_both"),
).collect()[0]

print(stats)

assert stats["rows"] > 0, "unified is empty — check the fetch cell"
assert stats["distinct_ids"] == stats["rows"], (
    f"recall_id not unique ({stats['distinct_ids']} distinct / {stats['rows']} rows) — "
    "Vector Search needs a unique primary key"
)

if stats["null_both"]:
    print(f"WARNING: {stats['null_both']} rows have neither product_description nor "
          "reason_hazard — these will be invisible to semantic search in Step 3")
else:
    print("OK: every row has search text")

print(f"date window: {stats['earliest']} -> {stats['latest']}")
print("NOTE: mode('overwrite') + the API's 1000 most recent means this window slides — "
      "older records drop off on re-run. Switch to MERGE if that matters.")