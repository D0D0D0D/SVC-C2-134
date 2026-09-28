"""AgentCore Platform v1.0 — SVC-C2-134 inner post_process (issue drafting).

Deterministic assembly: rebuilds the fixed issue structure from the candidate
clauses (one issue per candidate clause ascending; one three_way row and one
candidate_rule_applicability record per requirement, sorted by requirement_id) and
folds the LLM's VALUES into it. The LLM never decides which issues/requirements
exist — so issue_id, count, row count, and ordering are reproducible for the same
input (docs/02_design.md §Issue segmentation).
"""

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.agent_status import AgentStatus
from framework.schemas.trust_level import TrustLevel
from shared.utils.audit_logger import emit_trace_event

_VALUES = ("supports", "contradicts", "unknown")
_COVERAGE = ("covered", "partial", "unknown")
_PARTIES = ("accuser", "defendant", "third_party")
_RECONCILIATION_MODES = ("deterministic_fallback",)
# Output allowlist for a citation (design §Output contract). Any other key the LLM
# adds (e.g. a free-text "metadata"/"recommendation") is dropped here so it cannot
# smuggle unguarded, assertive content past the S-3 free-text scan (which only walks
# the named free-text paths, not arbitrary citation sub-objects).
_CITATION_KEYS = (
    "citation_id", "source_type", "source_ref", "requirement_id",
    "source_field", "exception_index", "verbatim_quote",
)


def _party_cell(llm_row: dict, party: str) -> dict:
    pc = llm_row.get(party) or {}
    value = pc.get("value")
    if value not in _VALUES:
        value = "unknown"
    ids = [c for c in (pc.get("citation_ids") or []) if isinstance(c, str)]
    return {"value": value, "citation_ids": ids}


class IssueDraftNode(FunctionNode):
    """Assemble the non-assertive dispute_analysis from candidates + LLM values."""

    required_trust_level: ClassVar[TrustLevel] = TrustLevel.VERIFIED_EXTERNAL

    def __init__(self, config: dict | None = None) -> None:
        super().__init__()
        self._config = config or {}

    def execute(self, state: dict) -> dict:
        candidates = state.get("candidate_clauses", []) or []
        analysis = state.get("llm_analysis", {}) or {}
        validated_input = state.get("validated_input", {}) or {}
        reconciliation_fallback = state.get("reconciliation_fallback") is True
        rulebook_version = (validated_input.get("rulebook", {}) or {}).get("version", "")
        # Determinism: no rebuttal submitted ("") ⇒ defendant column is always
        # `unknown` (never `contradicts`) — enforced in code, not left to the LLM.
        no_rebuttal = not validated_input.get("defendant_rebuttal")

        llm_by_clause = {}
        for it in (analysis.get("issues") or []):
            if isinstance(it, dict) and it.get("clause_id"):
                llm_by_clause[it["clause_id"]] = it

        issues = []
        for clause in candidates:  # already ascending clause_id
            cid = clause["clause_id"]
            llm_issue = llm_by_clause.get(cid, {})
            llm_tw = {row.get("requirement_id"): row for row in (llm_issue.get("three_way") or []) if isinstance(row, dict)}
            llm_ap = {rec.get("requirement_id"): rec for rec in (llm_issue.get("candidate_rule_applicability") or []) if isinstance(rec, dict)}

            three_way = []
            applic = []
            for req in clause["requirements"]:  # sorted by requirement_id
                rid = req["requirement_id"]
                row = llm_tw.get(rid, {})
                defendant = ({"value": "unknown", "citation_ids": []}
                             if no_rebuttal else _party_cell(row, "defendant"))
                three_way.append({
                    "requirement_id": rid,
                    "accuser": _party_cell(row, "accuser"),
                    "defendant": defendant,
                    "third_party": _party_cell(row, "third_party"),
                })
                ap = llm_ap.get(rid, {})
                coverage = ap.get("coverage")
                if coverage not in _COVERAGE:
                    coverage = "unknown"
                applic.append({
                    "clause_id": cid,
                    "requirement_id": rid,
                    "coverage": coverage,
                    "citation_ids": [c for c in (ap.get("citation_ids") or []) if isinstance(c, str)],
                })

            es = llm_issue.get("evidence_sufficiency") or {}
            status = es.get("status")
            if status not in ("sufficient", "insufficient", "unknown"):
                status = "unknown"
            citations = [
                {k: c[k] for k in _CITATION_KEYS if k in c}
                for c in (llm_issue.get("citations") or []) if isinstance(c, dict)
            ]

            drafted_issue = {
                "issue_id": f"issue_{cid}",
                "issue_summary": llm_issue.get("issue_summary", "") if isinstance(llm_issue.get("issue_summary"), str) else "",
                "clause_refs": [cid],
                "three_way": three_way,
                "candidate_rule_applicability": applic,
                "evidence_sufficiency": {"status": status, "gaps": [g for g in (es.get("gaps") or []) if isinstance(g, str)]},
                "referee_questions": [q for q in (llm_issue.get("referee_questions") or []) if isinstance(q, str)],
                "citations": citations,
            }
            if reconciliation_fallback:
                drafted_issue["reconciliation_mode"] = _RECONCILIATION_MODES[0]
            issues.append(drafted_issue)

        dispute_analysis = {
            "rulebook_version": rulebook_version,
            "issues": issues,
            "unresolved_notes": [n for n in (analysis.get("unresolved_notes") or []) if isinstance(n, str)],
        }
        emit_trace_event("issue_drafting", {"issues": len(issues)}, state)
        return {"dispute_analysis": dispute_analysis, "status": AgentStatus.SUCCESS.value}
