"""AgentCore Platform v1.0 — SVC-C2-134 inner domain workflow graph.

Inherits AgentBaseGraph (standard 3-slot backbone). Instantiated by
`DisputeWorkflowGraphNode.get_subgraph()`:

    pre_process  (ClauseMapNode)   ① timeline/evidence + deterministic clause map
    main         (ReconcileNode)   ②b+③+④ LLM three-way / applicability / sufficiency
    post_process (IssueDraftNode)  ⑤ deterministic issue assembly (non-assertive)

`get_output()` surfaces `dispute_analysis` as `output` for the outer
`merge_output()` (docs/02_design.md §Composition Pattern).
"""

from framework.graph.agent_base_graph import AgentBaseGraph

from src.nodes.clause_map_node import ClauseMapNode
from src.nodes.reconcile_node import ReconcileNode
from src.nodes.issue_draft_node import IssueDraftNode
from src.schemas.state import State


class DisputeWorkflowGraph(AgentBaseGraph):
    """Inner multi-step reconciliation workflow (fixed 3-slot backbone)."""

    @property
    def name(self) -> str:
        return "dispute_reconciliation_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        cfg = self.config or {}
        self._nodes["pre_process"] = ClauseMapNode(config=cfg)
        self._nodes["main"] = ReconcileNode(config=cfg)
        self._nodes["post_process"] = IssueDraftNode(config=cfg)

    # add_edges() / route() are NOT overridden — the backbone is framework-owned.

    def get_output(self, state: dict) -> dict:
        """Surface the draft analysis to the outer GraphNode.merge_output()."""
        return {
            "output": state.get("dispute_analysis", {}),
            "status": state.get("status"),
            "trace_id": state.get("trace_id"),
            "correlation_id": state.get("correlation_id"),
            "node_history": state.get("node_history", []),
        }
