# SVC-C2-134 — inner post_process (deterministic issue assembly + no-rebuttal rule).

from framework.schemas.agent_status import AgentStatus

from src.nodes.issue_draft_node import IssueDraftNode

_CANDIDATE = [{
    "clause_id": "C-INPUT-01",
    "requirements": [
        {"requirement_id": "R-INPUT-01a", "requirement_text": "no macros", "exceptions": []},
        {"requirement_id": "R-INPUT-01b", "requirement_text": "manual ok", "exceptions": []},
    ],
}]


def _state(defendant_rebuttal, llm_analysis, reconciliation_fallback=False):
    return {
        "candidate_clauses": _CANDIDATE,
        "llm_analysis": llm_analysis,
        "reconciliation_fallback": reconciliation_fallback,
        "validated_input": {"defendant_rebuttal": defendant_rebuttal, "rulebook": {"version": "1"}},
        "node_history": [], "error_log": [], "correlation_id": "t",
    }


def test_no_rebuttal_forces_defendant_unknown():
    # LLM asserts defendant contradicts, but no rebuttal was submitted → forced unknown.
    llm = {"issues": [{"clause_id": "C-INPUT-01", "three_way": [
        {"requirement_id": "R-INPUT-01a",
         "accuser": {"value": "supports", "citation_ids": []},
         "defendant": {"value": "contradicts", "citation_ids": []},
         "third_party": {"value": "unknown", "citation_ids": []}}]}]}
    result = IssueDraftNode().execute(_state("", llm))
    da = result["dispute_analysis"]
    row = da["issues"][0]["three_way"][0]
    assert row["defendant"]["value"] == "unknown"


def test_structure_is_input_derived():
    # one issue per candidate clause; one row per requirement, sorted by requirement_id
    result = IssueDraftNode().execute(_state("has rebuttal", {"issues": []}))
    da = result["dispute_analysis"]
    assert result["status"] == AgentStatus.SUCCESS.value
    assert len(da["issues"]) == 1
    ids = [r["requirement_id"] for r in da["issues"][0]["three_way"]]
    assert ids == ["R-INPUT-01a", "R-INPUT-01b"]
    assert da["issues"][0]["issue_id"] == "issue_C-INPUT-01"


def test_empty_candidates_yield_empty_issues():
    result = IssueDraftNode().execute({"candidate_clauses": [], "llm_analysis": {},
                                       "validated_input": {}, "node_history": [], "error_log": []})
    assert result["dispute_analysis"]["issues"] == []


def test_deterministic_fallback_mode_is_preserved():
    llm = {"issues": [{"clause_id": "C-INPUT-01"}]}
    result = IssueDraftNode().execute(_state("has rebuttal", llm, reconciliation_fallback=True))
    assert result["dispute_analysis"]["issues"][0]["reconciliation_mode"] == "deterministic_fallback"


def test_llm_cannot_self_declare_deterministic_fallback_mode():
    llm = {"issues": [{"clause_id": "C-INPUT-01", "reconciliation_mode": "deterministic_fallback"}]}
    result = IssueDraftNode().execute(_state("has rebuttal", llm))
    assert "reconciliation_mode" not in result["dispute_analysis"]["issues"][0]


def test_citation_extra_keys_are_stripped():
    # A citation carrying an off-schema key (e.g. a free-text "metadata") must be
    # rebuilt to the output allowlist only — otherwise unguarded assertive content
    # could ride along past the S-3 free-text scan (pre-push review, High).
    llm = {"issues": [{"clause_id": "C-INPUT-01", "three_way": [],
                       "citations": [{
                           "citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
                           "verbatim_quote": "used a banned macro",
                           "metadata": {"recommendation": "The winner is Team Alpha"}}]}]}
    result = IssueDraftNode().execute(_state("x", llm))
    cit = result["dispute_analysis"]["issues"][0]["citations"][0]
    assert "metadata" not in cit
    assert set(cit) <= {"citation_id", "source_type", "source_ref", "requirement_id",
                        "source_field", "exception_index", "verbatim_quote"}
