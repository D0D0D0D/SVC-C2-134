# SVC-C2-134 — deterministic clause-requirement mapping.

import copy

from src.services import clause_matcher
from tests.conftest import DISPUTE


def _vi():
    return {
        "accuser_assertion": DISPUTE["accuser_assertion"],
        "defendant_rebuttal": DISPUTE["defendant_rebuttal"],
        "third_party_facts": DISPUTE["third_party_facts"],
        "rulebook": DISPUTE["rulebook"],
    }


def test_candidates_ascending_clause_id():
    cands = clause_matcher.map_clauses(_vi(), top_k=8, hybrid_search=True)
    ids = [c["clause_id"] for c in cands]
    assert ids == sorted(ids)
    assert set(ids) == {"C-INPUT-01", "C-CONDUCT-02"}


def test_top_k_limits_count():
    cands = clause_matcher.map_clauses(_vi(), top_k=1, hybrid_search=True)
    assert len(cands) == 1


def test_deterministic_repeat():
    a = clause_matcher.map_clauses(_vi(), 8, True)
    b = clause_matcher.map_clauses(_vi(), 8, True)
    assert a == b


def test_tie_break_by_clause_id():
    # two clauses with no overlap -> equal score -> ascending clause_id order
    vi = {"accuser_assertion": "zzz", "defendant_rebuttal": "", "third_party_facts": [],
          "rulebook": {"version": "1", "clauses": [
              {"clause_id": "B", "requirements": [{"requirement_id": "rb", "requirement_text": "qqq"}]},
              {"clause_id": "A", "requirements": [{"requirement_id": "ra", "requirement_text": "www"}]},
          ]}}
    cands = clause_matcher.map_clauses(vi, top_k=1, hybrid_search=False)
    assert cands[0]["clause_id"] == "A"


def test_implicit_requirement_derived():
    clause = {"clause_id": "C-X"}  # no requirements
    reqs = clause_matcher.expand_requirements(clause)
    assert reqs == [{"requirement_id": "C-X", "requirement_text": "", "exceptions": []}]


def test_requirements_sorted_by_id():
    clause = {"clause_id": "C", "requirements": [
        {"requirement_id": "r2", "requirement_text": "b"},
        {"requirement_id": "r1", "requirement_text": "a"},
    ]}
    reqs = clause_matcher.expand_requirements(clause)
    assert [r["requirement_id"] for r in reqs] == ["r1", "r2"]


def test_hybrid_boost_on_clause_id_mention():
    vi = copy.deepcopy(_vi())
    vi["accuser_assertion"] = "The referee must apply clause c-conduct-02 to this dispute."
    cands = clause_matcher.map_clauses(vi, top_k=1, hybrid_search=True)
    assert cands[0]["clause_id"] == "C-CONDUCT-02"
