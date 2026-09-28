"""AgentCore Platform v1.0 — SVC-C2-134 outer `main` GraphNode.

Wraps the inner `DisputeWorkflowGraph`. Defined in `src/graph/` (NOT `src/nodes/`)
so the PB-6 node-discovery probe does not instantiate a GraphNode and run a whole
subgraph inside the single-node order check.

Overrides `execute()` to short-circuit an upstream (pre_process) error — the
`pre→main` edge is unconditional, so without this a rejected input would still run
the LLM workflow (docs/02_design.md §Data Flow / §Composition Pattern).
"""

import json
from typing import ClassVar

from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event


class DisputeWorkflowGraphNode(GraphNode):
    """Runs the inner dispute-reconciliation workflow and merges its result."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        # Upstream (pre_process) reject short-circuit: do not run the LLM workflow.
        if state.get("status") == AgentStatus.ERROR.value:
            emit_trace_event("upstream_error_passthrough", {}, state)
            return {}
        emit_trace_event("dispute_workflow_invoked", {}, state)
        return super().execute(state)

    def get_subgraph(self):
        from src.graph.domain_workflow_graph import DisputeWorkflowGraph
        agent = DisputeWorkflowGraph(config=self._config)
        agent.compile()
        return agent

    def extract_input(self, state: dict) -> str:
        # Inner graph receives a string only; pack validated_input as a JSON envelope.
        return json.dumps({"validated_input": state.get("validated_input", {})}, default=str)

    def merge_output(self, state: dict, sub_result: dict) -> dict:
        # Explicit field mapping — no raw sub_result pass-through.
        return {
            "dispute_analysis": sub_result.get("output", {}) or {},
            "status": sub_result.get("status", AgentStatus.SUCCESS.value),
        }
