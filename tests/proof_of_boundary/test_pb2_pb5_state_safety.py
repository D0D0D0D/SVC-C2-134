# PB-2 / PB-5: State serialization + checkpoint safety.
#
# PB-2: post-invoke output is msgpack-safe primitives only.
# PB-5: no raw request blob and no `pii_map` in the (deserialised) checkpoint;
#       production runs checkpointer-less (memory_enabled/hitl false), so nothing
#       is persisted at rest — the InMemorySaver here proves that even under
#       checkpointing the at-rest latest state is clean.
# Guidance: deserialise the checkpoint (never repr()/byte-scan a msgpack blob) and
# guard against a vacuous (empty) checkpoint.

import json

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel

from src.graph.graph import Graph
from src.services import pii
from tests.conftest import DISPUTE, load_config

_PRIMITIVES = (str, int, float, bool, type(None))


def _all_primitives(obj) -> bool:
    if isinstance(obj, dict):
        return all(isinstance(k, str) and _all_primitives(v) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_all_primitives(x) for x in obj)
    return isinstance(obj, _PRIMITIVES)


def _ctx():
    return InvocationContext(session_id="pb5", caller_trust_level=TrustLevel.VERIFIED_EXTERNAL, caller_id="")


def test_pb2_result_is_primitive_only(fake_llm):
    g = Graph(config=load_config())
    g.compile()
    result = g.invoke(json.dumps(DISPUTE), ctx=_ctx())
    assert _all_primitives(result["output"]), "post-invoke output contains a non-primitive value"
    # msgpack round-trip (checkpoint serialisation) must succeed
    json.dumps(result["output"])  # JSON-serialisable is a strict subset of msgpack-safe


def test_pb5_production_has_no_checkpointer():
    # memory_enabled=false / hitl.enabled=false -> nothing persisted at rest.
    g = Graph(config=load_config())
    g.compile()
    assert getattr(g, "_checkpointer", None) is None


def test_pb5_pii_map_never_written_to_state(fake_llm):
    g = Graph(config=load_config())
    g.compile()
    result = g.invoke(json.dumps(DISPUTE), ctx=_ctx())
    serialized = json.dumps(result, default=str)
    assert "pii_map" not in serialized


def test_pb5_checkpoint_latest_is_clean(fake_llm):
    try:
        from langgraph.checkpoint.memory import InMemorySaver as Saver
    except ImportError:  # older langgraph
        from langgraph.checkpoint.memory import MemorySaver as Saver

    saver = Saver()
    # The framework only threads a checkpointer (passes a thread_id in configurable)
    # when memory/hitl is enabled; production is checkpointer-less. Enable memory here
    # ONLY so invoke() supplies the thread_id — the node logic is unchanged, so the
    # at-rest cleanliness this asserts still reflects the production path.
    g = Graph(config={**load_config(), "memory_enabled": True})
    g.compile(checkpointer=saver)
    g.invoke(json.dumps(DISPUTE), ctx=_ctx())

    tuples = list(saver.list(None))
    assert tuples, "no checkpoint was persisted — assertion would be vacuous"
    latest = tuples[0].checkpoint
    channel_values = dict(latest.get("channel_values", {}))
    assert channel_values, "checkpoint channel_values empty — vacuous"
    # deserialised inspection (not repr/byte-scan)
    assert "pii_map" not in channel_values
    assert channel_values.get("user_input", "") == ""  # raw request blob cleared by pre_process


def test_pb5_pii_map_absent_from_every_checkpoint(fake_llm):
    # Pre-push review: PB-5 must not inspect only the latest checkpoint. `pii_map`
    # (token->original) is built and consumed in pre_process LOCAL scope and is
    # never a State field, so it must be absent from EVERY persisted snapshot
    # (including the pre-parse initialize snapshot), not just the final one.
    #
    # Honest caveat: the raw invoke payload transits `user_input` at ingress (the
    # framework's only input channel) until pre_process parses and clears it, so the
    # pre-parse snapshot does carry the raw bundle under a checkpointer — which is
    # exactly why production compiles checkpointer-less (memory_enabled/hitl false,
    # asserted by test_pb5_production_has_no_checkpointer). pii_map, by contrast, is
    # never persisted anywhere.
    try:
        from langgraph.checkpoint.memory import InMemorySaver as Saver
    except ImportError:
        from langgraph.checkpoint.memory import MemorySaver as Saver

    saver = Saver()
    g = Graph(config={**load_config(), "memory_enabled": True})
    g.compile(checkpointer=saver)
    g.invoke(json.dumps(DISPUTE), ctx=_ctx())

    tuples = list(saver.list(None))
    assert tuples, "no checkpoint was persisted — assertion would be vacuous"
    for t in tuples:
        cv = dict(t.checkpoint.get("channel_values", {}))
        # serialise the whole channel tree so a pii_map nested inside any channel
        # (e.g. validated_input) is caught, not only a top-level channel key.
        assert "pii_map" not in json.dumps(cv, default=str), "pii_map leaked into a persisted checkpoint"


def test_idisation_removes_pii_original_and_isolates_map():
    # Mechanism check independent of the detector's coverage: whatever detect_pii
    # matches is replaced by a token, the original is gone from the ID-ised text,
    # and pii_map is a SEPARATE return (never embedded in the ID-ised structure).
    obj = {"accuser_assertion": "contact me at scout@example.com now",
           "defendant_rebuttal": "", "third_party_facts": [], "rulebook": {}, "dispute_context": {}}
    idised, pii_map = pii.idise_input(obj)
    for token, original in pii_map.items():
        assert original not in json.dumps(idised)   # original not left in ID-ised text
        assert token in idised["accuser_assertion"]  # token substituted in
    assert "pii_map" not in json.dumps(idised)
