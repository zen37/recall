# Implementation — US Recalls MVP

Living build log. Do the steps in order. Each step has **what to do → how to verify → done-when**.

> Note: Steps 3–5 use fast-moving Databricks features (Lakebase, Vector Search, Mosaic AI Agent Framework, Apps). The flow below is correct in shape; confirm exact UI paths / SDK names against current Databricks docs as you go.

## Environment (confirmed)

- **Unity Catalog:** enabled — metastore `azure:westus:5f6d0df9-cb9c-4421-94f2-f2be880f5bef`.
- **Catalog / schema:** `adw` / `recalls`. The notebook creates the schema. The one source table is `adw.recalls.unified`.
- **No medallion for the MVP** — a single table, `adw.recalls.unified`. Everything downstream reads from it.
- **Lakebase:** instance running. Role `agent` (superuser, native password, non-expiring). DSN in
  Databricks secret `lakebase-recalls` / `lakebase-recalls-url`.
- **Code:** Git repo cloned as a Databricks **Git folder** (see `README.md`). Notebooks run in order from `notebooks/`.

## Phasing: openFDA-only vertical slice first

**Phase 1 (current):** openFDA only, all the way through Steps 1–5. FSIS is the one unknown data
source (unconfirmed field names); Lakebase, Vector Search, the agent, and the app are the unknown
*plumbing*. Prove one clean source through the whole pipeline first, so a break points at the
plumbing, not the data. In `01_ingest.py`, `fsis = []`; `fetch_fsis`/`map_fsis` stay, unused.

**Phase 2:** uncomment `fetch_fsis()`, run the inspect cell, fix the four `?` fields in `map_fsis`,
re-run ingest. No schema change — `unified` keeps its columns; downstream picks up the extra
`fsis / meat_poultry` rows automatically.

## Repo layout

```
notebooks/
  01_ingest.py         Step 1 — openFDA (+ FSIS phase 2) -> adw.recalls.unified
  02_lakebase.py       Step 2 — load recalls, create watchlist
  03_vector_search.py  Step 3 — search table, CDF, endpoint, index
  04_agent.py          Step 4 — wrap tools, register with MLflow
app/                   Step 5 — the deployment unit (Databricks Apps deploys ONLY this folder)
  app.py               Flask routes
  lakebase.py          Postgres connection helper (reads the DSN secret)
  tools.py             search_recalls, add_to_watchlist
  app.yaml
  requirements.txt
  static/index.html
docs/
README.md
```

Shared code (`lakebase.py`, `tools.py`) lives in `app/` because Apps deploys only that folder;
`04_agent.py` imports the tools via `sys.path.append("../app")`. Tools are plain importable Python so
you can call `search_recalls("listeria")` in a notebook cell and confirm it works *before* an LLM is
involved.

## Build order

1. ~~**Ingest** — `notebooks/01_ingest.py`~~ ✅ **done** (phase 1, openFDA only — 1000 rows)
2. ~~**Lakebase tables** — `recalls` (1000 rows) + `watchlist` (empty)~~ ✅ **done**
3. **Vector Search index** ← current
4. AI agent (2 tools: 1 read, 1 write)
5. Databricks App (frontend)

---

## Step 1 — Ingest ✅ done (phase 1)

openFDA food → `adw.recalls.unified`. Overwrite on re-run. FSIS disabled for phase 1.

**Measured result:** 1000 rows / 1000 distinct `recall_id`. Zero nulls in `product_description` and
`reason_hazard`. Date window 2025-11-12 → 2026-07-29 (~8.5 months). `recall_date` parsed as
`DateType`; `classification`/`brand`/`title` populated.

**Carry forward:** `title` is just the first 120 chars of `product_description`, often redundant with
`brand`. Handle in frontend rendering (Step 5), not the schema.

**Sliding window:** `mode("overwrite")` against the API's 1000 most recent means re-running drops the
oldest rows. Intentional for the MVP; same root cause as the notification gap (see Step 5 future work).

---

## Step 2 — Lakebase tables ✅ done

`recalls` + `watchlist` in Lakebase Postgres (`databricks_postgres`, `public` schema).

**Done:** `app/lakebase.py` reads secret `lakebase-recalls`/`lakebase-recalls-url` and returns a
`psycopg2.connect(dsn)` connection. `02_lakebase.py` bulk-loaded `recalls` (1000 rows) from
`adw.recalls.unified` and created `watchlist(user_email text, term text, created_at timestamptz DEFAULT now())`.

> Column is **`user_email`**, not `user` — `user` is a Postgres reserved word (unquoted it resolves
> to `current_user` and silently returns wrong data). Used unquoted everywhere downstream.

**Measured result:** `recalls` = 1000, `watchlist` = 0. Columns confirmed `user_email/term/created_at`.

**Note on `recalls` in Postgres:** with Step 3's index returning display columns directly (below),
the search path does **not** query Postgres `recalls` in the MVP. It's kept as a cheap
plain-SQL browse/escape-hatch table and has a real role in the future push-notification job
(matching new `recall_id`s against `watchlist`). Not load-bearing now — that's fine.

**Hardening (post-MVP):** `agent` is a superuser, which is more than the app needs. A dedicated
least-privilege role (CONNECT + CREATE + DML on the two tables) is the production move.

---

## Step 3 — Vector Search ← current

Semantic index over the recall narrative text. Notebook: `notebooks/03_vector_search.py`.

### Decision: index returns display columns (no Postgres join in the search path)

Recall records are small and static, so the index stores and returns everything the UI renders. One
call answers a search; the app never joins to Postgres to display a result. Keeps the search path to
a single system.

### Execute
1. **Build the search source as a Delta TABLE (not a view).**
   ```sql
   CREATE OR REPLACE TABLE adw.recalls.unified_search AS
   SELECT *, concat_ws(' ', product_description, reason_hazard) AS search_text
   FROM adw.recalls.unified;
   ```
   Must be a **table** — Delta-sync indexes require Change Data Feed, and CDF can't be enabled on a
   view. (Alternative: add `search_text` to `unified` in Step 1 and index `unified` directly. Not
   doing that now to avoid churning a done step; keep it in mind for phase 2.)
2. **Enable Change Data Feed:**
   ```sql
   ALTER TABLE adw.recalls.unified_search SET TBLPROPERTIES (delta.enableChangeDataFeed = true);
   ```
3. **Create a Vector Search endpoint** — UI → Compute → Vector Search, or the `databricks-vectorsearch` SDK.
4. **Create a Delta-sync index** on `unified_search`:
   - primary key `recall_id`
   - `embedding_source_column = search_text`
   - managed embedding endpoint (confirm the current name in your workspace's serving list — e.g.
     `databricks-gte-large-en`); Databricks computes the embeddings.
   - **Returned columns:** `recall_id, title, brand, recall_date, classification, reason_hazard,
     source, source_url` — the full display set, so search results render without a second query.

### Verify (done-when)
- Index status is **ONLINE**.
- `index.similarity_search(query_text="listeria in deli meat", columns=[<the display set>], num_results=5)`
  returns relevant recalls **with all display fields populated** (not just IDs).

### Notes / gotchas
- `recall_id` is unique (verified 1000/1000) — valid primary key.
- **Rebuild dependency:** re-running `01_ingest.py` overwrites `unified`, which makes
  `unified_search` stale. Re-run the `CREATE OR REPLACE TABLE` cell after any re-ingest; the
  Delta-sync index then picks the changes up via CDF. Make `03` idempotent so it's safe to re-run.
- Confirm `databricks-vectorsearch` SDK method/param names against current docs — fast-moving surface.

---

## Step 4 — AI agent

Agent with two tools: one read, one write. Notebook: `notebooks/04_agent.py`. Tools: `app/tools.py`.

### Execute
1. **Tools (plain Python, in `app/tools.py`):**
   - `search_recalls(query: str)` → Vector Search `similarity_search`, returns the display-column
     matches. *(read)*
   - `add_to_watchlist(user_email: str, term: str)` → `INSERT INTO watchlist (user_email, term)` via
     the `app/lakebase.py` connection. *(write)*
2. **Smoke-test them bare, before any agent:**
   ```python
   import sys; sys.path.append("../app")
   from tools import search_recalls, add_to_watchlist
   search_recalls("listeria")   # should return matches with display fields
   ```
   If this fails it's Step 3 or 2, not the LLM — don't debug it through an agent.
3. **Agent** — wrap the tools with the Mosaic AI Agent Framework (or LangGraph) on a Model Serving
   foundation-model endpoint. System prompt: *"Help users find product recalls and manage their
   watchlist. Use search_recalls to answer questions; use add_to_watchlist when asked to track
   something."*
4. **Register** with MLflow, passing the tool source so the logged model carries its deps —
   `code_paths=["../app/tools.py", "../app/lakebase.py"]` or the current equivalent. Optionally deploy
   to a Model Serving endpoint for the app to call.

### Verify (done-when)
- Bare `search_recalls("listeria")` returns matches *before* the agent exists.
- "any recent listeria recalls?" → agent calls `search_recalls` and answers.
- "watch romaine lettuce for me" → agent calls `add_to_watchlist`; a row appears in `watchlist`.

### Notes
- The interesting risk is the model triggering a **write**. "watch romaine for me" and "any romaine
  recalls?" look similar and mean opposite things — this is *why* an agent is warranted over a plain
  search box; worth stating in the write-up.
- Foundation-model endpoint names and the agent-authoring API move fast — confirm against docs,
  `code_paths` argument name especially.
- The agent's identity needs read on the Vector Search index and read/write on Lakebase.

---

## Step 5 — Databricks App

Minimal Flask + single-page frontend. The only user-facing piece; everything in Steps 1–4 is
infrastructure the user never sees. Databricks hosts the Flask process behind workspace SSO.

### Execute
1. **`app/app.py`** (Flask) — routes:
   - `GET /` → serve `static/index.html`
   - `POST /search` `{q}` → Vector Search `similarity_search` → JSON *(deterministic, no LLM)*
   - `POST /chat` `{message}` → invoke the agent → reply *(LLM decides: search vs. watchlist write)*
   - `GET /watchlist` → the pull panel (below)
   - user identity from the `X-Forwarded-Email` header → `user_email`
2. **`app/tools.py` + `app/lakebase.py`** — already written (Steps 2 & 4); `app.py` imports them
   directly (same folder, no `sys.path`).
3. **`app/app.yaml`** — run command e.g. `command: ["python", "app.py"]`; bind `0.0.0.0:$DATABRICKS_APP_PORT`.
4. **`app/requirements.txt`** — `flask`, `databricks-vectorsearch`, `psycopg2-binary`, `databricks-sdk` (+ `mlflow` if invoking the agent endpoint).
5. **`app/static/index.html`** — search box + results list, chat box, "My watchlist" panel.
6. **Deploy** — Databricks Apps → create app from `app/` (or `databricks apps deploy`).

### The watchlist pull panel
Closes the loop: without it, `add_to_watchlist` writes rows nothing reads back. `GET /watchlist`, for
the caller from `X-Forwarded-Email`:
1. `SELECT term FROM watchlist WHERE user_email = %s` — direct psycopg2, not via the agent.
2. For each term, `index.similarity_search(query_text=term, num_results=5)`.
3. Return `{term: [matches]}`, rendered per term.

**Pull, not push** — label it "matches for your watchlist," not "alerts." Optional if time:
`DELETE /watchlist/<term>` so the list can be pruned.

### Verify (done-when)
- App URL opens; `/search` returns results with full display fields.
- Chat: *"is romaine being recalled?"* → `search_recalls` answers.
- Chat: *"watch romaine for me"* → `add_to_watchlist`; row in Lakebase.
- Reload → "My watchlist" shows `romaine` with current matches. **Phase-1 checkpoint: openFDA data
  round-tripping through every layer, visible in a browser.**

### Notes
- The app's service principal needs **read on the Vector Search index AND read/write on Lakebase** —
  grant both. The email header says *who is asking*; the service principal is *what connects*. A
  missing grant is the usual first-deploy failure.
- `title` truncation (Step 1) will look scrappy as a headline — render `brand` + a trimmed
  `product_description` instead of raw `title`.
- `/search` and `/chat` as separate paths give a free bisect: `/search` works but `/chat` doesn't →
  the problem is Step 4, not Step 3 or app credentials.

### Future work (not in the MVP)
Push notification instead of pull, needing three things skipped here:
1. **Scheduled ingest** — `01_ingest.py` as a Databricks Job.
2. **Change detection** — switch `mode("overwrite")` to `MERGE` with a `first_seen` column so new
   recalls are identifiable.
3. **Delivery** — match new `recall_id`s against `watchlist` terms (**this is where Postgres
   `recalls` earns its place**), write hits to a `matches` table the app badges, or send email.