# SVC-C2-134 — S-3 Provenance contract (verbatim + locator + bidirectional).

import copy

import pytest

from src.services import provenance
from tests.conftest import DISPUTE, VALID_ANALYSIS


def _sources():
    return {
        "accuser_assertion": DISPUTE["accuser_assertion"],
        "defendant_rebuttal": DISPUTE["defendant_rebuttal"],
        "third_party_facts": DISPUTE["third_party_facts"],
        "rulebook": DISPUTE["rulebook"],
    }


def _one_issue(citations, three_way=None, summary="Neutral summary of the point [c1].", **extra):
    issue = {
        "issue_id": "issue_C-INPUT-01",
        "clause_id": "C-INPUT-01",
        "issue_summary": summary,
        "three_way": three_way if three_way is not None else [
            {"requirement_id": "R-INPUT-01a",
             "accuser": {"value": "unknown", "citation_ids": []},
             "defendant": {"value": "unknown", "citation_ids": []},
             "third_party": {"value": "unknown", "citation_ids": []}},
        ],
        "candidate_rule_applicability": [],
        "evidence_sufficiency": {"status": "unknown", "gaps": []},
        "referee_questions": [],
        "citations": citations,
    }
    issue.update(extra)
    return {"issues": [issue], "unresolved_notes": []}


def test_valid_analysis_passes():
    out = provenance.verify_with_sources(copy.deepcopy(VALID_ANALYSIS), _sources(), {})
    assert out["terminal"] is None
    assert len(out["issues"]) == 2


def test_clause_citation_valid():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
         "verbatim_quote": "hardware or software macros"},
    ])
    assert provenance.verify_with_sources(analysis, _sources(), {})["terminal"] is None


def test_cross_requirement_quote_mismatch_terminal():
    # Review finding (High): quote is verbatim in R-INPUT-01b but the citation names R-INPUT-01a.
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
         "verbatim_quote": "Manual input sequences performed by the player"},
    ])
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is not None
    assert "substring" in out["terminal"]


def test_missing_requirement_id_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "verbatim_quote": "hardware or software macros"},
    ])
    assert "missing requirement_id" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_unknown_requirement_id_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-NOPE", "verbatim_quote": "hardware or software macros"},
    ])
    assert "unknown requirement_id" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_mismatched_requirement_id_terminal():
    # requirement belongs to a DIFFERENT clause than source_ref
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-CONDUCT-02a", "verbatim_quote": "hardware or software macros"},
    ])
    assert "mismatched" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_exception_citation_valid():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "exception", "exception_index": 0,
         "verbatim_quote": "Accessibility remapping approved"},
    ])
    assert provenance.verify_with_sources(analysis, _sources(), {})["terminal"] is None


def test_exception_index_out_of_range_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "exception", "exception_index": 5,
         "verbatim_quote": "Accessibility remapping approved"},
    ])
    assert "out of range" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_negative_exception_index_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "exception", "exception_index": -1,
         "verbatim_quote": "Accessibility remapping approved"},
    ])
    assert "negative exception_index" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_exception_index_with_requirement_text_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "requirement_text", "exception_index": 0,
         "verbatim_quote": "hardware or software macros"},
    ])
    assert "exception_index present" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_non_clause_carrying_locator_is_normalized():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
         "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
         "exception_index": 0, "verbatim_quote": "used a banned macro"},
    ])
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is None
    assert out["normalizations"] == 3
    assert set(out["issues"][0]["citations"][0]).isdisjoint(
        {"requirement_id", "source_field", "exception_index"}
    )


def test_sub_min_quote_len_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
         "verbatim_quote": "macro"},  # < 12 chars
    ])
    assert "min_quote_len" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_dangling_marker_downgrades_issue_to_deterministic_fallback():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        summary="References a citation that does not exist [c9].",
    )
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is None
    assert out["issue_downgrades"] == 1
    issue = out["issues"][0]
    assert issue["reconciliation_mode"] == "deterministic_fallback"
    assert issue["citations"] == []
    assert all(cell[party]["value"] == "unknown"
               for cell in issue["three_way"]
               for party in ("accuser", "defendant", "third_party"))


def test_unknown_third_party_source_ref_remains_terminal():
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "third_party", "source_ref": "F-NOT-FOUND",
         "verbatim_quote": "12-input burst within 180 milliseconds"},
    ])
    assert "matches no fact_id" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_false_quote_remains_terminal_even_when_issue_has_dangling_marker():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "this quote does not occur in the source"}],
        summary="Unresolved placeholder [clause].",
    )
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert "not a substring" in out["terminal"]


def test_dangling_three_way_citation_terminal():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        three_way=[{"requirement_id": "R-INPUT-01a",
                    "accuser": {"value": "contradicts", "citation_ids": ["c9"]},
                    "defendant": {"value": "unknown", "citation_ids": []},
                    "third_party": {"value": "unknown", "citation_ids": []}}],
    )
    assert "dangling three_way" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_prohibited_field_terminal():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}])
    analysis["issues"][0]["verdict"] = "Bravo loses"
    assert "prohibited output field" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_assertive_lexicon_terminal():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        summary="Team Bravo is disqualified for this violation [c1].")
    assert "assertive ruling wording" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_coverage_downgrade_nonterminal():
    # supports cell with NO citation -> downgraded to unknown (non-terminal).
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        three_way=[{"requirement_id": "R-INPUT-01a",
                    "accuser": {"value": "supports", "citation_ids": []},
                    "defendant": {"value": "unknown", "citation_ids": []},
                    "third_party": {"value": "unknown", "citation_ids": []}}],
    )
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is None
    assert out["downgrades"] == 1
    assert out["issues"][0]["three_way"][0]["accuser"]["value"] == "unknown"


def test_downgrade_disabled_when_config_false():
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        three_way=[{"requirement_id": "R-INPUT-01a",
                    "accuser": {"value": "supports", "citation_ids": []},
                    "defendant": {"value": "unknown", "citation_ids": []},
                    "third_party": {"value": "unknown", "citation_ids": []}}],
    )
    cfg = {"provenance": {"require_citation_for_supports_contradicts": False}}
    out = provenance.verify_with_sources(analysis, _sources(), cfg)
    assert out["terminal"] is None
    assert out["downgrades"] == 0
    assert out["issues"][0]["three_way"][0]["accuser"]["value"] == "supports"


# ── gate_check (result-only re-enforcement) ──────────────────────────────────

def _formatted(analysis, verified=True):
    # Real post_process output has no `clause_id` on issues (issue_draft emits
    # issue_id + clause_refs); strip the fixture's convenience key so the shape
    # matches what the output-key allowlist enforces at the gate.
    issues = [{k: v for k, v in it.items() if k != "clause_id"} for it in analysis["issues"]]
    return {"formatted_output": {"provenance_verified": verified, "issues": issues},
            "status": "success"}


def test_gate_check_valid_passes():
    provenance.gate_check(_formatted(copy.deepcopy(VALID_ANALYSIS)), {})


def test_gate_check_rejects_provenance_false():
    with pytest.raises(provenance.ProvenanceError):
        provenance.gate_check(_formatted(copy.deepcopy(VALID_ANALYSIS), verified=False), {})


def test_gate_check_rejects_prohibited_field():
    a = copy.deepcopy(VALID_ANALYSIS)
    a["issues"][0]["penalty"] = "2 rounds"
    with pytest.raises(provenance.ProvenanceError):
        provenance.gate_check(_formatted(a), {})


def test_gate_check_skips_on_error_status():
    # terminal reject already set status=error — gate must not double-raise.
    provenance.gate_check({"formatted_output": {"provenance_verified": False}, "status": "error"}, {})


# ── adversarial-review regressions (pre-push) ──────────────────────────

def test_party_citation_wrong_source_ref_terminal():
    # An accuser citation with a bogus source_ref must reject — otherwise it would
    # verify AND dodge sensitive-issue withholding (the flag key is ("accuser","accuser")).
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "accuser", "source_ref": "x",
         "verbatim_quote": "used a banned macro"},
    ])
    assert "source_ref must be 'accuser'" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_unresolved_notes_assertive_terminal():
    # unresolved_notes is a side free-text path; an assertive ruling there must reject.
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}])
    analysis["unresolved_notes"] = ["The winner is Team Alpha."]
    assert "assertive" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_gate_check_rejects_assertive_unresolved_notes():
    fo = {"formatted_output": {"provenance_verified": True, "issues": [],
                               "unresolved_notes": ["We rule that Bravo forfeits the match."]},
          "status": "success"}
    with pytest.raises(provenance.ProvenanceError):
        provenance.gate_check(fo, {})


def test_masked_token_in_summary_is_not_dangling_marker():
    # [MASKED]/[PII_n] tokens share the [token] shape but are NOT citation markers;
    # a legitimate summary that mentions a masked entity must not falsely reject.
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}],
        summary="[MASKED] alleges a macro was used [c1].")
    assert provenance.verify_with_sources(analysis, _sources(), {})["terminal"] is None


def test_gate_check_success_without_formatted_output_raises():
    # fail-closed: a non-error result MUST carry a verified formatted_output dict.
    with pytest.raises(provenance.ProvenanceError):
        provenance.gate_check({"status": "success"}, {})
    with pytest.raises(provenance.ProvenanceError):
        provenance.gate_check({"status": "success", "formatted_output": None}, {})


def test_bare_string_unresolved_notes_assertive_terminal():
    # unresolved_notes as a bare string must not iterate per-character and bypass.
    analysis = _one_issue(
        [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
          "verbatim_quote": "used a banned macro"}])
    analysis["unresolved_notes"] = "The winner is Team Alpha."
    assert "assertive" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_citation_id_reserved_token_terminal():
    # a citation named after a reserved masking token would make its refs unverifiable.
    analysis = _one_issue([
        {"citation_id": "PII_1", "source_type": "accuser", "source_ref": "accuser",
         "verbatim_quote": "used a banned macro"},
    ])
    assert "reserved masking token" in provenance.verify_with_sources(analysis, _sources(), {})["terminal"]


def test_unhashable_citation_id_is_terminal_not_crash():
    # a malformed (unhashable) citation_id must be a clean terminal reject, not a
    # TypeError crashing the cite_ids set build.
    analysis = _one_issue([
        {"citation_id": [], "source_type": "accuser", "source_ref": "accuser",
         "verbatim_quote": "used a banned macro"},
    ])
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is not None
    assert "citation_id" in out["terminal"]


def test_clause_source_ref_unhashable_is_terminal_not_crash():
    # a clause citation whose source_ref is a list (unhashable) must be a clean
    # terminal reject, not a TypeError crashing the by_clause_req tuple-key lookup.
    analysis = _one_issue([
        {"citation_id": "c1", "source_type": "clause", "source_ref": [],
         "requirement_id": "R-INPUT-01a", "verbatim_quote": "hardware or software macros"},
    ])
    out = provenance.verify_with_sources(analysis, _sources(), {})
    assert out["terminal"] is not None
    assert "clause source_ref" in out["terminal"]


# ── F2: output-key ALLOWLIST at the gate (not denylist-only) ─────────────────

def test_gate_check_rejects_non_allowlisted_top_level_key():
    # A never-listed top-level key (e.g. a free-text "recommendation") must be
    # rejected by the allowlist even though it is not on the prohibited denylist —
    # otherwise it rides unguarded assertive content to the caller.
    fo = {"formatted_output": {"provenance_verified": True, "issues": [],
                               "recommendation": "The winner is Team Alpha"},
          "status": "success"}
    with pytest.raises(provenance.ProvenanceError, match="non-allowlisted"):
        provenance.gate_check(fo, {})


def test_gate_check_rejects_non_allowlisted_issue_key():
    fo = {"formatted_output": {"provenance_verified": True,
                               "issues": [{"issue_id": "i1", "issue_summary": "ok",
                                           "citations": [], "note": "Bravo cheated"}]},
          "status": "success"}
    with pytest.raises(provenance.ProvenanceError, match="non-allowlisted"):
        provenance.gate_check(fo, {})


def test_gate_check_rejects_non_allowlisted_citation_key():
    fo = {"formatted_output": {"provenance_verified": True, "issues": [{
        "issue_id": "i1", "issue_summary": "ok",
        "citations": [{"citation_id": "c1", "source_type": "accuser", "source_ref": "accuser",
                       "verbatim_quote": "used a banned macro", "metadata": "Bravo loses"}]}]},
          "status": "success"}
    with pytest.raises(provenance.ProvenanceError, match="non-allowlisted"):
        provenance.gate_check(fo, {})


def test_gate_check_allows_escalation_keys():
    # escalation_required (top) and manual_escalation_required (issue) ARE on the
    # allowlist (要配慮 manual-escalation signals produced by post_process).
    fo = {"formatted_output": {"provenance_verified": True, "escalation_required": ["issue_C-X"],
                               "issues": [{"issue_id": "issue_C-X",
                                           "issue_summary": "(withheld pending manual escalation: health)",
                                           "citations": [], "manual_escalation_required": True}]},
          "status": "success"}
    provenance.gate_check(fo, {})  # must NOT raise


def test_gate_check_rejects_unknown_reconciliation_mode():
    fo = {"formatted_output": {"provenance_verified": True, "issues": [{
        "issue_id": "issue_C-X", "issue_summary": "neutral", "citations": [],
        "reconciliation_mode": "best_effort",
    }]}, "status": "success"}
    with pytest.raises(provenance.ProvenanceError, match="reconciliation_mode"):
        provenance.gate_check(fo, {})
