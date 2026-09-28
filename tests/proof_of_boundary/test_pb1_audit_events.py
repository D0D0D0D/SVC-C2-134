# PB-1: BaseNode -> AuditLogger boundary.
#
# Each node's execute() must emit at least one DOMAIN audit event. We patch the
# node MODULE's own `emit_trace_event` binding and call execute() directly, so the
# framework auto-events (node_start/complete/error, emitted from base_node's binding)
# are NOT observed here — this closes the "framework events are not proof" gap
# Mutation check: deleting a
# node's domain emit makes its case capture zero events and fail.

import importlib
import json

import pytest

from tests.conftest import DISPUTE, load_config

_DISPUTE_JSON = json.dumps(DISPUTE)
_VALIDATED = {
    "accuser_assertion": DISPUTE["accuser_assertion"],
    "defendant_rebuttal": DISPUTE["defendant_rebuttal"],
    "third_party_facts": DISPUTE["third_party_facts"],
    "rulebook": DISPUTE["rulebook"],
    "dispute_context": DISPUTE["dispute_context"],
}
_CANDIDATES = [{"clause_id": "C-CONDUCT-02",
                "requirements": [{"requirement_id": "R-CONDUCT-02a", "requirement_text": "cooperate", "exceptions": []}]}]

# (module, class, success-path state) — each must emit >=1 domain event in execute().
_CASES = [
    ("src.nodes.pre_process_node", "PreProcessNode",
     {"user_input": _DISPUTE_JSON, "correlation_id": "t", "node_history": [], "error_log": []}),
    ("src.nodes.clause_map_node", "ClauseMapNode",
     {"validated_input": _VALIDATED, "correlation_id": "t", "node_history": [], "error_log": []}),
    ("src.nodes.reconcile_node", "ReconcileNode",
     {"validated_input": _VALIDATED, "candidate_clauses": _CANDIDATES, "correlation_id": "t",
      "node_history": [], "error_log": []}),
    ("src.nodes.issue_draft_node", "IssueDraftNode",
     {"candidate_clauses": _CANDIDATES, "llm_analysis": {"issues": []}, "validated_input": _VALIDATED,
      "correlation_id": "t", "node_history": [], "error_log": []}),
    ("src.nodes.post_process_node", "PostProcessNode",
     {"dispute_analysis": {"issues": [], "unresolved_notes": []}, "accuser_assertion": "a",
      "defendant_rebuttal": "", "third_party_facts": [], "rulebook": {}, "sensitive_flags": [],
      "dispute_context": {"rulebook_version": "1"}, "correlation_id": "t",
      "node_history": [], "error_log": []}),
]


@pytest.mark.parametrize("module_path,cls_name,state", _CASES)
def test_node_emits_domain_event(monkeypatch, module_path, cls_name, state):
    module = importlib.import_module(module_path)
    captured: list[str] = []
    monkeypatch.setattr(module, "emit_trace_event",
                        lambda event_type, _p, _s, _c=captured: _c.append(event_type))
    node = getattr(module, cls_name)(config=load_config())
    node.execute(state)
    assert captured, f"{cls_name}.execute() emitted no domain audit event (unaudited execution)"


def test_post_process_emits_on_terminal_reject(monkeypatch):
    # S-4 must fire on the NON-success paths too (design §S-4 "including reject ...").
    import src.nodes.post_process_node as mod
    captured: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event",
                        lambda event_type, _p, _s, _c=captured: _c.append(event_type))
    node = mod.PostProcessNode(config=load_config())
    # dispute_analysis carrying a prohibited field -> terminal provenance reject
    state = {
        "dispute_analysis": {"issues": [{"issue_id": "i", "verdict": "Bravo loses", "citations": []}],
                             "unresolved_notes": []},
        "accuser_assertion": "a", "defendant_rebuttal": "", "third_party_facts": [],
        "rulebook": {}, "sensitive_flags": [], "dispute_context": {"rulebook_version": "1"},
        "correlation_id": "t", "node_history": [], "error_log": [],
    }
    out = node.execute(state)
    assert out["status"] == "error"
    assert "provenance_rejected" in captured


def test_graphnode_emits_on_upstream_error_passthrough(monkeypatch):
    # The main-slot GraphNode short-circuits an upstream reject and must still audit.
    import src.graph.dispute_workflow_node as mod
    captured: list[str] = []
    monkeypatch.setattr(mod, "emit_trace_event",
                        lambda event_type, _p, _s, _c=captured: _c.append(event_type))
    node = mod.DisputeWorkflowGraphNode(config=load_config())
    out = node.execute({"status": "error", "correlation_id": "t", "node_history": [], "error_log": []})
    assert out == {}
    assert "upstream_error_passthrough" in captured
