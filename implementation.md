# Implementation — US Recalls MVP

Living build log. Do the steps in order. Each step has **what to do → how to verify → done-when**. Only Step 1 is detailed now; later steps get filled in as we reach them.

## Build order

1. **Ingest + Silver** — `notebook_step1_2_ingest_silver.py` ← current
2. Lakebase tables (load `recalls` + create `watchlist`)
3. Vector Search index
4. AI agent (2 tools: 1 read, 1 write)
5. Databricks App (frontend)

---

## Step 1 — Ingest + Silver

Fetch openFDA food + FSIS → bronze → `silver.recalls`. Overwrite on re-run.

### Prerequisites
- Azure Databricks workspace with Unity Catalog.
- A catalog where you can create a schema (notebook default: `main`; change the widget if needed).
- Compute (serverless or a cluster) with **outbound internet** to `api.fda.gov` and `www.fsis.usda.gov`. If egress is locked down (VNet injection without a NAT/firewall allow-rule), allow those two hosts first.
- The file `notebook_step1_2_ingest_silver.py`.

### Execute
1. **Import the notebook:** Workspace → your folder → **Import** → **File** → select `notebook_step1_2_ingest_silver.py`. It lands as a notebook (cells split on `# COMMAND ----------`).
2. **Attach** it to serverless or a running cluster.
3. **Set the widgets** at the top: `catalog` (your catalog), `schema` (e.g. `recalls_mvp`).
4. **Run all.**

### Verify (done-when)
- The fetch cell prints roughly `openFDA: 1000 | FSIS: <a few hundred>`.
- The FSIS inspect cell prints one JSON record — glance at the field names. If `field_title` / `field_summary` / `field_establishment` / press-release URL differ from the fallbacks, edit `map_fsis` and re-run.
- "Quick check" shows a `groupBy(source, category)` count with both `openfda_food / food` and `fsis / meat_poultry`, plus a 10-row sample.
- These tables now exist: `{catalog}.{schema}.openfda_food_raw`, `.fsis_raw`, `.recalls`.

### If something's off
- **`openFDA: 0`** → outbound internet blocked, or (rarely) the unauthenticated daily rate limit was hit. Check egress first.
- **FSIS count 0 / error** → the response envelope may have changed. Print `type(data)` in `fetch_fsis` and report what it is.
- **Lots of null `brand`/`title` for FSIS** → a `?` field name is wrong; fix it in `map_fsis` using the inspect-cell output.
- **Permission error on `CREATE SCHEMA`** → point the `schema` widget at an existing schema you own.

### Output of this step
`{catalog}.{schema}.recalls` populated with food recalls from both sources — the table every later step reads from.

---

## Step 2 — Lakebase tables
_To be detailed once Step 1 is green._

## Step 3 — Vector Search
_TBD._

## Step 4 — AI agent
_TBD._

## Step 5 — Databricks App
_TBD._
