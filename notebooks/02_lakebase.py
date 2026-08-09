# Databricks notebook source
# MAGIC %md
# MAGIC # Step 2 · Lakebase tables (US food recalls MVP)
# MAGIC
# MAGIC Load `{catalog}.{schema}.unified` → Lakebase table **`recalls`**, and create an empty
# MAGIC **`watchlist`** table. Lakebase is managed Postgres; we reach it with plain `psycopg2`.
# MAGIC
# MAGIC **Preconditions (already done by a human — this notebook does NOT create them):**
# MAGIC - The Lakebase instance exists and is running.
# MAGIC - Its full DSN is stored as one secret: scope `lakebase-recalls`, key `lakebase-recalls-url`,
# MAGIC   value `postgresql://agent:<pw>@<host>/databricks_postgres?sslmode=require`. The `agent`
# MAGIC   role is a superuser, so no GRANTs are needed.
# MAGIC
# MAGIC Connection is via `app/lakebase.py` (`connect()`), the same helper Steps 4-5 import — so the
# MAGIC path that works here is the path they'll use.
# MAGIC
# MAGIC **Load path:** psycopg2 bulk-insert (read `unified` into pandas, INSERT). *Not* a UC synced
# MAGIC table — that's more managed-resource setup than the MVP needs.

# COMMAND ----------
# MAGIC %md ## 0 · Install the Postgres driver
# MAGIC `psycopg2-binary` isn't on the serverless image by default. Install, then restart Python so
# MAGIC the import is visible. (`databricks-sdk` is preinstalled — the helper's fallback path uses it.)

# COMMAND ----------
# MAGIC %pip install psycopg2-binary
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## Parameters
# MAGIC Same source table as Step 1. Only reads it — never writes it back.

# COMMAND ----------
dbutils.widgets.text("catalog", "adw")
dbutils.widgets.text("schema", "recalls")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
source_table = f"{catalog}.{schema}.unified"
print("source table:", source_table)

# COMMAND ----------
# MAGIC %md ## 1 · Connect via the shared helper
# MAGIC `app/lakebase.py` reads the DSN secret and hands back a `psycopg2` connection. Importing it
# MAGIC here (rather than inlining `psycopg2.connect`) means the notebook and the app share one
# MAGIC connection code path — if it works below, it works in Steps 4-5.

# COMMAND ----------
import sys

sys.path.append("../app")  # Git-folder layout: notebooks/ and app/ are siblings
from lakebase import connect  # noqa: E402

# Prove the connection + credentials before touching any tables.
with connect() as conn:
    with conn.cursor() as cur:
        cur.execute("select current_user, current_database(), version()")
        who, db, ver = cur.fetchone()
print("connected as:", who, "| db:", db)
print(ver)

# COMMAND ----------
# MAGIC %md ## 2 · Read `unified` into pandas
# MAGIC 1000 openFDA food rows — small enough for a single in-memory bulk insert, no batching needed.

# COMMAND ----------
import pandas as pd  # noqa: E402

# Fix column order explicitly so it matches the CREATE TABLE / INSERT below,
# and so this doesn't silently break if `unified` column order ever shifts.
COLS = [
    "recall_id",
    "source",
    "category",
    "title",
    "product_description",
    "brand",
    "recall_date",
    "classification",
    "reason_hazard",
    "source_url",
    "ingested_at",
]

pdf = spark.table(source_table).select(*COLS).toPandas()
print("rows read from unified:", len(pdf))
print("distinct recall_id:", pdf["recall_id"].nunique())


def _clean(v):
    # psycopg2 adapts pandas.Timestamp (a datetime subclass) fine, but NaT / NaN
    # must become SQL NULL, not the string "NaT".
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


rows = [tuple(_clean(v) for v in r) for r in pdf.itertuples(index=False, name=None)]
print("rows prepared for insert:", len(rows))

# COMMAND ----------
# MAGIC %md ## 3 · Create `recalls` and bulk-insert
# MAGIC `DROP … CREATE` makes the load idempotent — re-running mirrors `unified` cleanly, matching
# MAGIC Step 1's overwrite semantics. Schema maps 1:1 to the 11 `unified` columns; `recall_id` is the
# MAGIC primary key (it's unique — Step 1 asserts it).

# COMMAND ----------
from psycopg2.extras import execute_values  # noqa: E402

CREATE_RECALLS = """
CREATE TABLE recalls (
    recall_id           text PRIMARY KEY,
    source              text,
    category            text,
    title               text,
    product_description text,
    brand               text,
    recall_date         date,
    classification      text,
    reason_hazard       text,
    source_url          text,
    ingested_at         timestamptz
)
"""

conn = connect()
try:
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("DROP TABLE IF EXISTS recalls")
        cur.execute(CREATE_RECALLS)
        execute_values(
            cur,
            f"INSERT INTO recalls ({', '.join(COLS)}) VALUES %s",
            rows,
            page_size=1000,
        )
    conn.commit()
    print("inserted rows:", len(rows))
except Exception:
    conn.rollback()
    raise
finally:
    conn.close()

# COMMAND ----------
# MAGIC %md ## 4 · Create `watchlist` (empty)
# MAGIC `CREATE TABLE IF NOT EXISTS` — so re-running Step 2 never wipes rows that the agent/app add in
# MAGIC later steps. Column is `user_email` (not `user`) — `user` is a reserved word in Postgres, and
# MAGIC `user_email` also matches the `X-Forwarded-Email` identity the app uses in Step 5.

# COMMAND ----------
CREATE_WATCHLIST = """
CREATE TABLE IF NOT EXISTS watchlist (
    user_email text,
    term       text,
    created_at timestamptz DEFAULT now()
)
"""

conn = connect()
try:
    with conn.cursor() as cur:
        cur.execute(CREATE_WATCHLIST)
    conn.commit()
    print("watchlist ready")
finally:
    conn.close()

# COMMAND ----------
# MAGIC %md ## Verify (done-when)
# MAGIC Read the numbers back from Postgres and **print them** — success is the output below, not the
# MAGIC absence of an error.
# MAGIC
# MAGIC - `recalls` count should be `> 0` (expect 1000).
# MAGIC - `watchlist` exists and is empty (count `0`), with the expected columns.

# COMMAND ----------
conn = connect()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM recalls")
        recalls_count = cur.fetchone()[0]

        cur.execute("SELECT count(*) FROM watchlist")
        watchlist_count = cur.fetchone()[0]

        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'watchlist' ORDER BY ordinal_position"
        )
        watchlist_cols = cur.fetchall()

        # A sample row, so 'it loaded' is visible, not just a count.
        cur.execute(
            "SELECT recall_id, source, brand, title FROM recalls "
            "ORDER BY recall_date DESC NULLS LAST LIMIT 3"
        )
        sample = cur.fetchall()
finally:
    conn.close()

print("SELECT count(*) FROM recalls   ->", recalls_count)
print("SELECT count(*) FROM watchlist ->", watchlist_count)
print("watchlist columns:", watchlist_cols)
print("sample recalls rows:")
for r in sample:
    print("  ", r)

assert recalls_count > 0, "recalls is empty — load failed"
assert watchlist_count == 0, "watchlist should be empty after Step 2"
print("\nStep 2 checks PASSED" if recalls_count > 0 and watchlist_count == 0 else "\nStep 2 checks FAILED")
