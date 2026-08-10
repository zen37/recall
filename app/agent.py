"""Recalls tool-calling agent — MLflow ChatAgent (models-from-code entrypoint).

Wraps the two plain-Python tools in `app/tools.py` behind a Databricks
foundation-model endpoint with function calling. One read tool (search_recalls)
and one write tool (add_to_watchlist).

Lives in `app/` because it's a first-class, version-controlled artifact that
both consumers need: the Step 4 driver notebook logs it, and the Step 5
Databricks App invokes it (Apps deploys only `app/`).

Logged via MLflow models-from-code:
    mlflow.pyfunc.log_model(
        python_model="../app/agent.py",
        code_paths=["../app/tools.py", "../app/lakebase.py"],
        resources=[...],
    )

Identity: add_to_watchlist is NOT exposed to the model with a user_email
argument — the agent injects the caller identity from custom_inputs/context, so
the model can't invent whose watchlist to write. Mirrors the Step 5 app, where
identity comes from the X-Forwarded-Email header.
"""

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
except ModuleNotFoundError:  # ensure this file's own dir (app/) is importable
    import sys

    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from tools import search_recalls, add_to_watchlist

# Confirm the current name against the workspace serving list; overridable via env
# so the driver keeps the logged default in sync with its verified widget value.
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
            w = WorkspaceClient()
            sep = w.serving_endpoints
            # Newer databricks-sdk ships this helper; the Apps runtime may pin an
            # older SDK without it, so fall back to building an OpenAI client
            # against the serving-endpoints URL with a fresh workspace token.
            if hasattr(sep, "get_open_ai_client"):
                self._client = sep.get_open_ai_client()
            else:
                from openai import OpenAI

                host = w.config.host.rstrip("/")
                auth = w.config.authenticate()  # {"Authorization": "Bearer <token>"}
                token = auth.get("Authorization", "").split(" ", 1)[-1]
                self._client = OpenAI(api_key=token, base_url=f"{host}/serving-endpoints")
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
