"""AgentCore Platform v1.0 — SVC-C2-134 outer graph.

Cat 2 — AgentBaseGraph (fixed 5-node backbone). The domain complexity lives in the
inner `DisputeWorkflowGraph`, wrapped by `DisputeWorkflowGraphNode` in the `main`
slot. `add_edges()` / `route()` are NOT overridden (framework-owned backbone).

    START → initialize → pre_process → main(GraphNode) → {route} → post_process → finalize → END
"""

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.schemas.invocation_context import InvocationContext

from src.graph.dispute_workflow_node import DisputeWorkflowGraphNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.post_process_node import PostProcessNode
from src.schemas.state import State
from src.services import pii
from src.services.config_spec import validate_config


class Graph(AgentBaseGraph):
    """Esports Tournament Dispute Adjudication Support Agent (SVC-C2-134)."""

    @property
    def name(self) -> str:
        return "svc_c2_134"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        # Domain config validation (fail-closed) before the framework's own checks.
        validate_config(self.config or {})
        super()._validate_config()

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        cfg = self.config or {}
        self._nodes["pre_process"] = PreProcessNode(config=cfg)
        self._nodes["main"] = DisputeWorkflowGraphNode(config=cfg)
        self._nodes["post_process"] = PostProcessNode(config=cfg)

    # add_edges() is NOT overridden — the backbone wiring is framework-owned.

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict | None = None,
    ) -> dict:
        """Pre-mask 要配慮 detection at the only pre-mask seam (design D8-bis).

        The framework default S-2 gate masks contact PII (email/phone) in
        `user_input` to `[MASKED]` before ANY node's `execute()` runs, so a contact
        channel is invisible to `pre_process`. Detect it here, on the raw payload,
        and thread the PII-free flags via `input_context` — NOT a `_PII_SCAN_FIELD`,
        so it survives masking.

        This agent's only input is the JSON `user_input` string; it consumes nothing
        from a caller-supplied `input_context`. Because `input_context` is not a
        `_PII_SCAN_FIELD`, the framework would checkpoint any caller-supplied keys
        verbatim — so we pass an `input_context` carrying ONLY the computed pre-mask
        flags (PII-free) and DROP caller-supplied keys. This also makes escalation
        un-spoofable: a caller cannot force or suppress the flag set. All other invoke
        behaviour (checkpointer/HITL/output shaping) is delegated to L1.
        """
        ic = {pii.PREMASK_FLAGS_INPUT_CONTEXT_KEY: pii.sensitive_flags_from_raw(user_input)}
        return super().invoke(user_input, session_id=session_id, ctx=ctx, input_context=ic)
