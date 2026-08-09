# US Recalls — Capstone Architecture

A Databricks app that ingests US product recalls, makes them semantically searchable, and exposes an AI agent that can search recalls and take actions (watchlists, alerts) on the user's behalf.

## Flow

```
             +---------------+
             |  Recall APIs  |
             +-------+-------+
                     |
                     v
             +---------------+
             | Spark pipeline|
             +-------+-------+
                     |
          +----------+----------+
          |                     |
          v                     v
    +------------+      +----------------+
    |  Lakebase  |      |  Vector Search |
    +-----+------+      +--------+-------+
          |                      |
          +----------+-----------+
                     |
                     v
             +---------------+
             |   AI agent    |
             +-------+-------+
                     |
                     v
             +---------------+
             |  App frontend |
             +---------------+
```

Detail for each node:
- **Recall APIs** — openFDA, CPSC, NHTSA, FSIS (third-party APIs)
- **Spark pipeline** — Lakeflow / Auto Loader; landing → bronze → silver → gold
- **Lakebase** — Postgres; relational recall + watchlist tables
- **Vector Search** — embeddings over recall narrative text
- **AI agent** — Mosaic AI Agent Framework; read + write tools
- **App frontend** — Databricks App; chat + search UI

## Requirement coverage

| Capstone requirement | How it is met |
|---|---|
| Data pipeline in Spark | Medallion pipeline (landing → bronze → silver → gold) built with Lakeflow Declarative Pipelines + Auto Loader, running on Spark. |
| Integration with ≥1 third-party API | Ingests from openFDA, CPSC, NHTSA, and USDA-FSIS recall APIs (all public, no auth). |
| Processing of unstructured data | Recall narrative text (reason/hazard descriptions, notice text) is embedded and indexed in Databricks Vector Search for semantic retrieval. |
| Databricks App with a frontend | Databricks App with a Flask backend and a simple SPA frontend (search + chat panel). |
| AI agent with read **and** write tools | Agent (Mosaic AI Agent Framework) with retrieval tools (keyword + semantic search) **and** write tools that act on the database (add to watchlist, create alert, annotate recall). |

Shared skeleton: relational tables in Lakebase ✅ · embeddings over unstructured text ✅ · agent with read + write DB tools ✅.

## Components

### 1. Sources (third-party APIs)
- openFDA: `api.fda.gov/food/enforcement.json`, `/drug/enforcement.json`, `/device/enforcement.json`
- CPSC: `saferproducts.gov/RestWebServices/Recall?format=json`
- NHTSA: bulk flat file at `static.nhtsa.gov/odi/ffdd/rcl/`
- FSIS: `fsis.usda.gov/fsis/api/recall/v/1`

### 2. Spark pipeline (medallion)
- Landing: raw API responses written to a Unity Catalog Volume.
- Bronze: Auto Loader → Delta; schema + type validation, dedup.
- Silver: normalize all sources into one `recalls` table.
- Gold: search-ready `recalls` table.
- Scheduled daily with a Lakeflow Job.

Unified `recalls` fields: `recall_id`, `source`, `agency`, `category`, `title`, `product_description`, `brand`, `recall_date`, `status`, `classification`, `reason_hazard`, `source_url`.

### 3. Lakebase (relational store)
- `recalls` table synced from gold.
- `watchlist` table (user → brand/category) written by the agent.

### 4. Embeddings + Vector Search (unstructured retrieval)
- Embed `product_description + reason_hazard` (recall narrative text).
- Databricks Vector Search index (Delta-sync from gold) for semantic queries.

### 5. AI agent (read + write tools)
Read tools:
- `keyword_search(query, filters)` → Lakebase full-text search
- `semantic_search(query)` → Vector Search
- `get_recall(recall_id)` → Lakebase

Write tools:
- `add_to_watchlist(user, brand|category)` → Lakebase
- `create_alert(user, criteria)` → Lakebase
- `annotate_recall(recall_id, note)` → Lakebase

### 6. Databricks App (frontend)
- Flask backend + SPA, bound to `0.0.0.0:$DATABRICKS_APP_PORT`, identity via `X-Forwarded-Email`.
- Chat panel calls the agent; search view queries Lakebase directly.

## Example end-to-end flow
User asks "any recent infant-formula recalls I should worry about?" → agent calls `semantic_search` → summarizes matches → user says "watch that brand and alert me" → agent calls `add_to_watchlist` + `create_alert` (writes to Lakebase). This single interaction exercises the Spark data, the embeddings, the frontend, and the read + write agent.
