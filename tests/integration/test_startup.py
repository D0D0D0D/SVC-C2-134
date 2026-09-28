# SVC-C2-134 — startup exercise: importing the server compiles the graph and
# proves config/config.yaml actually reaches the nodes (a server that ignores
# config is the defect class this guards against). The route
# tests below additionally drive the /invoke handler end-to-end (auth + ctx
# construction + full agent.invoke) so the entry-point adapter is exercised, not
# just imported — the standalone adapter is otherwise never run by CI.

import asyncio
import json
from types import SimpleNamespace

from framework.schemas.trust_level import TrustLevel

from tests.conftest import DISPUTE

_DISPUTE_JSON = json.dumps(DISPUTE)


def test_server_import_compiles_and_propagates_config():
    import src.api.server as srv

    assert srv.agent is not None
    # config.yaml value must have reached the pre_process node (not defaulted away)
    pre = srv.agent._nodes["pre_process"]
    assert pre._config.get("input_quality", {}).get("max_field_chars") == 20000
    post = srv.agent._nodes["post_process"]
    assert post._config.get("provenance", {}).get("min_quote_len") == 12


def test_trust_level_enum_not_lowercase_string():
    # startup uses TrustLevel enums (lowercase string literals would ValueError at
    # InvocationContext.from_state and kill startup in production).
    import src.api.server as srv  # noqa: F401
    assert TrustLevel.VERIFIED_EXTERNAL.value == "VERIFIED_EXTERNAL"


def _fake_request(headers=None):
    return SimpleNamespace(headers=headers or {}, state=SimpleNamespace())


def test_invoke_route_authed_succeeds(monkeypatch):
    # Full route path: caller supplies the entry-point auth token -> trust is
    # elevated to VERIFIED_EXTERNAL -> agent.invoke runs (offline deterministic
    # fallback) -> a well-formed, provenance-verified envelope is returned.
    import src.api.server as srv
    from src.services.llm_seam import set_llm

    set_llm(None)  # deterministic offline path
    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "tok-123")
    req = srv.InvokeRequest(input=_DISPUTE_JSON, session_id="s1")
    result = asyncio.run(srv.invoke(req, _fake_request({"authorization": "Bearer tok-123"})))
    assert result["status"] == "success"
    assert result["output"]["provenance_verified"] is True


def test_invoke_route_bad_token_rejected(monkeypatch):
    # A wrong bearer token when INVOKE_AUTH_TOKEN is set -> 401 (entry-point caller
    # auth, before any InvocationContext exists).
    import src.api.server as srv
    from fastapi import HTTPException

    monkeypatch.setenv("INVOKE_AUTH_TOKEN", "tok-123")
    req = srv.InvokeRequest(input=_DISPUTE_JSON, session_id="s2")
    try:
        asyncio.run(srv.invoke(req, _fake_request({"authorization": "Bearer wrong"})))
        raised = None
    except HTTPException as exc:
        raised = exc
    assert raised is not None and raised.status_code == 401


def test_invoke_route_unauthenticated_denied_by_trust_gate(monkeypatch):
    # No caller trust and no INVOKE_AUTH_TOKEN -> ANONYMOUS -> the S-1 trust gate
    # denies the VERIFIED_EXTERNAL nodes; the route returns status=error (the trust
    # gate is enforced through the real adapter, not just in unit tests).
    import src.api.server as srv
    from src.services.llm_seam import set_llm

    set_llm(None)
    monkeypatch.delenv("INVOKE_AUTH_TOKEN", raising=False)
    req = srv.InvokeRequest(input=_DISPUTE_JSON, session_id="s3")
    result = asyncio.run(srv.invoke(req, _fake_request()))
    assert result["status"] == "error"
