"""AgentCore Platform v1.0 — SVC-C2-134 outer post_process.

Runs the S-3 Provenance verification (needs `state` source text), applies the
structural adjudication-boundary and non-assertion guard, maps 要配慮 flags onto the
issues it builds (temporal consistency), and DECLARES `provenance_verified` inside
`formatted_output`. The `_extra_security_gate_output(result)` hook then enforces the
declared result on `result` alone (verify-in-execute / enforce-in-gate).
"""

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

from src.services import provenance


class PostProcessNode(FunctionNode):
    """Verify provenance, block adjudication fields, and finalise output."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    # ── S-3 domain gate: enforce the DECLARED result on `result` alone ───────
    def _extra_security_gate_output(self, result: dict) -> dict:
        provenance.gate_check(result, self._config)
        return result

    def execute(self, state: dict) -> dict:
        analysis = state.get("dispute_analysis") or {}
        sources = {
            "accuser_assertion": state.get("accuser_assertion", ""),
            "defendant_rebuttal": state.get("defendant_rebuttal", ""),
            "third_party_facts": state.get("third_party_facts", []) or [],
            "rulebook": state.get("rulebook", {}) or {},
        }

        outcome = provenance.verify_with_sources(analysis, sources, self._config)

        if outcome["terminal"] is not None:
            emit_trace_event("provenance_rejected", {"reason": outcome["terminal"]}, state)
            return {
                "status": AgentStatus.ERROR.value,
                "formatted_output": {"provenance_verified": False},
                "error_log": [f"S-3 provenance terminal reject: {outcome['terminal']}"],
            }

        issues = outcome["issues"]

        # Map source-level 要配慮 flags onto the issues that cite the flagged source;
        # withhold those issues' free-text and mark manual-escalation required.
        flagged = state.get("sensitive_flags", []) or []
        flag_index = {(f.get("source_type"), f.get("source_ref")) for f in flagged}
        flag_cats: dict[tuple, set] = {}
        for f in flagged:
            flag_cats.setdefault((f.get("source_type"), f.get("source_ref")), set()).add(f.get("category"))

        escalation: list[str] = []
        for issue in issues:
            cats: set = set()
            for c in issue.get("citations", []) or []:
                key = (c.get("source_type"), c.get("source_ref"))
                if key in flag_index:
                    cats |= flag_cats.get(key, set())
            if cats:
                iid = issue.get("issue_id", "")
                escalation.append(iid)
                cat_str = ", ".join(sorted(str(c) for c in cats))
                # Parentheses, NOT [brackets]: the S-3 citation-marker regex reads
                # any [token] as an inline citation reference, so a bracketed
                # placeholder would be flagged as a dangling citation and reject the
                # whole request (withholding must not trip provenance).
                issue["issue_summary"] = f"(withheld pending manual escalation: {cat_str})"
                if isinstance(issue.get("evidence_sufficiency"), dict):
                    issue["evidence_sufficiency"]["gaps"] = []
                issue["referee_questions"] = ["(withheld pending manual escalation)"]
                issue["manual_escalation_required"] = True

        rulebook_version = (
            state.get("dispute_context", {}).get("rulebook_version")
            or sources["rulebook"].get("version", "")
        )
        formatted_output = {
            "rulebook_version": rulebook_version,
            "provenance_verified": True,
            "issues": issues,
            "unresolved_notes": analysis.get("unresolved_notes", []) or [],
            "escalation_required": escalation,
        }

        emit_trace_event(
            "output_finalized",
            {"issues": len(issues), "downgrades": outcome["downgrades"],
             "issue_downgrades": outcome["issue_downgrades"],
             "normalizations": outcome["normalizations"], "escalations": len(escalation)},
            state,
        )
        return {
            "formatted_output": formatted_output,
            "sensitive_escalation": escalation,
            "status": AgentStatus.SUCCESS.value,
        }
