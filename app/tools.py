"""Agent tools — plain Python, no agent framework.

Two functions the Step 4 agent (and the Step 5 app) wrap:

- search_recalls(query)            -> read: semantic search over the Vector
                                      Search index, returns display-column dicts.
- add_to_watchlist(user_email,term)-> write: INSERT into Lakebase `watchlist`.

Kept framework-free on purpose: you can call these in a notebook and confirm
they work *before* an LLM is involved (see notebooks/04_agent.py). Lives in
`app/` because Databricks Apps deploys only that folder, and both consumers
(the app and the agent) import from here.
"""

import os

from lakebase import connect  # same folder (app/); notebook adds ../app to sys.path

# Step 3 handles — the ONLINE delta-sync index and its endpoint.
VS_ENDPOINT = "recalls_vs"
VS_INDEX = "adw.recalls.unified_search_index"

# The display set the index syncs — returned so callers render results with no
# join back to Postgres. recall_id is the primary key.
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

_vsc = None


def _index():
    """Lazily build one VectorSearchClient and return the index handle. Lazy so
    importing this module never requires Vector Search creds (e.g. the write
    tool works without them)."""
    global _vsc
    if _vsc is None:
        from databricks.vector_search.client import VectorSearchClient

        kwargs = {"disable_notice": True}
        # In a Databricks App the client won't auto-detect creds — pass the
        # app service principal's injected OAuth creds explicitly. In a notebook
        # these env vars are absent and the client uses ambient auth.
        cid = os.environ.get("DATABRICKS_CLIENT_ID")
        secret = os.environ.get("DATABRICKS_CLIENT_SECRET")
        host = os.environ.get("DATABRICKS_HOST")
        if host and not host.startswith("http"):
            # Apps inject DATABRICKS_HOST as a bare hostname; the client builds
            # the OAuth token URL by concatenation and needs the scheme.
            host = "https://" + host
        if cid and secret and host:
            kwargs.update(
                workspace_url=host,
                service_principal_client_id=cid,
                service_principal_client_secret=secret,
            )
        _vsc = VectorSearchClient(**kwargs)
    return _vsc.get_index(endpoint_name=VS_ENDPOINT, index_name=VS_INDEX)


def search_recalls(query: str, num_results: int = 5) -> list[dict]:
    """Semantic search over recalls. Returns a clean list of dicts with the
    display columns (raw similarity_search envelope dropped)."""
    res = _index().similarity_search(
        query_text=query,
        columns=DISPLAY_COLS,
        num_results=num_results,
    )
    cols = [c["name"] for c in res["manifest"]["columns"]]
    rows = res["result"]["data_array"] or []
    return [dict(zip(cols, row)) for row in rows]


def add_to_watchlist(user_email: str, term: str) -> dict:
    """Record that `user_email` wants to track `term`. One INSERT into Lakebase
    `watchlist`. Column is user_email (never the reserved word `user`)."""
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO watchlist (user_email, term) VALUES (%s, %s)",
                (user_email, term),
            )
        conn.commit()
    finally:
        conn.close()
    return {"status": "added", "user_email": user_email, "term": term}
