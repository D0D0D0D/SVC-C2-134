# SVC-C2-134 — input validation gate (allowlist / type / version-pin / quality).

import copy
import json

from src.services import input_gate
from tests.conftest import DISPUTE

CFG = {"input_quality": {"max_bad_char_ratio": 0.10, "max_field_chars": 20000}}


def _mut(**changes):
    d = copy.deepcopy(DISPUTE)
    for k, v in changes.items():
        d[k] = v
    return d


def test_valid_input_ok():
    ok, rejected, _ = input_gate.validate(json.dumps(DISPUTE), CFG)
    assert ok and rejected == []


def test_unknown_top_level_key_rejected():
    d = copy.deepcopy(DISPUTE)
    d["hax"] = 1
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any(r["field"] == "hax" for r in rejected)


def test_missing_accuser_rejected():
    d = copy.deepcopy(DISPUTE)
    del d["accuser_assertion"]
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any(r["field"] == "accuser_assertion" for r in rejected)


def test_non_string_accuser_rejected():
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(accuser_assertion=123)), CFG)
    assert not ok and any(r["field"] == "accuser_assertion" for r in rejected)


def test_empty_defendant_allowed():
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(defendant_rebuttal="")), CFG)
    assert ok and rejected == []


def test_non_string_defendant_rejected():
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(defendant_rebuttal=5)), CFG)
    assert not ok and any(r["field"] == "defendant_rebuttal" for r in rejected)


def test_empty_facts_rejected():
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(third_party_facts=[])), CFG)
    assert not ok and any(r["field"] == "third_party_facts" for r in rejected)


def test_duplicate_fact_id_rejected():
    facts = [
        {"fact_id": "F1", "text": "aaaaaaaaaaaa", "source_ref": "s"},
        {"fact_id": "F1", "text": "bbbbbbbbbbbb", "source_ref": "s"},
    ]
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(third_party_facts=facts)), CFG)
    assert not ok and any("duplicate fact_id" in r["reason"] for r in rejected)


def test_fact_missing_text_rejected():
    facts = [{"fact_id": "F1", "source_ref": "s"}]
    ok, rejected, _ = input_gate.validate(json.dumps(_mut(third_party_facts=facts)), CFG)
    assert not ok


def test_rulebook_missing_version_rejected():
    d = copy.deepcopy(DISPUTE)
    del d["rulebook"]["version"]
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any(r["field"] == "rulebook.version" for r in rejected)


def test_duplicate_requirement_id_rejected():
    d = copy.deepcopy(DISPUTE)
    d["rulebook"]["clauses"][1]["requirements"][0]["requirement_id"] = "R-INPUT-01a"
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any("duplicate requirement_id" in r["reason"] for r in rejected)


def test_version_pin_mismatch_rejected():
    d = copy.deepcopy(DISPUTE)
    d["dispute_context"]["rulebook_version"] = "9.9"
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any("version-pin mismatch" in r["reason"] for r in rejected)


def test_unknown_nested_key_rejected():
    d = copy.deepcopy(DISPUTE)
    d["dispute_context"]["foo"] = "bar"
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any(r["field"] == "dispute_context.foo" for r in rejected)


def test_length_quality_reject():
    d = _mut(accuser_assertion="x" * 20001)
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any("length" in r["reason"] for r in rejected)


def test_bad_char_ratio_reject():
    d = _mut(accuser_assertion="ok " + "\x00" * 50)
    ok, rejected, _ = input_gate.validate(json.dumps(d), CFG)
    assert not ok and any("bad-char ratio" in r["reason"] for r in rejected)


def test_non_json_body_rejected():
    ok, rejected, obj = input_gate.validate("this is not json {", CFG)
    assert not ok and obj == {}


def test_non_object_body_rejected():
    ok, rejected, _ = input_gate.validate(json.dumps([1, 2, 3]), CFG)
    assert not ok
