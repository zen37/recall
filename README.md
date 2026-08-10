# Product Recalls

An end-to-end **US food-recalls** app built on Databricks: semantic search, an AI assistant, and a
personal watchlist over openFDA food-recall data.

## What it does

- **Search** — semantic (vector) search over ~1,000 recent openFDA food recalls; ranked matches with
  brand, date, classification, and hazard. Deterministic, no LLM.
- **Assistant** — a Mosaic AI agent that reads your request and decides whether to *answer* (search)
  or *act* (add a term to your watchlist). "any listeria recalls?" and "watch romaine for me" look
  alike but mean opposite things — that read-vs-write intent split is why it uses a model.
- **Watchlist** — save terms and see current matches for each when you open the app. **Pull, not
  push**: nothing runs in the background and nothing notifies you.

## Architecture

One unified table feeds every downstream layer:

```
openFDA API  →  adw.recalls.unified (Unity Catalog)  →  Lakebase (Postgres) + Vector Search index
             →  agent (2 tools)  →  Databricks App
```

| Step | What | Tech |
|---|---|---|
| 1 · Ingest | openFDA food enforcement → one table `adw.recalls.unified` (1000 rows, `recall_id` PK) | Unity Catalog, PySpark |
| 2 · Lakebase | load `recalls` + create `watchlist` | Lakebase (managed Postgres), psycopg2 |
| 3 · Vector Search | `unified_search` (search_text + CDF) → delta-sync index with managed embeddings | Vector Search, `databricks-gte-large-en` |
| 4 · Agent | 2 tools — `search_recalls` (read) + `add_to_watchlist` (write) — as an MLflow `ChatAgent`, registered in Unity Catalog | Mosaic AI Agent Framework, MLflow |
| 5 · App | Flask + single-page UI: `/search`, `/chat` (in-process agent), `/watchlist` | Databricks Apps, Flask |

The shared code (`agent.py`, `tools.py`, `lakebase.py`) lives in `app/` because Databricks Apps
deploys only that folder — the notebooks and the app import the same modules.

> Scope: openFDA food recalls only (phase 1). USDA FSIS is a planned phase 2 — the schema and
> pipeline already accommodate it. See [`implementation.md`](implementation.md) for the step-by-step
> build log and [`docs/`](docs) for architecture notes.

## Repo layout

```
notebooks/   01_ingest · 02_lakebase · 03_vector_search · 04_agent
app/         app.py, agent.py, tools.py, lakebase.py, app.yaml, requirements.txt, static/index.html
implementation.md   step-by-step build log
docs/        architecture notes
```

# Screenshots

<img width="1161" height="898" alt="image" src="https://github.com/user-attachments/assets/6d0c86bb-08d2-4d12-bad4-f474058c7027" />


# Mobile App - To Do
