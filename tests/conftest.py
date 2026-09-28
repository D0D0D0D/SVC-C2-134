# SVC-C2-134 — shared test fixtures.

import copy
import json

import pytest

from shared.services.llm.base_llm import BaseLLM


# A valid dispute bundle. Clause C-INPUT-01 holds TWO requirements (needed for the
# cross-requirement provenance test); C-CONDUCT-02 holds one.
DISPUTE = {
    "accuser_assertion": (
        "Team Alpha claims Team Bravo used a banned macro during round 3 giving "
        "them an unfair timing advantage in the match."
    ),
    "defendant_rebuttal": (
        "Team Bravo states the input was a manual sequence permitted under the "
        "rules and no external tool was used."
    ),
    "third_party_facts": [
        {"fact_id": "F1",
         "text": "The match log shows a 12-input burst within 180 milliseconds at round 3 timestamp 04:12.",
         "source_ref": "matchlog-round3"},
        {"fact_id": "F2",
         "text": "Referee observation notes the client anti-cheat did not flag any external process during the round.",
         "source_ref": "referee-obs"},
    ],
    "rulebook": {
        "version": "2026.1",
        "clauses": [
            {"clause_id": "C-INPUT-01", "requirements": [
                {"requirement_id": "R-INPUT-01a",
                 "requirement_text": "Players must not use hardware or software macros that automate multiple inputs from a single action.",
                 "exceptions": ["Accessibility remapping approved by the tournament in advance."]},
                {"requirement_id": "R-INPUT-01b",
                 "requirement_text": "Manual input sequences performed by the player are permitted regardless of speed."},
            ]},
            {"clause_id": "C-CONDUCT-02", "requirements": [
                {"requirement_id": "R-CONDUCT-02a",
                 "requirement_text": "Teams must cooperate with anti-cheat verification during matches."},
            ]},
        ],
    },
    "dispute_context": {"match_id": "M-99", "protest_id": "P-7", "rulebook_version": "2026.1"},
}


# A provenance-valid analysis whose quotes are exact substrings of DISPUTE sources.
VALID_ANALYSIS = {
    "issues": [
        {"clause_id": "C-INPUT-01",
         "issue_summary": "Whether the burst constitutes a prohibited macro [c1][c2] is contested by the accuser [c3].",
         "three_way": [
             {"requirement_id": "R-INPUT-01a",
              "accuser": {"value": "contradicts", "citation_ids": ["c3"]},
              "defendant": {"value": "unknown", "citation_ids": []},
              "third_party": {"value": "unknown", "citation_ids": ["c2"]}},
             {"requirement_id": "R-INPUT-01b",
              "accuser": {"value": "unknown", "citation_ids": []},
              "defendant": {"value": "unknown", "citation_ids": []},
              "third_party": {"value": "unknown", "citation_ids": []}},
         ],
         "candidate_rule_applicability": [
             {"clause_id": "C-INPUT-01", "requirement_id": "R-INPUT-01a", "coverage": "partial", "citation_ids": ["c1", "c2"]},
             {"clause_id": "C-INPUT-01", "requirement_id": "R-INPUT-01b", "coverage": "unknown", "citation_ids": []},
         ],
         "evidence_sufficiency": {"status": "insufficient",
                                  "gaps": ["No hardware capture to confirm automation vs manual input [c2]"]},
         "referee_questions": ["Can the client telemetry distinguish a macro from manual input for this burst?"],
         "citations": [
             {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
              "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
              "verbatim_quote": "hardware or software macros"},
             {"citation_id": "c2", "source_type": "third_party", "source_ref": "F1",
              "verbatim_quote": "12-input burst within 180 milliseconds"},
             {"citation_id": "c3", "source_type": "accuser", "source_ref": "accuser",
              "verbatim_quote": "used a banned macro"},
         ]},
        {"clause_id": "C-CONDUCT-02",
         "issue_summary": "Cooperation with anti-cheat is documented [c4].",
         "three_way": [
             {"requirement_id": "R-CONDUCT-02a",
              "accuser": {"value": "unknown", "citation_ids": []},
              "defendant": {"value": "unknown", "citation_ids": []},
              "third_party": {"value": "supports", "citation_ids": ["c4"]}},
         ],
         "candidate_rule_applicability": [
             {"clause_id": "C-CONDUCT-02", "requirement_id": "R-CONDUCT-02a", "coverage": "covered", "citation_ids": ["c4"]},
         ],
         "evidence_sufficiency": {"status": "sufficient", "gaps": []},
         "referee_questions": [],
         "citations": [
             {"citation_id": "c4", "source_type": "third_party", "source_ref": "F2",
              "verbatim_quote": "anti-cheat did not flag any external process"},
         ]},
    ],
    "unresolved_notes": [],
}


# The two drift shapes observed in the 2026-09-16 Marketplace/gpt-4.1 runs,
# applied to the existing canonical DISPUTE response fixture. This keeps the
# production BaseLLM response protocol and changes only the observed fields.
OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS = copy.deepcopy(VALID_ANALYSIS)
OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS["issues"][0]["citations"][1]["requirement_id"] = "R-INPUT-01a"

OBSERVED_DANGLING_MARKER_ANALYSIS = copy.deepcopy(VALID_ANALYSIS)
OBSERVED_DANGLING_MARKER_ANALYSIS["issues"][0]["evidence_sufficiency"]["gaps"].append("[clause]")


class FakeLLM(BaseLLM):
    """Deterministic BaseLLM returning a canned analysis (provenance-valid)."""

    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload if payload is not None else VALID_ANALYSIS

    def complete(self, messages: list) -> dict:
        return {"content": json.dumps(self._payload), "tool_calls": [], "model": "fake-test", "usage": {}}

    def stream(self, messages: list):
        yield json.dumps(self._payload)

    def bind_tools(self, tools: list) -> "FakeLLM":
        return self


def load_config() -> dict:
    import pathlib
    import yaml
    p = pathlib.Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@pytest.fixture
def dispute() -> dict:
    return copy.deepcopy(DISPUTE)


@pytest.fixture
def dispute_json(dispute) -> str:
    return json.dumps(dispute)


@pytest.fixture
def config() -> dict:
    return load_config()


@pytest.fixture
def valid_analysis() -> dict:
    return copy.deepcopy(VALID_ANALYSIS)


@pytest.fixture
def observed_fact_locator_drift_analysis() -> dict:
    return copy.deepcopy(OBSERVED_FACT_LOCATOR_DRIFT_ANALYSIS)


@pytest.fixture
def observed_dangling_marker_analysis() -> dict:
    return copy.deepcopy(OBSERVED_DANGLING_MARKER_ANALYSIS)


@pytest.fixture
def fake_llm():
    """Inject FakeLLM into the module-level seam; reset afterwards."""
    from src.services.llm_seam import set_llm
    llm = FakeLLM()
    set_llm(llm)
    yield llm
    set_llm(None)


@pytest.fixture
def agent_config() -> dict:
    """Use config/config.yaml without additional runtime dependencies."""
    return {}


@pytest.fixture
def case_payload() -> str:
    """Exercise the same dispute JSON as the integration happy path."""
    return json.dumps(DISPUTE)


@pytest.fixture
def live_input_context() -> dict:
    """Match the integration happy path, which supplies no input context."""
    return {}


@pytest.fixture
def live_llm_injector():
    """Keep config declarative while connecting the live client to the LLM seam."""
    from src.services.llm_seam import set_llm

    def _inject(agent, llm):
        agent.config["llm"] = load_config()["llm"]
        set_llm(llm)
        return agent

    yield _inject
    set_llm(None)
