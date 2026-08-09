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
# MAGIC %md ## 3 · Author the agent (MLflow models-from-code)
# MAGIC Written to `agent.py` at runtime so MLflow can log it as a code artifact. The same file is
# MAGIC imported below for the bare done-when calls, so there's a single source of truth. It imports
# MAGIC the tools from `app/tools.py` (via `code_paths` at serving time; via `../app` in the notebook).

# COMMAND ----------
agent_src = r'''
"""Recalls tool-calling agent — MLflow ChatAgent, models-from-code entrypoint."""
import json
import os
import uuid
from typing import Any, Optional

import mlflow
from databricks.sdk import WorkspaceClient
from mlflow.pyfunc import ChatAgent
from mlflow.types.agent import ChatAgentMessage, ChatAgentResponse, ChatContext

try:
    from tools import search_recalls, add_to_watchlist
except ModuleNotFoundError:  # notebook dev: tools live in ../app
    import sys
    sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app"))
    from tools import search_recalls, add_to_watchlist

LLM_ENDPOINT_NAME = os.environ.get("LLM_ENDPOINT_NAME", "databricks-meta-llama-3-3-70b-instruct")

SYSTEM_PROMPT = (
    "Help users find product recalls and manage their watchlist. "
    "Use search_recalls to answer questions; use add_to_watchlist when asked to track something."
)

# Tools exposed to the model. add_to_watchlist does NOT take user_email — the agent injects the
# caller identity so the model can't invent whose watchlist to write.
TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "search_recalls",
            "description": "Search US food product recalls by semantic similarity. Use for any "
                           "question about recalls, hazards, brands, or contaminated products.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "what to look for, in natural language"}
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_to_watchlist",
            "description": "Add a term to the current user's recall watchlist. Use when the user asks "
                           "to watch, track, or monitor a product or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "term": {"type": "string", "description": "product or keyword to watch, e.g. romaine lettuce"}
                },
                "required": ["term"],
            },
        },
    },
]


def _run_tool(name: str, args: dict, user_email: Optional[str]) -> Any:
    if name == "search_recalls":
        return search_recalls(args["query"])
    if name == "add_to_watchlist":
        if not user_email:
            return {"error": "no user_email in context; cannot add to watchlist"}
        return add_to_watchlist(user_email, args["term"])
    return {"error": "unknown tool " + name}


def _msg(role, content, **kw):
    return ChatAgentMessage(id=str(uuid.uuid4()), role=role, content=content or "", **kw)


class RecallsAgent(ChatAgent):
    def __init__(self, llm_endpoint: str = LLM_ENDPOINT_NAME):
        self.llm_endpoint = llm_endpoint
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = WorkspaceClient().serving_endpoints.get_open_ai_client()
        return self._client

    def _to_openai(self, messages: list) -> list:
        out = []
        for m in messages:
            d = {"role": m.role, "content": m.content or ""}
            if getattr(m, "tool_calls", None):
                d["tool_calls"] = m.tool_calls
            if getattr(m, "tool_call_id", None):
                d["tool_call_id"] = m.tool_call_id
            if getattr(m, "name", None):
                d["name"] = m.name
            out.append(d)
        return out

    @mlflow.trace(span_type="AGENT")
    def predict(self, messages, context: Optional[ChatContext] = None,
                custom_inputs: Optional[dict] = None) -> ChatAgentResponse:
        user_email = (custom_inputs or {}).get("user_email")
        if not user_email and context is not None:
            user_email = getattr(context, "user_id", None)

        convo = [{"role": "system", "content": SYSTEM_PROMPT}] + self._to_openai(messages)
        produced = []

        for _ in range(6):  # bounded tool-calling loop
            resp = self.client.chat.completions.create(
                model=self.llm_endpoint, messages=convo, tools=TOOL_SPECS
            )
            choice = resp.choices[0].message
            if choice.tool_calls:
                tool_calls = [tc.model_dump() for tc in choice.tool_calls]
                convo.append({"role": "assistant", "content": choice.content or "", "tool_calls": tool_calls})
                produced.append(_msg("assistant", choice.content, tool_calls=tool_calls))
                for tc in choice.tool_calls:
                    args = json.loads(tc.function.arguments or "{}")
                    result = _run_tool(tc.function.name, args, user_email)
                    payload = json.dumps(result, default=str)
                    convo.append({"role": "tool", "tool_call_id": tc.id, "content": payload})
                    produced.append(_msg("tool", payload, tool_call_id=tc.id, name=tc.function.name))
                continue
            produced.append(_msg("assistant", choice.content))
            break

        return ChatAgentResponse(messages=produced)


AGENT = RecallsAgent()
mlflow.models.set_model(AGENT)
'''

with open("agent.py", "w") as f:
    f.write(agent_src)
print("wrote agent.py")

# COMMAND ----------
# MAGIC %md ## 4 · Run the agent (done-when #2 and #3)
# MAGIC Bare calls straight to the agent — no deployment needed to verify tool use.

# COMMAND ----------
import os  # noqa: E402

os.environ["LLM_ENDPOINT_NAME"] = llm_endpoint  # keep agent.py's default in sync with the widget
sys.path.insert(0, ".")  # so `import agent` picks up the file we just wrote
import agent as agent_mod  # noqa: E402
from mlflow.types.agent import ChatAgentMessage  # noqa: E402

bot = agent_mod.RecallsAgent(llm_endpoint=llm_endpoint)


def show(resp):
    for m in resp.messages:
        if getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                print(f"  [tool call] {tc['function']['name']}({tc['function']['arguments']})")
        elif m.role == "tool":
            print(f"  [tool result] {m.content[:200]}")
        else:
            print(f"  [{m.role}] {m.content}")


# COMMAND ----------
# MAGIC %md ### Done-when #2 — read path: "any recent listeria recalls?"

# COMMAND ----------
resp = bot.predict(messages=[ChatAgentMessage(role="user", content="any recent listeria recalls?")])
show(resp)
called = [tc["function"]["name"] for m in resp.messages if getattr(m, "tool_calls", None) for tc in m.tool_calls]
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
called = [tc["function"]["name"] for m in resp.messages if getattr(m, "tool_calls", None) for tc in m.tool_calls]
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
# MAGIC Models-from-code (`python_model="agent.py"`), tool source shipped via `code_paths`, and the
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
        python_model="agent.py",
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
