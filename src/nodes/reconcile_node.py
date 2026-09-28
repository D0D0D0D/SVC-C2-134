"""AgentCore Platform v1.0 — SVC-C2-134 inner main (three-way reconciliation).

The single LLM step: applies the facts to each candidate clause's requirements and
produces the per-issue analysis (three_way values + coverage + sufficiency + neutral
free text + citations) as one structured object. The LLM fills VALUES only — the
issue/requirement structure is fixed upstream. Degrades to a deterministic all-
`unknown` analysis when no LLM is injected (best-effort enrichment seam).
"""

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import reconciliation
from src.services.llm_seam import get_llm


class ReconcileNode(FunctionNode):
    """LLM three-way reconciliation of parties × clause requirements."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        validated_input = state.get("validated_input", {}) or {}
        candidates = state.get("candidate_clauses", []) or []
        min_quote_len = int((self._config.get("provenance", {}) or {}).get("min_quote_len", 12))

        llm = get_llm()
        if llm is None or not candidates:
            emit_trace_event("reconciliation", {"mode": "deterministic_fallback", "issues": len(candidates)}, state)
            return {
                "llm_analysis": reconciliation.fallback_analysis(candidates),
                "reconciliation_fallback": True,
                "status": AgentStatus.SUCCESS.value,
            }

        prompt = reconciliation.build_prompt(validated_input, candidates, min_quote_len)
        analysis = None
        try:
            response = llm.complete([{"role": "user", "content": prompt}])
            content = response.get("content", "") if isinstance(response, dict) else str(response)
            analysis = reconciliation.parse_response(content)
            emit_trace_event(
                "llm_call",
                {"model": (response.get("model") if isinstance(response, dict) else None),
                 "usage": (response.get("usage") if isinstance(response, dict) else None),
                 "parsed": analysis is not None},
                state,
            )
        except Exception as exc:  # noqa: BLE001 — degrade rather than crash the workflow
            emit_trace_event("llm_error", {"error": type(exc).__name__}, state)
            analysis = None

        fallback = analysis is None
        if fallback:
            analysis = reconciliation.fallback_analysis(candidates)

        return {
            "llm_analysis": analysis,
            "reconciliation_fallback": fallback,
            "status": AgentStatus.SUCCESS.value,
        }
