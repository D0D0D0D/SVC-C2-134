"""AgentCore Platform v1.0 — SVC-C2-134 standalone HTTP entry point.

Adapter only — no business logic. Loads the runtime config (`config/config.yaml`)
and passes it to the graph so the manifest's runtime knobs actually reach the nodes
(a server that ignores config is a defect class this template guards against).
No LLM client / model id is constructed here (by design —
the platform injects a `BaseLLM` via `src.services.llm_seam.set_llm`; absent one the
graph degrades deterministically).

Request body: `input` is a JSON string of the dispute bundle (accuser_assertion,
defendant_rebuttal, third_party_facts, rulebook, dispute_context) — `pre_process`
parses and validates it.
"""

import os
import secrets
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory
from src.graph.graph import Graph

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "config.yaml"


def _load_config() -> dict:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}


app = FastAPI(title="SVC-C2-134 — Esports Tournament Dispute Adjudication Support Agent")

agent = Graph(config=_load_config())
agent.compile()
agent.provision_secrets(secrets_factory(namespace="svc", agent_name="svc-c2-134"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    trust = getattr(request.state, "trust_level", TrustLevel.ANONYMOUS)
    # Standalone/STG caller auth. Entry-point
    # caller credential, read before any InvocationContext exists (the
    # entry-point exception); not an agent secret, never stored in state.
    expected = os.environ.get("INVOKE_AUTH_TOKEN")
    if expected and trust is TrustLevel.ANONYMOUS:
        supplied = request.headers.get("authorization", "")
        if not secrets.compare_digest(supplied.encode(), f"Bearer {expected}".encode()):
            raise HTTPException(status_code=401, detail="Token is invalid or expired.")
        trust = TrustLevel.VERIFIED_EXTERNAL
    with bound_secrets(agent._secrets_provider):
        ctx = InvocationContext(
            session_id=req.session_id or str(uuid4()),
            caller_trust_level=trust,
            caller_id=getattr(request.state, "caller_id", ""),
        )
        return agent.invoke(req.input, ctx=ctx)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "svc-c2-134"}
