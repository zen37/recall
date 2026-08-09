# Databricks notebook source
# MAGIC %md
# MAGIC # Steps 1–2 · Ingest + Silver (US food recalls MVP)
# MAGIC
# MAGIC openFDA food + FSIS → bronze (raw) → `silver.recalls`. MVP semantics: **overwrite on re-run**, no
# MAGIC incremental / dedup-by-date / retries. Import this file into Databricks as a notebook.

# COMMAND ----------
# MAGIC %md ## Parameters

# COMMAND ----------
dbutils.widgets.text("catalog", "main")
dbutils.widgets.text("schema", "recalls_mvp")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
print("target:", f"{catalog}.{schema}")

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
fsis = fetch_fsis()
print("openFDA:", len(openfda), "| FSIS:", len(fsis))

# COMMAND ----------
# MAGIC %md ### Confirm FSIS field names (marked `?` in the plan)
# MAGIC Eyeball this once, then adjust `map_fsis` below if a field name differs.

# COMMAND ----------
print(json.dumps(fsis[0], indent=2)[:2500] if fsis else "no FSIS records returned")

# COMMAND ----------
# MAGIC %md ## 2a · Bronze (raw JSON, overwrite)
# MAGIC Raw payload kept as a JSON string so schema drift never breaks ingestion.

# COMMAND ----------
from pyspark.sql import Row

ingested_at = datetime.now(timezone.utc)

def to_bronze(records, source):
    rows = [Row(source=source, ingested_at=ingested_at, raw=json.dumps(rec)) for rec in records]
    return spark.createDataFrame(rows)

to_bronze(openfda, "openfda_food").write.mode("overwrite").saveAsTable(
    f"{catalog}.{schema}.openfda_food_raw")
to_bronze(fsis, "fsis").write.mode("overwrite").saveAsTable(
    f"{catalog}.{schema}.fsis_raw")
print("bronze written")

# COMMAND ----------
# MAGIC %md ## 2b · Mapping helpers

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
    # Confirmed fields: field_recall_number, field_recall_date,
    # field_recall_classification, field_recall_reason.
    # `?` fields use fallbacks — adjust after checking the inspect cell.
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

# COMMAND ----------
# MAGIC %md ## 2c · Build silver from bronze (overwrite)

# COMMAND ----------
from pyspark.sql.types import (
    StructType, StructField, StringType, DateType, TimestampType)

def load_and_map(table, mapper):
    rows = spark.table(table).select("raw").collect()
    out = []
    for x in rows:
        rec = json.loads(x["raw"])
        m = mapper(rec)
        m["ingested_at"] = ingested_at
        out.append(m)
    return out

records = (load_and_map(f"{catalog}.{schema}.openfda_food_raw", map_openfda)
           + load_and_map(f"{catalog}.{schema}.fsis_raw", map_fsis))

# drop rows whose natural key was missing
records = [m for m in records if m["recall_id"] and not m["recall_id"].endswith("::None")]

silver_schema = StructType([
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

cols = [f.name for f in silver_schema.fields]
tuples = [tuple(m.get(c) for c in cols) for m in records]

silver_df = spark.createDataFrame(tuples, silver_schema).dropDuplicates(["recall_id"])
(silver_df.write.mode("overwrite").option("overwriteSchema", "true")
    .saveAsTable(f"{catalog}.{schema}.recalls"))

print("silver rows:", silver_df.count())

# COMMAND ----------
# MAGIC %md ## Quick check

# COMMAND ----------
display(spark.table(f"{catalog}.{schema}.recalls").groupBy("source", "category").count())

# COMMAND ----------
display(spark.table(f"{catalog}.{schema}.recalls")
        .select("source", "recall_date", "classification", "brand", "title")
        .orderBy("recall_date", ascending=False).limit(10))
