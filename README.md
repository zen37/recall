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

# Screenshots

https://recalls-7405613269176411.11.azure.databricksapps.com

<img width="1161" height="898" alt="image" src="https://github.com/user-attachments/assets/6d0c86bb-08d2-4d12-bad4-f474058c7027" />


# To Do

## Mobile App

A consumer front-end that turns the watchlist into a zero-effort habit — instead of typing terms,
you scan what you actually bought:

1. **Snap your receipt** — photograph a grocery receipt; OCR extracts the line items.
2. **Items are saved** — the extracted product/brand names become watchlist terms in Lakebase, tied
   to your account (reusing the agent's `add_to_watchlist` write).
3. **Monitoring runs automatically** — new recalls are matched against your saved items and pushed to
   you when one hits.

![Mobile app concept](docs/mobile-app-mockup.svg)

This is the **push** half the current MVP deliberately skips (see *Future work* in
[`implementation.md`](implementation.md)): scheduled ingest + change detection (a `first_seen`
column) + delivery. No new backend architecture — the web app already proves the round-trip; the
mobile app just adds an OCR on-ramp and flips **pull → push**.

## Lakebase Table 'Recalls'

The Lakebase `recalls` table (loaded in Step 2) is **unused by the app** — every recall shown in the
UI comes back through the Vector Search index, never from Postgres. Once the index was built to
return the full display columns (Step 3), the app renders results in one call with no join back to
Lakebase, which removed the reason for this table. It's also **stale**: Step 2 is a one-time
`psycopg2` load with no auto-sync, so it drifts from `adw.recalls.unified` on any re-ingest.

Two options:

- **Drop it (cleanest).** Removes a frozen, unread copy of ~1,000 rows and the drift it invites.
  `DROP TABLE recalls;` in Lakebase. The app is unaffected — it doesn't reference this table.
  Recommended unless the option below is on the roadmap. Note: keep the `watchlist` table; that one
  *is* load-bearing (the app reads and writes it live).

  *Why it was there in the first place:* the original plan built the search index as **IDs-only** —
  the app would query Vector Search for matching `recall_id`s, then **join back to the Lakebase
  `recalls` table** to fetch the display fields (title, brand, date, hazard…). Under that design the
  Postgres copy was essential, which is why Step 2 loaded it. Step 3 then chose the opposite: make
  the index **return the full display set** so results render in one call with no join. That decision
  made the round-trip to Postgres unnecessary and orphaned the table. It's a fossil of the earlier
  "IDs-only index + Postgres join" approach that was superseded.

- **Keep it only for faceted / filtered querying.** Semantic (vector) search is weak at exact
  filters and ordering — "show all **Class I** recalls," "filter by **brand**," "sort by **date**."
  A relational `recalls` table in Postgres answers those with plain indexed `WHERE` / `ORDER BY`,
  complementing the semantic `/search`. If structured browsing like that is planned, keep the table
  (and switch its load to a keep-in-sync path — a UC→Lakebase synced table, or re-run the load on
  each ingest — so it stops going stale).

## Lakebase Change Data Feed (CDF)
Persist Lakebase tables' Change Data Feed to Unity Catalog.

If you have a **watchlist of products** stored in Lakebase and you enable CDF, here's how it could help your recalls search app:

### What CDF gives you for a watchlist

Every time a product is **added to, removed from, or updated on** the watchlist in Lakebase, that change streams into a `lb_<watchlist_table>_history` Delta table in Unity Catalog within ~15 seconds.

### Practical use cases for your scenario

| Use case                    | How it helps                                                                                                                                                                                                                             |
| --------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Proactive recall alerts** | A downstream pipeline watches the CDF feed. When a new product is added to the watchlist, it automatically cross-references against your `unified_search_index` recalls data — if there's a matching recall, it triggers a notification. |
| **Audit trail**             | You get a full history of what was on the watchlist and when — useful for compliance ("was this product being monitored when the recall was issued?").                                                                                   |
| **Keep analytics in sync**  | Build a silver/gold table in Unity Catalog that always reflects the current watchlist state. Dashboards and reports stay up to date without manual ETL.                                                                                  |
| **Trigger re-indexing**     | When a product is added/removed from the watchlist, a pipeline could update your vector search index or adjust search relevance/filtering.                                                                                               |
### Example flow

```
User adds "Brand X Baby Monitor" to watchlist in Lakebase
        ↓ (~15 sec)
CDF writes INSERT row to lb_watchlist_history in Unity Catalog
        ↓
Streaming job picks up the change
        ↓
Queries unified_search_index for matching recalls
        ↓
Finds active recall → sends alert to user
```

### Without CDF

You'd need to either:
- Poll the Lakebase table on a schedule (latency, wasted compute)
- Build your own CDC pipeline with external tools (complexity)
- Handle everything in the app layer (no audit trail, no lakehouse integration)

### Bottom line

CDF turns your watchlist from a static app-only table into a **reactive event source** that the rest of your Databricks lakehouse can act on — alerts, analytics, and audit all come for free.