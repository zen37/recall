# Databricks notebook source
# MAGIC %md
# MAGIC # Step 3 · Vector Search (US food recalls MVP)
# MAGIC
# MAGIC Semantic index over the recall narrative text. **Design decision (fixed):** the index syncs the
# MAGIC full *display* column set, so the app renders search results in one call — no join back to
# MAGIC Postgres. This is deliberately **not** an IDs-only index.
# MAGIC
# MAGIC Pipeline: `adw.recalls.unified` → `unified_search` (Delta table, `search_text` + CDF) →
# MAGIC Vector Search endpoint → Delta-sync index (managed embeddings) → `similarity_search`.
# MAGIC
# MAGIC **Idempotent / safe to re-run:** `CREATE OR REPLACE TABLE` for the source; endpoint and index
# MAGIC creation are guarded (reused if they already exist, and an existing index is re-synced).
# MAGIC
# MAGIC **Rebuild dependency:** re-running `01_ingest.py` overwrites `unified`. After any re-ingest,
# MAGIC **re-run the `CREATE OR REPLACE TABLE unified_search` cell below** — the Delta-sync index then
# MAGIC picks the changes up via Change Data Feed (or trigger a sync, see the index cell).

# COMMAND ----------
# MAGIC %md ## 0 · Install the Vector Search SDK
# MAGIC `databricks-vectorsearch` isn't on the serverless image by default. Install, then restart
# MAGIC Python so the import is visible. (`databricks-sdk` is preinstalled — used to list serving
# MAGIC endpoints below.)

# COMMAND ----------
# MAGIC %pip install databricks-vectorsearch
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## Parameters
# MAGIC Same source catalog/schema as Steps 1-2. Endpoint / index / embedding-model names are widgets
# MAGIC so nothing is hardcoded; the embedding endpoint is *verified against the workspace serving
# MAGIC list* before use (next-but-one cell).

# COMMAND ----------
dbutils.widgets.text("catalog", "adw")
dbutils.widgets.text("schema", "recalls")
dbutils.widgets.text("vs_endpoint", "recalls_vs")
dbutils.widgets.text("embedding_endpoint", "databricks-gte-large-en")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
vs_endpoint = dbutils.widgets.get("vs_endpoint")
embedding_endpoint = dbutils.widgets.get("embedding_endpoint")

source_table = f"{catalog}.{schema}.unified"
search_table = f"{catalog}.{schema}.unified_search"
index_name = f"{catalog}.{schema}.unified_search_index"

# The display set — what the index returns so the app renders results without a Postgres join.
# recall_id is the primary key; search_text is the embedding source. Neither is a display field,
# but the PK and embedding column are always synced regardless.
DISPLAY_COLS = [
    "recall_id",
    "title",
    "brand",
    "recall_date",
    "classification",
    "reason_hazard",
    "source",
    "source_url",
]

print("source table :", source_table)
print("search table :", search_table)
print("index        :", index_name)
print("vs endpoint  :", vs_endpoint)
print("embed model  :", embedding_endpoint)

# COMMAND ----------
# MAGIC %md ## 1 · Build the search source table (`unified_search`)
# MAGIC A Delta **table**, not a view — Change Data Feed (needed for a Delta-sync index) can't be
# MAGIC enabled on a view. `search_text = product_description + reason_hazard` is what gets embedded.

# COMMAND ----------
spark.sql(f"""
    CREATE OR REPLACE TABLE {search_table} AS
    SELECT *, concat_ws(' ', product_description, reason_hazard) AS search_text
    FROM {source_table}
""")

n = spark.table(search_table).count()
print(f"{search_table}: {n} rows")
# How many rows have empty search_text (both source fields null) — those embed as "" and are
# effectively invisible to semantic search. Matches the Step 1 post-write warning.
empty = spark.sql(
    f"SELECT count(*) c FROM {search_table} WHERE nullif(trim(search_text), '') IS NULL"
).collect()[0]["c"]
print(f"rows with empty search_text: {empty}")

# COMMAND ----------
# MAGIC %md ## 2 · Enable Change Data Feed
# MAGIC Required for the Delta-sync index to track changes incrementally.

# COMMAND ----------
spark.sql(f"ALTER TABLE {search_table} SET TBLPROPERTIES (delta.enableChangeDataFeed = true)")
props = {r["key"]: r["value"] for r in spark.sql(f"SHOW TBLPROPERTIES {search_table}").collect()}
print("delta.enableChangeDataFeed =", props.get("delta.enableChangeDataFeed"))

# COMMAND ----------
# MAGIC %md ## 3 · Verify the embedding endpoint exists (don't hardcode a guess)
# MAGIC The managed embedding endpoint name is workspace-specific and moves fast. Confirm the widget
# MAGIC value is actually served here *before* creating the index — otherwise the index create fails
# MAGIC minutes later with a less obvious error.

# COMMAND ----------
from databricks.sdk import WorkspaceClient  # noqa: E402

w = WorkspaceClient()
served = [e.name for e in w.serving_endpoints.list()]
print(f"{len(served)} serving endpoints in this workspace")

# Foundation-model embedding endpoints are typically prefixed 'databricks-' and mention gte/bge/embed.
candidates = sorted(
    n for n in served
    if any(k in n.lower() for k in ("gte", "bge", "embed", "qwen"))
)
print("likely embedding endpoints:", candidates)

assert embedding_endpoint in served, (
    f"embedding endpoint '{embedding_endpoint}' is not served in this workspace.\n"
    f"Pick one of the likely embedding endpoints above (e.g. databricks-gte-large-en / "
    f"databricks-bge-large-en) and set the 'embedding_endpoint' widget accordingly."
)
print(f"OK: '{embedding_endpoint}' is available")

# COMMAND ----------
# MAGIC %md ## 4 · Vector Search endpoint (create if absent)
# MAGIC Reused if it already exists. A brand-new endpoint can take ~10-20 min to come up;
# MAGIC `create_endpoint_and_wait` blocks until it's ready.

# COMMAND ----------
from databricks.vector_search.client import VectorSearchClient  # noqa: E402

vsc = VectorSearchClient(disable_notice=True)

existing_endpoints = [e.get("name") for e in vsc.list_endpoints().get("endpoints", [])]
if vs_endpoint in existing_endpoints:
    print(f"endpoint '{vs_endpoint}' already exists — reusing")
else:
    print(f"creating endpoint '{vs_endpoint}' (this can take several minutes)...")
    vsc.create_endpoint_and_wait(name=vs_endpoint, endpoint_type="STANDARD")
    print("endpoint ready")

# COMMAND ----------
# MAGIC %md ## 5 · Delta-sync index (create if absent, else re-sync)
# MAGIC `columns_to_sync` = the display set, so results carry every field the app renders — not just
# MAGIC IDs. `pipeline_type="TRIGGERED"`: sync on demand (cheaper than CONTINUOUS for this MVP);
# MAGIC re-running the notebook triggers a fresh sync so re-ingested data flows through.

# COMMAND ----------
def index_exists(client, endpoint, name):
    try:
        client.get_index(endpoint_name=endpoint, index_name=name).describe()
        return True
    except Exception:
        return False


if index_exists(vsc, vs_endpoint, index_name):
    print(f"index '{index_name}' exists — triggering a sync to pick up source changes")
    index = vsc.get_index(endpoint_name=vs_endpoint, index_name=index_name)
    try:
        index.sync()
    except Exception as e:
        # A CREATE OR REPLACE of the source can break CDF continuity; if a sync can't proceed,
        # drop the index and re-run this cell to rebuild it from scratch.
        print("sync raised (source may have been replaced):", e)
        print("If results look stale, drop the index and re-run: "
              f"vsc.delete_index(endpoint_name='{vs_endpoint}', index_name='{index_name}')")
else:
    print(f"creating index '{index_name}' (embeddings computed by Databricks; a few minutes)...")
    index = vsc.create_delta_sync_index_and_wait(
        endpoint_name=vs_endpoint,
        index_name=index_name,
        source_table_name=search_table,
        pipeline_type="TRIGGERED",
        primary_key="recall_id",
        embedding_source_column="search_text",
        embedding_model_endpoint_name=embedding_endpoint,
        columns_to_sync=DISPLAY_COLS,
    )
    print("index created")

# COMMAND ----------
# MAGIC %md ## Verify (done-when) · poll until ONLINE
# MAGIC Success is the printed status, not the absence of an error. Poll `describe()` until the index
# MAGIC reports ready.

# COMMAND ----------
import time  # noqa: E402

index = vsc.get_index(endpoint_name=vs_endpoint, index_name=index_name)

deadline = time.time() + 900  # 15 min
status = {}
while time.time() < deadline:
    status = index.describe().get("status", {})
    state = status.get("detailed_state") or status.get("state")
    ready = status.get("ready")
    print(f"status: detailed_state={state} ready={ready}")
    if ready:
        break
    time.sleep(20)

print("\nfinal index status:", status)
assert status.get("ready"), "index did not reach a ready/ONLINE state within the timeout"
print("index is ONLINE")

# COMMAND ----------
# MAGIC %md ## Verify (done-when) · similarity_search returns full display fields
# MAGIC Query with `columns` = the display set and confirm every field comes back populated (not just
# MAGIC `recall_id`).

# COMMAND ----------
result = index.similarity_search(
    query_text="listeria in deli meat",
    columns=DISPLAY_COLS,
    num_results=5,
)

manifest_cols = [c["name"] for c in result["manifest"]["columns"]]
rows = result["result"]["data_array"]

print("returned columns:", manifest_cols)
print(f"rows returned: {len(rows)}\n")
for r in rows:
    rec = dict(zip(manifest_cols, r))
    score = rec.get("__db_score") or (r[-1] if len(r) > len(DISPLAY_COLS) else None)
    print(f"- {rec.get('title')}  [{rec.get('brand')}]")
    print(f"    recall_id={rec.get('recall_id')} date={rec.get('recall_date')} "
          f"class={rec.get('classification')} source={rec.get('source')} score={score}")
    print(f"    reason: {(rec.get('reason_hazard') or '')[:160]}")

assert len(rows) > 0, "similarity_search returned no rows"
# Every display field present in the manifest (i.e. not an IDs-only index).
for col in DISPLAY_COLS:
    assert col in manifest_cols, f"display column '{col}' missing from index results"
print("\nStep 3 checks PASSED: index ONLINE and returns the full display set")
