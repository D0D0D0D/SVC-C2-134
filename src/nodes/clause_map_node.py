"""AgentCore Platform v1.0 — SVC-C2-134 inner pre_process (clause mapping).

Deterministic clause-requirement candidate mapping (internal Tool). Produces the
candidate-clause set — a pure function of the version-pinned input + config — that
fixes the downstream issue structure. Receives the parent `validated_input` via the
GraphNode string envelope (docs/02_design.md §Node Configuration — inner workflow).
"""

import json
from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import clause_matcher


class ClauseMapNode(FunctionNode):
    """Map the dispute onto candidate rulebook clauses (deterministic)."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        # GraphNode passes the parent validated_input as a JSON envelope string.
        validated_input = state.get("validated_input")
        if not isinstance(validated_input, dict):
            try:
                envelope = json.loads(state.get("user_input") or "{}")
            except (ValueError, TypeError):
                envelope = {}
            validated_input = envelope.get("validated_input", {}) if isinstance(envelope, dict) else {}

        cm = self._config.get("clause_match", {}) or {}
        top_k = cm.get("top_k", 8)
        hybrid = bool(cm.get("hybrid_search", True))
        candidates = clause_matcher.map_clauses(validated_input, top_k, hybrid)

        emit_trace_event("clause_mapping", {"candidates": len(candidates)}, state)
        return {
            "validated_input": validated_input,
            "candidate_clauses": candidates,
            "status": AgentStatus.SUCCESS.value,
        }
