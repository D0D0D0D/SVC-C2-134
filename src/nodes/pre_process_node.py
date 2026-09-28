"""AgentCore Platform v1.0 — SVC-C2-134 outer pre_process.

Input validation (allowlist / type / version-pin / quality), player-PII
ID-isation, and 要配慮 (sensitive) source-level flagging.

S-2 domain rejection lives in `_extra_security_gate_input()` (runs before execute
and short-circuits on reject) but is *lenient on absent input* so the framework
PB-6 bare-state probe still reaches execute; execute handles the empty/absent case
and produces `validated_input` for valid input.
"""

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import input_gate, pii


class PreProcessNode(FunctionNode):
    """Validate, ID-ise, and flag the incoming dispute bundle."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    # ── S-2 domain gate (before execute; short-circuits on reject) ───────────
    def _extra_security_gate_input(self, state: dict) -> dict:
        raw = state.get("user_input")
        # Lenient on absent/empty input: let execute return the empty-input error
        # (keeps the framework PB-6 bare-state order intact). Only reject PRESENT
        # but invalid input here (fail-closed, before any processing).
        if not raw:
            return state
        ok, rejected, _ = input_gate.validate(raw, self._config)
        if not ok:
            emit_trace_event("input_rejected", {"count": len(rejected)}, state)
            state = dict(state)
            state["status"] = AgentStatus.ERROR.value
            state["rejected"] = rejected
            state["error_log"] = [f"S-2 input rejected: {r['field']} — {r['reason']}" for r in rejected]
        return state

    def execute(self, state: dict) -> dict:
        config = self._config
        raw = state.get("user_input")
        ok, rejected, obj = input_gate.validate(raw, config)
        if not ok:
            emit_trace_event("input_rejected", {"count": len(rejected)}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "rejected": rejected,
                "error_log": [f"input rejected: {r['field']} — {r['reason']}" for r in rejected],
            }

        # 要配慮 flags: prefer the pre-mask set computed at the invoke boundary
        # (design D8-bis / Graph.invoke). Contact PII (email/phone) is masked to
        # [MASKED] by the framework S-2 gate BEFORE this node runs, so it can only be
        # detected on the raw payload upstream; those flags arrive via input_context
        # (PII-free, un-spoofable). Fall back to detecting on the already-parsed obj
        # for direct-node callers that bypass invoke — health/harassment lexicons
        # survive masking; contact PII does not (documented limitation of the
        # fallback path).
        premask = None
        ctx_in = state.get("input_context")
        if isinstance(ctx_in, dict):
            cand = ctx_in.get(pii.PREMASK_FLAGS_INPUT_CONTEXT_KEY)
            if isinstance(cand, list):
                premask = cand
        sensitive_flags = premask if premask is not None else pii.collect_sensitive_flags(obj)

        # ID-ise player-PII in local scope; pii_map (token→original) is discarded
        # here and NEVER written to State / checkpoint.
        idised, _pii_map = pii.idise_input(obj)
        idised.setdefault("defendant_rebuttal", "")

        validated_input = {
            "accuser_assertion": idised.get("accuser_assertion", ""),
            "defendant_rebuttal": idised.get("defendant_rebuttal", ""),
            "third_party_facts": idised.get("third_party_facts", []),
            "rulebook": idised.get("rulebook", {}),
            "dispute_context": idised.get("dispute_context", {}),
        }

        emit_trace_event(
            "input_validated",
            {"facts": len(validated_input["third_party_facts"]),
             "sensitive_flags": len(sensitive_flags),
             "has_rebuttal": bool(validated_input["defendant_rebuttal"])},
            state,
        )

        return {
            # ID-ised fields only; raw user_input cleared so no raw text lingers.
            "user_input": "",
            "accuser_assertion": validated_input["accuser_assertion"],
            "defendant_rebuttal": validated_input["defendant_rebuttal"],
            "third_party_facts": validated_input["third_party_facts"],
            "rulebook": validated_input["rulebook"],
            "dispute_context": validated_input["dispute_context"],
            "validated_input": validated_input,
            "sensitive_flags": sensitive_flags,
            "rejected": [],
            "status": AgentStatus.SUCCESS.value,
        }
