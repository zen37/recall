# Implementation — US Recalls MVP

Living build log. Do the steps in order. Each step has **what to do → how to verify → done-when**.

> Note: Steps 3–5 use fast-moving Databricks features (Lakebase, Vector Search, Mosaic AI Agent Framework, Apps). The flow below is correct in shape; confirm exact UI paths / SDK names against current Databricks docs as you go.

## Environment (confirmed)

- **Unity Catalog:** enabled — metastore `azure:westus:5f6d0df9-cb9c-4421-94f2-f2be880f5bef`.
- **Catalog / schema:** `adw` / `recalls`. The notebook creates the schema. The one data table is `adw.recalls.unified`.
- **No medallion for the MVP** — a single table, `adw.recalls.unified`. Everything downstream reads from it.
- **Code:** Git repo cloned as a Databricks **Git folder** (see `README.md`). Notebooks run in order from `notebooks/`.

## Build order

1. **Ingest** — `notebooks/01_ingest.py` ← current
2. Lakebase tables (load `unified` → `recalls` + create `watchlist`)
3. Vector Search index
4. AI agent (2 tools: 1 read, 1 write)
5. Databricks App (frontend)

---

## Phasing: openFDA-only vertical slice first

**Phase 1 (current):** openFDA only, all the way through Steps 1–5, in the browser. FSIS is the
one genuinely unknown data source (unconfirmed field names); Lakebase, Vector Search, the agent,
and the app are the genuinely unknown *plumbing* (never wired together, fast-moving surfaces). Keep
those separate — one clean, predictable source through the whole pipeline first, so a break points
at the plumbing, not the data.

In `01_ingest.py`, `fsis = fetch_fsis()` is commented out and replaced with `fsis = []`.
`fetch_fsis` and `map_fsis` stay in the file, unused. Since the FSIS branch of every downstream
list comprehension iterates an empty list, nothing else in the notebook needed to change.

**Phase 1 checkpoint (done-when):** `unified` has only `openfda_food / food` rows, and that data is
visibly flowing end to end — through Lakebase, into the Vector Search index, through the agent, and
showing up in the app in the browser.

**Phase 2:** flip FSIS back on — uncomment `fetch_fsis()` in the fetch cell, run the inspect cell,
fix the four `?` fields in `map_fsis` (`field_title`/`field_summary`/`field_establishment`/press
release URL) if they differ from the fallbacks, re-run ingest. No schema change — `unified` keeps
its columns exactly as-is. Everything downstream (Lakebase sync, Vector Search index, agent, app)
just picks up the extra `fsis / meat_poultry` rows automatically.

---

## Step 1 — Ingest

Fetch openFDA food + FSIS → map → **one table** `adw.recalls.unified`. Overwrite on re-run.
Currently running **phase 1 (openFDA only)** — see phasing note above.

### Prerequisites
- Azure Databricks workspace with Unity Catalog — ✅ confirmed.
- Writable catalog — ✅ `adw`. The notebook creates `adw.recalls` itself.
- Compute (serverless or a cluster) with **outbound internet** to `api.fda.gov` and `www.fsis.usda.gov`. If egress is locked down (VNet injection without a NAT/firewall allow-rule), allow those two hosts first.
- Repo cloned as a Git folder (preferred), or `notebooks/01_ingest.py` imported.

### Execute
1. **Get the notebook in** — preferred: Workspace → **Create → Git folder** → clone the repo → open `notebooks/01_ingest.py`. (Alternative: Workspace → Import → File.)
2. **Attach** to serverless or a running cluster.
3. **Set the widgets:** `catalog` = `adw`, `schema` = `recalls`.
4. **Run all.**

### Verify (done-when)
- Fetch cell prints roughly `openFDA: 1000 | FSIS: 0` (phase 1 — FSIS disabled).
- FSIS inspect cell prints `no FSIS records returned` (expected in phase 1).
- "Quick check" shows a `groupBy(source, category)` count with `openfda_food / food` only (phase 1).
- Table exists: `adw.recalls.unified`.

### If something's off
- **`openFDA: 0`** → outbound internet blocked, or (rarely) the unauth daily rate limit. Check egress first.
- **Permission error on `CREATE SCHEMA`** → request `CREATE SCHEMA` on `adw`, or point the widget at a schema you own.

### Phase 2 additions (once phase 1 checkpoint is done)
- **FSIS count 0 / error** → response envelope may have changed. Print `type(data)` in `fetch_fsis` and report.
- **Null `brand`/`title` for FSIS** → a `?` field name is wrong; fix in `map_fsis` from the inspect output.
- After fixing, "Quick check" should show both `openfda_food / food` and `fsis / meat_poultry`.

### Output
`adw.recalls.unified` — the single table every later step reads from.

---

## Step 2 — Lakebase tables

Stand up a Lakebase (managed Postgres) instance and put `recalls` + `watchlist` in it.

### Execute
1. **Create a Lakebase instance** — Databricks UI → Compute (or Catalog) → **Database Instances / Lakebase** → create. Note host, port `5432`, database name.
2. **Load `recalls`** — cleanest path: register `adw.recalls.unified` as a **synced table** into Lakebase (managed, auto-syncs from UC). Fallback for MVP: read `unified` into pandas and bulk-`INSERT` via `psycopg2`.
3. **Create `watchlist`** — `CREATE TABLE watchlist (user text, term text, created_at timestamptz DEFAULT now());`
4. Save connection details as a secret / app resource for later steps.

### Verify (done-when)
- `SELECT count(*) FROM recalls;` returns > 0 from a notebook or `psql`.
- `watchlist` exists (empty).

### Notes
- Use a `lakebase.py` connection-helper (host/db/token) so Steps 4–5 reuse it.
- Writes will use the app/agent identity later — keep that principal in mind when granting Postgres access.

---

## Step 3 — Vector Search

Semantic index over the recall narrative text.

### Execute
1. **Add a search column** — create a view/table `adw.recalls.unified_search` = `SELECT *, concat_ws(' ', product_description, reason_hazard) AS search_text FROM adw.recalls.unified`.
2. **Enable Change Data Feed** on the source (required for Delta-sync index): `ALTER TABLE adw.recalls.unified_search SET TBLPROPERTIES (delta.enableChangeDataFeed = true);`
3. **Create a Vector Search endpoint** — UI → Compute → Vector Search, or the `databricks-vectorsearch` SDK.
4. **Create a Delta-sync index** with primary key `recall_id`, `embedding_source_column = search_text`, and a managed embedding model endpoint (e.g. `databricks-gte-large-en`). Databricks computes the embeddings.

### Verify (done-when)
- Index status is **ONLINE**.
- `index.similarity_search(query_text="listeria in deli meat", columns=["recall_id","title","brand"], num_results=5)` returns relevant recalls.

### Notes
- `recall_id` must be unique (it is — the notebook dedups on it).
- Confirm the current managed embedding endpoint name in your workspace's model-serving list.

---

## Step 4 — AI agent

Agent with two tools: one read, one write.

### Execute
1. **Tools (plain Python):**
   - `search_recalls(query: str)` → calls the Vector Search index `similarity_search`, returns top matches. *(read)*
   - `add_to_watchlist(user: str, term: str)` → `INSERT` into Lakebase `watchlist` via `psycopg2`. *(write)*
2. **Agent** — wrap the tools with the Mosaic AI Agent Framework (or LangGraph) using an LLM on Model Serving (a Databricks foundation-model endpoint). System prompt: *"Help users find product recalls and manage their watchlist. Use search_recalls to answer questions; use add_to_watchlist when asked to track something."*
3. **Register** the agent with MLflow; optionally deploy to a Model Serving endpoint for the app to call.

### Verify (done-when)
- "any recent listeria recalls?" → agent calls `search_recalls` and answers.
- "watch romaine lettuce for me" → agent calls `add_to_watchlist`; the row appears in Lakebase `watchlist`.

### Notes
- Foundation-model endpoint names and the agent-authoring API move fast — confirm against current docs.
- Give the agent's identity access to both the Vector Search index and the Lakebase DB.

---

## Step 5 — Databricks App

Minimal Flask + single-page frontend.

### Execute
1. **`app/app.py`** (Flask) — routes:
   - `GET /` → serve `static/index.html`
   - `POST /search` `{q}` → Vector Search `similarity_search` → JSON
   - `POST /chat` `{message}` → invoke the agent → reply
   - user from the `X-Forwarded-Email` header
2. **`app/app.yaml`** — run command, e.g. `command: ["python", "app.py"]`; bind `0.0.0.0:$DATABRICKS_APP_PORT`.
3. **`app/requirements.txt`** — `flask`, `databricks-vectorsearch`, `psycopg2-binary`, `databricks-sdk` (+ `mlflow` if invoking the agent endpoint).
4. **`app/static/index.html`** — a search box + results list + a chat box.
5. **Deploy** — Databricks Apps → create app from the `app/` folder (or `databricks apps deploy`).

### Verify (done-when)
- App URL opens; you can run a search, chat with the agent, and add a watchlist item that lands in Lakebase.

### Notes
- The app's service principal needs access to the Vector Search index and Lakebase.
- Keep the frontend trivial — one HTML file, fetch() to the two endpoints. No build step.
