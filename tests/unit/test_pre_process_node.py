# SVC-C2-134 — outer pre_process node + PII/sensitive services.

import copy
import json

from framework.schemas.agent_status import AgentStatus

from src.nodes.pre_process_node import PreProcessNode
from src.services import pii
from tests.conftest import DISPUTE, load_config


def _node():
    return PreProcessNode(config=load_config())


def _state(payload):
    return {"user_input": payload, "correlation_id": "t", "node_history": [], "error_log": []}


def test_valid_input_builds_validated_input():
    result = _node().execute(_state(json.dumps(DISPUTE)))
    assert result["status"] == AgentStatus.SUCCESS.value
    vi = result["validated_input"]
    assert vi["accuser_assertion"]
    assert len(vi["third_party_facts"]) == 2
    assert result["user_input"] == ""  # raw cleared


def test_empty_input_rejected():
    result = _node().execute(_state(""))
    assert result["status"] == AgentStatus.ERROR.value
    assert result["rejected"]


def test_invalid_input_rejected_in_execute():
    d = copy.deepcopy(DISPUTE)
    d["dispute_context"]["rulebook_version"] = "9.9"  # version-pin mismatch
    result = _node().execute(_state(json.dumps(d)))
    assert result["status"] == AgentStatus.ERROR.value


def test_gate_rejects_present_invalid():
    d = copy.deepcopy(DISPUTE)
    d["evil"] = 1  # unknown top-level key
    state = _node()._extra_security_gate_input(_state(json.dumps(d)))
    assert state["status"] == AgentStatus.ERROR.value
    assert state["rejected"]


def test_gate_lenient_on_absent_input():
    # bare state (no user_input) must NOT be rejected by the gate (keeps PB-6 intact)
    state = _node()._extra_security_gate_input({"correlation_id": "t"})
    assert state.get("status") != AgentStatus.ERROR.value


def test_no_rebuttal_defaults_empty_string():
    d = copy.deepcopy(DISPUTE)
    del d["defendant_rebuttal"]
    result = _node().execute(_state(json.dumps(d)))
    assert result["status"] == AgentStatus.SUCCESS.value
    assert result["validated_input"]["defendant_rebuttal"] == ""


def test_sensitive_health_flag():
    assert "health_condition" in pii.detect_sensitive("The player suffered a concussion mid-match.")


def test_sensitive_harassment_flag():
    assert "harassment_or_abuse" in pii.detect_sensitive("The chat contained targeted harassment.")


def test_sensitive_contact_pii_email_and_phone():
    # A genuine contact channel (email OR a validated phone) is 要配慮 "beyond a
    # player handle". Covered via the shared typed detector (email/phone_jp/phone_us).
    assert "contact_pii" in pii.detect_sensitive("reach me at scout@example.com anytime")
    # NFKC-normalised so a full-width email still matches.
    assert "contact_pii" in pii.detect_sensitive("ｓｃｏｕｔ＠ｅｘａｍｐｌｅ．ｃｏｍ にどうぞ")
    # Review regression (Medium): a phone contact channel must also fire contact_pii
    # (masking the digits is NOT the sensitive-category escalation signal).
    assert "contact_pii" in pii.detect_sensitive("call the manager at 090-1234-5678 to discuss")
    assert "contact_pii" in pii.detect_sensitive("or +81-3-1234-5678")
    # full-width phone matches after NFKC normalisation too
    assert "contact_pii" in pii.detect_sensitive("０９０－１２３４－５６７８ まで")


def test_match_log_numerics_are_not_contact_pii():
    # Match logs are full of numerics; none must be mistaken for contact PII (a false
    # contact_pii flag WITHHOLDS the issue). The shared phone_jp/phone_us patterns are
    # structural (leading 0/+81/+1 + separator groups), so these do not misfire.
    for txt in ("the log timestamp is 2024-08-04 round 3",
                "a 12-input burst within 180 milliseconds at 04:12",
                "final score 16-14 in overtime"):
        assert "contact_pii" not in pii.detect_sensitive(txt)


def test_date_shaped_numerics_are_not_contact_pii():
    # Review regression (Medium): the shared phone_jp pattern matches a leading-zero
    # DD-MM-YYYY date (e.g. "04-12-2024"). A calendar date in a dispute source must
    # NOT be treated as contact PII (false withholding is worse than a missed
    # escalation). Covers -/ separators and YYYY-first order.
    for txt in ("the match was on 04-12-2024 at the venue",
                "incident date 09/06/2024 per the report",
                "scheduled 2024-04-12 for the rematch"):
        assert "contact_pii" not in pii.detect_sensitive(txt)
    # a genuine phone that is NOT date-shaped still flags (guard is narrow)
    assert "contact_pii" in pii.detect_sensitive("call the desk at 03-1234-5678 please")


def test_masked_handle_is_not_contact_pii():
    # The framework default gate masks player/team NAMES (handles) to [MASKED].
    # A masked handle must NOT be treated as contact PII — otherwise every dispute
    # (all name their teams) would be withheld and the agent would return nothing.
    assert pii.detect_sensitive("[MASKED] claims [MASKED] used a banned macro") == []


def test_collect_sensitive_flags_source_level():
    obj = copy.deepcopy(DISPUTE)
    obj["accuser_assertion"] = "Opponent sent me a threat and a slur after the match."
    flags = pii.collect_sensitive_flags(obj)
    assert any(f["source_type"] == "accuser" and f["category"] == "harassment_or_abuse" for f in flags)


def test_sensitive_flags_from_raw_detects_contact_channel_on_raw():
    # D8-bis: contact PII must be detected on the RAW payload (before the framework
    # S-2 gate masks it). sensitive_flags_from_raw parses the invoke payload string
    # and flags the source carrying a genuine email/phone.
    obj = copy.deepcopy(DISPUTE)
    obj["accuser_assertion"] = "Team Alpha; reach us at scout@example.com or 090-1234-5678."
    flags = pii.sensitive_flags_from_raw(json.dumps(obj))
    assert any(f["source_type"] == "accuser" and f["category"] == "contact_pii" for f in flags)
    # a clean dispute (team names only) carries no contact_pii flag
    assert not any(f["category"] == "contact_pii" for f in pii.sensitive_flags_from_raw(json.dumps(DISPUTE)))


def test_sensitive_flags_from_raw_tolerant_of_bad_payload():
    # Best-effort + STR-ONLY: a malformed / non-object / non-str payload yields []
    # (pre_process rejects bad input separately; a non-str user_input is a contract
    # violation that also bypasses framework masking, so it is unsupported here).
    # Never raises on ingress.
    assert pii.sensitive_flags_from_raw("not-json") == []
    assert pii.sensitive_flags_from_raw(json.dumps(["a", "list"])) == []
    assert pii.sensitive_flags_from_raw("") == []
    assert pii.sensitive_flags_from_raw(None) == []
    # a dict (pre-parsed) is NOT accepted — invoke's channel is a JSON str; a dict
    # also dodges the framework str-only masking, so it must not emit divergent flags.
    assert pii.sensitive_flags_from_raw({"accuser_assertion": "reach scout@example.com"}) == []


def test_sensitive_flags_from_raw_never_leaks_pii_in_source_ref():
    # Review regression (High): fact_id is caller-controlled; a PII-bearing fact_id must
    # NOT ride into State via a flag's source_ref. sensitive_flags_from_raw masks
    # source_ref (mirroring the framework masking of the same fact_id in user_input),
    # so no flag value contains the raw email — and the masked ref still matches the
    # downstream (also-masked) citation source_ref.
    obj = copy.deepcopy(DISPUTE)
    obj["third_party_facts"] = [
        {"fact_id": "witness@example.com",
         "text": "The observer reported contact at scout@example.com.",
         "source_ref": "obs"},
    ]
    flags = pii.sensitive_flags_from_raw(json.dumps(obj))
    tp = [f for f in flags if f["source_type"] == "third_party" and f["category"] == "contact_pii"]
    assert tp, "third_party contact PII should still be flagged"
    for f in flags:
        for v in f.values():
            assert "@example.com" not in str(v), f"raw PII leaked into flag: {f}"
    assert tp[0]["source_ref"] == "[MASKED]"  # masked to match the downstream fact_id
