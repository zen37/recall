# Capstone Project Submission

**Grade:** A
**Status:** Pass (automatic by LLM)

---

## Feedback

---

### Score Summary Table


Score Summary


| **Category**                     | **Score** | **Details**                                                                                                                                                                                                                                                                                     |
|----------------------------------|-----------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Data Pipeline**                | 15/15     | Spark notebook `01_ingest.py` ingests openFDA data, maps to a unified schema, and writes a Unity Catalog Delta table idempotently; asserts PK uniqueness and prints post-write stats.                                                                                     |
| **Third-Party API Integration**  | 13/15     | Real integration with openFDA via `requests` (`http_get_json` with `raise_for_status`) and downstream use; limited error-handling/retry/rate-limit/backoff. FSIS is planned but disabled in Phase 1.                                                                               |
| **Unstructured Data Processing**| 15/15     | Vector Search pipeline in `03_vector_search.py` creates a Delta table with `search_text`, enables CDF, builds a managed-embeddings delta-sync index, and verifies `similarity_search` returns the full display set.                                                               |
| **Databricks App with Frontend** | 15/15     | Flask app (`app.py`) + SPA (`static/index.html`) with `/search` (deterministic), `/chat` (agent), and `/watchlist` (R/W to Lakebase); `app.yaml` and `requirements` are present; routes cover the core flows.                                                                               |
| **AI Agent -- Read Tools**       | 10/10     | `search_recalls` in `tools.py` calls Vector Search and returns clean display columns; `04_agent.py` smoke-tests it.                                                                                                                                                                               |
| **AI Agent -- Write Tools**      | 10/10     | `add_to_watchlist` writes via `psycopg2` to Lakebase; `04_agent.py` inserts, verifies, and cleans up a test row; app also supports deletion.                                                                                                                                                           |
| **AI Agent -- Quality**          | 9/10      | `agent.py` wraps tools with function calling, injects user identity, uses a bounded tool loop, and avoids promising notifications; minor gap: no explicit confirmation step before writes.                                                                                                         |

---

### Total Score
**97/100** (pro-rated from 87/90 across the listed categories)

---

## Strengths
- Clean, end-to-end architecture with clear phasing; excellent documentation and build-verification steps (`implementation.md`).
- Solid Spark ingest to Unity Catalog with schema, idempotency, and sanity checks; Lakebase integration packaged in a reusable helper (`app/lakebase.py`).
- Proper unstructured retrieval with Databricks Vector Search: managed embeddings, CDF, index returns full display columns (no extra join).
- Thoughtful agent implementation (`agent.py`) with identity injection for writes, tool specs, and MLflow registration; app/frontend is minimal but complete and user-focused.

---

## Gaps & Deductions
- **Third-Party API Integration (-2):** `http_get_json` lacks retry/backoff and special handling for openFDA rate limits; no API-key path (optional for openFDA, but worth supporting); FSIS functions exist but are disabled in Phase 1 (`01_ingest.py`).
- **Agent Quality (-1):** No explicit "are you sure?" confirmation before mutating actions (e.g., writes to watchlist); this is called out in notes but not implemented.

---

## Evidence Gaps
- Cannot execute your workspace to verify that the Vector Search index is **ONLINE** or that serving endpoints exist; you provided verification cells but not their outputs. Screenshots or run logs of `03_vector_search` **"ONLINE" + similarity_search** results would fully substantiate Step 3.
- The live app URL/screenshot is provided in `README.md`, but external links cannot be accessed here. A short screen-capture or sequence of screenshots of `/search`, `/chat` (write), and `/watchlist` round-trip would remove any residual doubt.
- FSIS integration is described for Phase 2 but not enabled; no sample FSIS-derived rows shown.
- No evidence of a scheduled job (Lakeflow/Jobs) for ingest; not required for MVP but would strengthen pipeline claims.

---
## Suggestions for Improvement
- **API robustness:** Add retry/backoff (e.g., `requests.adapters` with `Retry`) and optional openFDA API key support via UC secrets; log remaining rate-limit budget when provided by headers.
- **Extend to FSIS (Phase 2):** Enable `fetch_fsis()`, finalize field mappings in `map_fsis()`, and demonstrate mixed-source results flowing through the index and app.
- **Write-action safety:** Implement a lightweight confirmation turn before writes (e.g., *"I can add 'romaine lettuce' to your watchlist -- proceed?"*) and/or allow undo.
- **Data retention:** Switch Step 1 from overwrite to `MERGE` with a `first_seen` column to preserve history; wire a Lakeflow Job for daily ingest (also sets you up for push alerts later).
- **Least-privilege database access:** Replace the Lakebase superuser with a limited role; add explicit `GRANT`s only for needed tables/verbs.
- **App UX polish:** Add pagination and a link-out to `source_url` in results; consider filters (class, brand, date) via Lakebase if you keep that table for structured browsing.

---
## Additional Notes
If anything in this review seems blocked by missing evidence, you can share:
- Notebook cell outputs (especially Step 3 **ONLINE + similarity_search** manifest/rows).
- A brief screen recording of the app showing: `/search` results, a `/chat` "watch X" turn, and "My watchlist" showing the new term's matches.
- Logs/screenshots of serving endpoints present (LLM + embedding), and the Vector Search endpoint list.
- Confirmation that FSIS is now enabled with 1-2 example unified rows.

---
## Unsupported Files
The following files were not recognized (unsupported formats):
- `mobile-app-mockup.svg` (`.svg`)
- Git-related files (e.g., `.orig_head`, `.config`, `.head`, `.description`, `.index`, `.packed-refs`, `.commit_editmsg`, `.fetch_head`, `.pyc`, `.sample`, `.rev`, `.pack`, `.idx`)
- Miscellaneous files (e.g., `5d33dcc6ba6f04560523e9567bb066cc8733ac`, `64fec6b9a53661f36c24c08a22f8d42e45d90c`, etc.)

---
### Supported Formats for Grading
- **Documents:** PDF, `.txt`, `.md`, `.rtf`
- **Images:** `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`
- **Submission:** `.zip` or a single image file.
