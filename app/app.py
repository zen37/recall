"""Databricks App — US food recalls MVP (Flask).

The only user-facing piece. Everything in Steps 1-4 is infrastructure; this
serves a single page and four endpoints behind Databricks workspace SSO.

Routes:
  GET  /              -> static/index.html
  POST /search  {q}   -> Vector Search similarity_search (deterministic, no LLM)
  POST /chat    {message} -> in-process agent (LLM decides: search vs. watchlist)
  GET  /watchlist     -> pull panel: the caller's terms + current matches per term
  DELETE /watchlist/<term> -> remove a term (keeps the demo tidy)

Identity: the signed-in user's email comes from the X-Forwarded-Email header
that Databricks Apps injects. The app's *service principal* is what actually
connects to Vector Search and Lakebase — the header says who is asking, the SP
says what connects. Both need access (a common first-deploy gotcha).

Same-folder imports (Apps deploys only this folder): tools, lakebase, agent.
"""

import os

from flask import Flask, jsonify, request, send_from_directory

from tools import search_recalls, add_to_watchlist  # noqa: F401  (add_to_watchlist used via agent)
from lakebase import connect

app = Flask(__name__, static_folder="static", static_url_path="/static")

_agent = None


def get_agent():
    """Build the in-process agent lazily (first /chat only) so /search and
    /watchlist don't pay for it."""
    global _agent
    if _agent is None:
        from agent import RecallsAgent

        _agent = RecallsAgent()  # LLM endpoint from LLM_ENDPOINT_NAME env
    return _agent


def current_user():
    """The authenticated user's email, injected by Databricks Apps."""
    return request.headers.get("X-Forwarded-Email") or "anonymous@unknown"


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.post("/search")
def search():
    """Deterministic path — no LLM. Straight Vector Search."""
    q = (request.get_json(silent=True) or {}).get("q", "").strip()
    if not q:
        return jsonify({"results": []})
    return jsonify({"results": search_recalls(q)})


@app.post("/chat")
def chat():
    """LLM path — the agent decides whether to search or write to the watchlist.
    Identity is injected from the header, never supplied by the model."""
    from mlflow.types.agent import ChatAgentMessage

    message = (request.get_json(silent=True) or {}).get("message", "").strip()
    if not message:
        return jsonify({"reply": ""})

    resp = get_agent().predict(
        messages=[ChatAgentMessage(role="user", content=message)],
        custom_inputs={"user_email": current_user()},
    )
    # The final natural-language answer is the last assistant message with text
    # (earlier assistant messages may be tool-call-only, with empty content).
    reply = ""
    for m in resp.messages:
        if m.role == "assistant" and m.content:
            reply = m.content
    return jsonify({"reply": reply})


@app.get("/watchlist")
def watchlist():
    """Pull panel: the caller's watched terms, each with its current matches.
    This is pull, not push — matches show when the page opens; nothing notifies.
    Direct psycopg2 read (no LLM needed to list rows)."""
    user = current_user()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT term FROM watchlist WHERE user_email = %s ORDER BY created_at DESC",
                (user,),
            )
            terms = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()

    # Dedupe (append-only table can hold repeats) while preserving recency order.
    seen, unique_terms = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            unique_terms.append(t)

    panel = {t: search_recalls(t) for t in unique_terms}
    return jsonify({"user": user, "watchlist": panel})


@app.delete("/watchlist/<path:term>")
def watchlist_delete(term):
    user = current_user()
    conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM watchlist WHERE user_email = %s AND term = %s",
                (user, term),
            )
        conn.commit()
    finally:
        conn.close()
    return jsonify({"deleted": term})


if __name__ == "__main__":
    port = int(os.environ.get("DATABRICKS_APP_PORT", "8000"))
    app.run(host="0.0.0.0", port=port)
