# SVC-C2-134 — full outer-graph invoke (GraphNode inner workflow end-to-end).

import copy
import json

import pytest

import cli
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from tests.conftest import (
    DISPUTE,
    OBSERVED_DANGLING_MARKER_ANALYSIS,
    OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS,
    load_config,
)


def _ctx():
    return InvocationContext(session_id="it-1", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="")


def _invoke(payload: str):
    g = Graph(config=load_config())
    g.compile()
    return g.invoke(payload, ctx=_ctx())


def _invoke_marketplace_analysis(monkeypatch, analysis):
    """Invoke through the production Marketplace adapter with an observed drift fixture."""
    from src.services.llm_seam import set_llm

    set_llm(None)
    monkeypatch.setattr(
        cli._AzureMarketplaceLLM,
        "complete",
        lambda self, messages: {
            "content": json.dumps(analysis),
            "tool_calls": [],
            "model": "observed-drift-fixture",
            "usage": {},
        },
    )
    cli._install_marketplace_llm()
    graph = cli.MarketplaceGraph(config=load_config())
    graph.compile()
    try:
        return graph.invoke(json.dumps(DISPUTE), ctx=_ctx())
    finally:
        set_llm(None)


def test_full_invoke_success(fake_llm):
    result = _invoke(json.dumps(DISPUTE))
    assert result["status"] == "success"
    out = result["output"]
    assert out["provenance_verified"] is True
    assert len(out["issues"]) == 2
    # A normal dispute (team NAMES only, no health/harassment/contact PII) must NOT
    # be withheld. The framework default S-2 gate masks the team names to [MASKED];
    # that masked handle must not be mistaken for 要配慮 contact PII (regression guard
    # — otherwise every dispute is withheld and the agent returns nothing usable).
    assert out["escalation_required"] == []
    inp = next(i for i in out["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert inp.get("manual_escalation_required") is not True
    assert not inp["issue_summary"].startswith("(withheld")
    # a cited supports/contradicts cell survived verification
    assert inp["three_way"][0]["accuser"]["value"] == "contradicts"


def test_invoke_deterministic_fallback_without_llm():
    # no LLM injected -> deterministic all-unknown analysis, still non-empty + valid.
    from src.services.llm_seam import set_llm
    set_llm(None)
    result = _invoke(json.dumps(DISPUTE))
    assert result["status"] == "success"
    out = result["output"]
    assert out["provenance_verified"] is True
    assert len(out["issues"]) == 2
    assert all(issue["reconciliation_mode"] == "deterministic_fallback" for issue in out["issues"])


def test_marketplace_observed_fact_locator_drift_is_normalized(monkeypatch):
    result = _invoke_marketplace_analysis(monkeypatch, OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS)
    assert result["status"] == "success"
    issue = next(i for i in result["output"]["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    fact = next(c for c in issue["citations"] if c["citation_id"] == "c2")
    assert "requirement_id" not in fact
    assert issue.get("reconciliation_mode") is None


def test_marketplace_observed_dangling_marker_downgrades_issue(monkeypatch):
    result = _invoke_marketplace_analysis(monkeypatch, OBSERVED_DANGLING_MARKER_ANALYSIS)
    assert result["status"] == "success"
    issue = next(i for i in result["output"]["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert issue["reconciliation_mode"] == "deterministic_fallback"
    assert issue["citations"] == []
    assert all(cell[party]["value"] == "unknown"
               for cell in issue["three_way"]
               for party in ("accuser", "defendant", "third_party"))


@pytest.mark.parametrize(
    "analysis",
    [OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS, OBSERVED_DANGLING_MARKER_ANALYSIS],
    ids=["fact-requirement-id", "dangling-clause-marker"],
)
def test_observed_drift_is_terminal_when_normalization_is_removed(monkeypatch, analysis):
    from src.services import provenance

    monkeypatch.setattr(
        provenance,
        "normalize_before_verification",
        lambda value: (copy.deepcopy(value), set(), 0),
    )
    result = _invoke_marketplace_analysis(monkeypatch, analysis)
    assert result["status"] == "error"
    assert result["output"].get("provenance_verified") is not True


def test_all_marker_downgrades_match_deterministic_fallback(monkeypatch):
    from src.services.llm_seam import set_llm

    analysis = copy.deepcopy(OBSERVED_DANGLING_MARKER_ANALYSIS)
    analysis["issues"][1]["issue_summary"] += " [clause]"
    degraded = _invoke_marketplace_analysis(monkeypatch, analysis)
    set_llm(None)
    fallback = _invoke(json.dumps(DISPUTE))
    assert degraded["status"] == fallback["status"] == "success"
    assert degraded["output"] == fallback["output"]


def test_invalid_input_rejected(fake_llm):
    bad = copy.deepcopy(DISPUTE)
    bad["dispute_context"]["rulebook_version"] = "9.9"  # version-pin mismatch
    result = _invoke(json.dumps(bad))
    assert result["status"] == "error"


def test_sensitive_issue_withheld_but_request_succeeds(dispute, fake_llm):
    # A 要配慮 term in the accuser source flags the issue that cites the accuser.
    # That issue's free text is WITHHELD (manual escalation) but the request still
    # succeeds and the non-sensitive issue is returned — and the parenthesised
    # placeholder must NOT trip the S-3 citation-marker gate.
    dispute["accuser_assertion"] = (
        "Team Alpha claims Team Bravo used a banned macro and also sent a "
        "threatening slur to our player during the match."
    )
    result = _invoke(json.dumps(dispute))
    assert result["status"] == "success"  # gate did not reject the withheld placeholder
    out = result["output"]
    assert out["provenance_verified"] is True
    assert "issue_C-INPUT-01" in out["escalation_required"]
    withheld = next(i for i in out["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert withheld.get("manual_escalation_required") is True
    assert withheld["issue_summary"].startswith("(withheld")
    # the non-sensitive issue is still returned intact
    kept = next(i for i in out["issues"] if i["issue_id"] == "issue_C-CONDUCT-02")
    assert kept.get("manual_escalation_required") is not True


def test_phone_contact_pii_withholds_issue(dispute, fake_llm):
    # Review regression (Medium): a phone number in the accuser source is 要配慮
    # contact PII "beyond a handle". The issue citing the accuser must be WITHHELD
    # and escalated — even though the framework masks the digits, masking is not the
    # sensitive-category signal. (Companion of the harassment-term test above.)
    dispute["accuser_assertion"] = (
        "Team Alpha claims Team Bravo used a banned macro; reach our manager at "
        "090-1234-5678 to discuss the protest."
    )
    result = _invoke(json.dumps(dispute))
    assert result["status"] == "success"
    out = result["output"]
    assert out["provenance_verified"] is True
    assert "issue_C-INPUT-01" in out["escalation_required"]
    withheld = next(i for i in out["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert withheld.get("manual_escalation_required") is True
    assert withheld["issue_summary"].startswith("(withheld")
    # a non-sensitive issue is still returned intact
    kept = next(i for i in out["issues"] if i["issue_id"] == "issue_C-CONDUCT-02")
    assert kept.get("manual_escalation_required") is not True


def test_email_contact_pii_withholds_issue(dispute, fake_llm):
    # D8-bis regression: an EMAIL in the accuser source is 要配慮 contact PII. This is
    # the path the original D8 claimed to cover but never exercised end-to-end — the
    # framework S-2 gate masks the email to [MASKED] before pre_process, so the flag
    # can only be raised on the raw payload at the invoke boundary. The citing issue
    # must be WITHHELD + escalated.
    dispute["accuser_assertion"] = (
        "Team Alpha claims Team Bravo used a banned macro; contact us at "
        "scout@example.com about the protest."
    )
    result = _invoke(json.dumps(dispute))
    assert result["status"] == "success"
    out = result["output"]
    assert out["provenance_verified"] is True
    assert "issue_C-INPUT-01" in out["escalation_required"]
    withheld = next(i for i in out["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert withheld.get("manual_escalation_required") is True
    assert withheld["issue_summary"].startswith("(withheld")


def test_caller_supplied_input_context_cannot_spoof_or_leak(fake_llm):
    # A direct caller must NOT be able to (a) force/suppress escalation via a forged
    # pre-mask flag, nor (b) sneak PII into state via input_context. Graph.invoke drops
    # ALL caller-supplied input_context keys and sets only the flags it computes from
    # the raw payload. A clean dispute + a forged flag + a PII note must still return
    # escalation_required == [] (forged flag discarded; PII note never enters state).
    from src.services import pii
    g = Graph(config=load_config())
    g.compile()
    forged = {"source_type": "accuser", "source_ref": "accuser", "category": "contact_pii"}
    result = g.invoke(
        json.dumps(DISPUTE),
        ctx=_ctx(),
        input_context={pii.PREMASK_FLAGS_INPUT_CONTEXT_KEY: [forged],
                       "note": "scout@example.com"},
    )
    assert result["status"] == "success"
    assert result["output"]["escalation_required"] == []


def test_third_party_contact_pii_withholds_cited_issue(dispute, fake_llm):
    # D8-bis third-party path: a contact channel in a THIRD-PARTY fact's text (cited by
    # issue_C-INPUT-01 via c2/F1) must withhold that issue. The clause quote stays a
    # verbatim substring so provenance still passes; the fact_id ("F1") is clean, so the
    # flag's masked source_ref matches the (also-masked) citation source_ref.
    dispute["third_party_facts"][0]["text"] = (
        "The match log shows a 12-input burst within 180 milliseconds at round 3 "
        "timestamp 04:12. Contact scout@example.com for the raw capture."
    )
    result = _invoke(json.dumps(dispute))
    assert result["status"] == "success"
    out = result["output"]
    assert "issue_C-INPUT-01" in out["escalation_required"]
    withheld = next(i for i in out["issues"] if i["issue_id"] == "issue_C-INPUT-01")
    assert withheld.get("manual_escalation_required") is True


def test_cross_requirement_mismatch_terminal_status(dispute):
    # A FakeLLM whose clause quote is verbatim in R-INPUT-01b but names R-INPUT-01a
    # must produce a TERMINAL reject: final status=error, not a sanitised success.
    from tests.conftest import FakeLLM
    from src.services.llm_seam import set_llm
    payload = {
        "issues": [{
            "clause_id": "C-INPUT-01",
            "issue_summary": "contested [c1]",
            "three_way": [{"requirement_id": "R-INPUT-01a",
                           "accuser": {"value": "unknown", "citation_ids": []},
                           "defendant": {"value": "unknown", "citation_ids": []},
                           "third_party": {"value": "unknown", "citation_ids": []}}],
            "candidate_rule_applicability": [],
            "evidence_sufficiency": {"status": "unknown", "gaps": []},
            "referee_questions": [],
            "citations": [{"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
                           "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
                           "verbatim_quote": "Manual input sequences performed by the player"}],
        }],
        "unresolved_notes": [],
    }
    set_llm(FakeLLM(payload))
    try:
        result = _invoke(json.dumps(dispute))
    finally:
        set_llm(None)
    assert result["status"] == "error"
    assert result["output"].get("provenance_verified") is not True
