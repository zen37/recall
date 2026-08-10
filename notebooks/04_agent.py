# Databricks notebook source
# MAGIC %md
# MAGIC # Step 4 · AI agent (US food recalls MVP)
# MAGIC
# MAGIC Wrap two plain-Python tools (`app/tools.py`) behind a Databricks foundation-model endpoint with
# MAGIC function calling, as an MLflow `ChatAgent`. One **read** tool (`search_recalls`, Vector Search)
# MAGIC and one **write** tool (`add_to_watchlist`, Lakebase).
# MAGIC
# MAGIC **Order matters:** the tools are smoke-tested *bare* (no LLM) first. If that fails, it's Step 2
# MAGIC or 3 — not something to debug through a model.
# MAGIC
# MAGIC **Identity:** `add_to_watchlist` is *not* exposed to the model with a `user_email` argument — the
# MAGIC agent injects the caller identity from `custom_inputs`/context. The model shouldn't invent whose
# MAGIC watchlist to write. This mirrors the Step 5 app, where identity comes from `X-Forwarded-Email`.

# COMMAND ----------
# MAGIC %md ## 0 · Install deps
# MAGIC `databricks-vectorsearch` (read tool), `psycopg2-binary` (write tool via `app/lakebase.py`),
# MAGIC `openai` (the tool-calling client), `mlflow` (ChatAgent + logging). Then restart Python.

# COMMAND ----------
# MAGIC %pip install -U databricks-vectorsearch psycopg2-binary openai mlflow databricks-sdk
# MAGIC dbutils.library.restartPython()

# COMMAND ----------
# MAGIC %md ## Parameters
# MAGIC The foundation-model endpoint is a widget and is *verified against the workspace serving list*
# MAGIC (like Step 3's embedding check) — it must exist and support tool calling. No hardcoded guess.

# COMMAND ----------
dbutils.widgets.text("catalog", "adw")
dbutils.widgets.text("schema", "recalls")
dbutils.widgets.text("llm_endpoint", "databricks-meta-llama-3-3-70b-instruct")
dbutils.widgets.text("embedding_endpoint", "databricks-gte-large-en")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
llm_endpoint = dbutils.widgets.get("llm_endpoint")
embedding_endpoint = dbutils.widgets.get("embedding_endpoint")

uc_model_name = f"{catalog}.{schema}.recalls_agent"
print("llm endpoint :", llm_endpoint)
print("uc model     :", uc_model_name)

# COMMAND ----------
# MAGIC %md ## 1 · Smoke-test the tools BARE (no agent)
# MAGIC Done-when #1: `search_recalls("listeria")` returns matches with display fields.

# COMMAND ----------
import sys

sys.path.append("../app")  # Git-folder layout: notebooks/ and app/ are siblings
from tools import search_recalls, add_to_watchlist  # noqa: E402

hits = search_recalls("listeria")
print("search_recalls('listeria') ->", len(hits), "matches")
for h in hits[:3]:
    print(f"  - {h.get('title')}  [{h.get('brand')}]  {h.get('recall_date')}  {h.get('classification')}")
assert hits and all("recall_id" in h and "title" in h for h in hits), "read tool returned no/incomplete rows"
print("keys returned:", sorted(hits[0].keys()))

# COMMAND ----------
# MAGIC %md ### Write tool bare, then confirm + clean up the test row
# MAGIC Insert a throwaway row, read it back to prove it landed, then delete it (keep `watchlist` clean).

# COMMAND ----------
from lakebase import connect  # noqa: E402

print(add_to_watchlist("test@example.com", "romaine"))

conn = connect()
try:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT user_email, term, created_at FROM watchlist "
            "WHERE user_email = %s AND term = %s",
            ("test@example.com", "romaine"),
        )
        print("row landed:", cur.fetchall())
        cur.execute(
            "DELETE FROM watchlist WHERE user_email = %s AND term = %s",
            ("test@example.com", "romaine"),
        )
    conn.commit()
    print("test row cleaned up")
finally:
    conn.close()

# COMMAND ----------
# MAGIC %md ## 2 · Verify the LLM endpoint is served (don't guess)

# COMMAND ----------
from databricks.sdk import WorkspaceClient  # noqa: E402

w = WorkspaceClient()
served = [e.name for e in w.serving_endpoints.list()]
chat_like = sorted(n for n in served if any(k in n.lower() for k in ("llama", "claude", "gpt", "qwen", "mixtral", "dbrx", "gemma")))
print("likely chat endpoints:", chat_like)
assert llm_endpoint in served, (
    f"LLM endpoint '{llm_endpoint}' is not served here. Pick a tool-calling chat endpoint from "
    f"the list above and set the 'llm_endpoint' widget."
)
print(f"OK: '{llm_endpoint}' is available")

# COMMAND ----------
# MAGIC %md ## 3 · The agent lives in `app/agent.py`
# MAGIC A committed, first-class file — not generated at runtime. It's the MLflow models-from-code
# MAGIC entrypoint (logged in cell 5), it's imported here for the bare done-when calls, and Step 5's
# MAGIC Databricks App invokes the same file. Single source of truth. It imports the tools from
# MAGIC `app/tools.py` (via `../app` in the notebook; via `code_paths` at serving time).

# COMMAND ----------
# MAGIC %md ## 4 · Run the agent (done-when #2 and #3)
# MAGIC Bare calls straight to the agent — no deployment needed to verify tool use.

# COMMAND ----------
import os  # noqa: E402

os.environ["LLM_ENDPOINT_NAME"] = llm_endpoint  # keep agent.py's logged default in sync with the widget
# ../app is already on sys.path (cell 1) — so `import agent` and its `from tools import ...` resolve.
import agent as agent_mod  # noqa: E402
from mlflow.types.agent import ChatAgentMessage  # noqa: E402

bot = agent_mod.RecallsAgent(llm_endpoint=llm_endpoint)


# ChatAgentMessage.tool_calls come back as ToolCall pydantic objects (attribute
# access), not dicts — handle both so the display/asserts are version-proof.
def _tc_fn(tc):
    fn = tc["function"] if isinstance(tc, dict) else tc.function
    name = fn["name"] if isinstance(fn, dict) else fn.name
    args = fn["arguments"] if isinstance(fn, dict) else fn.arguments
    return name, args


def _tools_called(resp):
    return [
        _tc_fn(tc)[0]
        for m in resp.messages
        if getattr(m, "tool_calls", None)
        for tc in m.tool_calls
    ]


def show(resp):
    for m in resp.messages:
        if getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                name, args = _tc_fn(tc)
                print(f"  [tool call] {name}({args})")
        elif m.role == "tool":
            print(f"  [tool result] {m.content[:200]}")
        else:
            print(f"  [{m.role}] {m.content}")


# COMMAND ----------
# MAGIC %md ### Done-when #2 — read path: "any recent listeria recalls?"

# COMMAND ----------
resp = bot.predict(messages=[ChatAgentMessage(role="user", content="any recent listeria recalls?")])
show(resp)
called = _tools_called(resp)
assert "search_recalls" in called, f"agent did not call search_recalls (called: {called})"
print("\nOK: agent invoked search_recalls and answered")

# COMMAND ----------
# MAGIC %md ### Done-when #3 — write path: "watch romaine lettuce for me"
# MAGIC Identity is injected via `custom_inputs` (stands in for `X-Forwarded-Email` in the app).

# COMMAND ----------
resp = bot.predict(
    messages=[ChatAgentMessage(role="user", content="watch romaine lettuce for me")],
    custom_inputs={"user_email": "demo@example.com"},
)
show(resp)
called = _tools_called(resp)
assert "add_to_watchlist" in called, f"agent did not call add_to_watchlist (called: {called})"

# Confirm the row via the exact done-when query.
conn = connect()
try:
    with conn.cursor() as cur:
        cur.execute("SELECT user_email, term, created_at FROM watchlist WHERE term = %s", ("romaine lettuce",))
        rows = cur.fetchall()
finally:
    conn.close()
print("\nSELECT * FROM watchlist WHERE term = 'romaine lettuce':")
for r in rows:
    print("  ", r)
assert rows, "agent claimed to add, but no watchlist row found"
print("\nOK: agent invoked add_to_watchlist and the row landed")

# COMMAND ----------
# MAGIC %md ## 5 · Log + register the agent with MLflow
# MAGIC Models-from-code (`python_model="../app/agent.py"`), tool source shipped via `code_paths`, and the
# MAGIC Databricks resources the agent needs declared for auth passthrough. Deploying to a serving
# MAGIC endpoint is out of scope for Step 4.

# COMMAND ----------
import mlflow  # noqa: E402
from mlflow.models.resources import (  # noqa: E402
    DatabricksServingEndpoint,
    DatabricksVectorSearchIndex,
)

resources = [
    DatabricksServingEndpoint(endpoint_name=llm_endpoint),
    DatabricksServingEndpoint(endpoint_name=embedding_endpoint),  # index's managed-embedding model
    DatabricksVectorSearchIndex(index_name="adw.recalls.unified_search_index"),
]

input_example = {"messages": [{"role": "user", "content": "any recent listeria recalls?"}]}

with mlflow.start_run():
    logged = mlflow.pyfunc.log_model(
        artifact_path="agent",
        python_model="../app/agent.py",
        code_paths=["../app/tools.py", "../app/lakebase.py"],
        resources=resources,
        input_example=input_example,
        pip_requirements=[
            "mlflow",
            "databricks-vectorsearch",
            "psycopg2-binary",
            "openai",
            "databricks-sdk",
        ],
    )
print("logged model uri:", logged.model_uri)

# NOTE: a deployed serving endpoint also needs read on secret scope
# `lakebase-recalls` for the write tool — that's a Step 5 (deploy) concern.

# COMMAND ----------
mlflow.set_registry_uri("databricks-uc")
registered = mlflow.register_model(model_uri=logged.model_uri, name=uc_model_name)
print(f"registered {uc_model_name} version {registered.version}")

# COMMAND ----------
# MAGIC %md ## Verify (done-when) summary
# MAGIC All three checks above assert + print their evidence:
# MAGIC 1. bare `search_recalls("listeria")` returned matches with display fields;
# MAGIC 2. "any recent listeria recalls?" → agent called `search_recalls` and answered;
# MAGIC 3. "watch romaine lettuce for me" → agent called `add_to_watchlist`; the row is in `watchlist`.

# COMMAND ----------
print("Step 4 checks PASSED — tools verified bare; agent drove both read and write tools")
