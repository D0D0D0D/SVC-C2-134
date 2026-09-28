# SVC-C2-134 — config contract: defaults valid, out-of-range rejected at compile,
# prohibited floor non-removable.

import copy

import pytest

from src.services import config_spec, output_guard
from src.services.config_spec import ConfigError
from tests.conftest import load_config


def test_shipped_config_is_valid():
    config_spec.validate_config(load_config())  # must not raise


def test_temperature_out_of_range_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"llm": {"temperature": 1.5}})


def test_min_quote_len_out_of_range_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"provenance": {"min_quote_len": 0}})


def test_top_k_out_of_range_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"clause_match": {"top_k": 0}})


def test_max_retry_out_of_range_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"max_retry": 9})


def test_bad_char_ratio_out_of_range_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"input_quality": {"max_bad_char_ratio": 2.0}})


def test_unknown_key_ignored():
    config_spec.validate_config({"totally_unknown": 123})  # must not raise


def test_non_mapping_section_rejected():
    # fail-closed: a section supplied as a list/scalar must not be silently coerced
    # to {} (which would skip its range checks). Pre-push review finding.
    with pytest.raises(ConfigError):
        config_spec.validate_config({"provenance": []})


def test_llm_model_wrong_type_rejected():
    with pytest.raises(ConfigError):
        config_spec.validate_config({"llm": {"model": 123}})


def test_bool_rejected_for_int_keys():
    # bool is a subclass of int — `True` must NOT satisfy an int-typed config key
    # (fail-closed wrong-type check). Pre-push review finding.
    with pytest.raises(ConfigError):
        config_spec.validate_config({"provenance": {"min_quote_len": True}})
    with pytest.raises(ConfigError):
        config_spec.validate_config({"clause_match": {"top_k": True}})
    with pytest.raises(ConfigError):
        config_spec.validate_config({"max_retry": True})


def test_compile_rejects_out_of_range_config():
    from src.graph.graph import Graph
    bad = copy.deepcopy(load_config())
    bad["provenance"]["min_quote_len"] = -1
    g = Graph(config=bad)
    with pytest.raises(Exception):
        g.compile()


def test_compile_accepts_shipped_config():
    from src.graph.graph import Graph
    g = Graph(config=load_config())
    g.compile()  # must not raise


def test_prohibited_floor_non_removable():
    # config that tries to remove the floor by supplying an empty list has no effect.
    eff = output_guard.effective_prohibited({"output_blocking": {"prohibited_fields": []}})
    for f in output_guard.PROHIBITED_FLOOR:
        assert f in eff


def test_prohibited_floor_additive():
    eff = output_guard.effective_prohibited({"output_blocking": {"prohibited_fields": ["custom_field"]}})
    assert "custom_field" in eff
    assert "verdict" in eff
