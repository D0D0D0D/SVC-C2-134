"""Runtime config validation (fail-closed at graph compile).

Every accepted key has a type/range and a hardcoded default. `validate_config`
raises `ConfigError` for any out-of-range / wrong-type value; unknown keys are
ignored (docs/02_design.md §Config contract). The `output_blocking.prohibited_fields`
list is additive-only — removing a floor entry has no effect (enforced structurally
in `output_guard.effective_prohibited`, re-asserted here).
"""

from __future__ import annotations

try:  # framework ConfigError if available, else a local fallback
    from framework.errors import ConfigError  # type: ignore
except Exception:  # pragma: no cover - environment-dependent
    class ConfigError(Exception):  # type: ignore
        pass

from .output_guard import PROHIBITED_FLOOR


def _fail(msg: str) -> None:
    raise ConfigError(f"SVC-C2-134 config invalid: {msg}")


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    # bool is a subclass of int — `isinstance(True, int)` is True — so an int-typed
    # config key must exclude bool explicitly (fail-closed wrong-type check).
    return isinstance(v, int) and not isinstance(v, bool)


def _require_mapping(cfg: dict, key: str) -> dict:
    """A present section MUST be a mapping (fail-closed — a list/scalar section is a
    config error, not silently coerced to {} which would skip its range checks)."""
    if key not in cfg:
        return {}
    val = cfg[key]
    if not isinstance(val, dict):
        _fail(f"{key} must be a mapping, got {type(val).__name__}")
    return val


def validate_config(config: dict) -> None:
    """Validate the runtime config; raise ConfigError on any out-of-range value."""
    cfg = config or {}

    llm = _require_mapping(cfg, "llm")
    if "provider" in llm and llm["provider"] not in ("anthropic", "openai", "bedrock"):
        _fail(f"llm.provider must be anthropic|openai|bedrock, got {llm['provider']!r}")
    if "model" in llm and not (isinstance(llm["model"], str) and llm["model"].strip()):
        _fail("llm.model must be a non-empty string")
    if "temperature" in llm and not (_is_num(llm["temperature"]) and 0.0 <= llm["temperature"] <= 1.0):
        _fail("llm.temperature must be a float in [0.0, 1.0]")
    if "max_tokens" in llm and not (_is_int(llm["max_tokens"]) and llm["max_tokens"] >= 1):
        _fail("llm.max_tokens must be an int >= 1")

    if "timeout_s" in cfg and not (_is_int(cfg["timeout_s"]) and cfg["timeout_s"] >= 1):
        _fail("timeout_s must be an int >= 1")

    if "budget_usd" in cfg and cfg["budget_usd"] is not None and not (_is_num(cfg["budget_usd"]) and cfg["budget_usd"] >= 0):
        _fail("budget_usd must be null or a number >= 0")

    cm = _require_mapping(cfg, "clause_match")
    if "top_k" in cm and not (_is_int(cm["top_k"]) and cm["top_k"] >= 1):
        _fail("clause_match.top_k must be an int >= 1")
    if "hybrid_search" in cm and not isinstance(cm["hybrid_search"], bool):
        _fail("clause_match.hybrid_search must be a bool")

    prov = _require_mapping(cfg, "provenance")
    if "min_quote_len" in prov and not (_is_int(prov["min_quote_len"]) and prov["min_quote_len"] >= 1):
        _fail("provenance.min_quote_len must be an int >= 1")
    if "require_citation_for_supports_contradicts" in prov and not isinstance(prov["require_citation_for_supports_contradicts"], bool):
        _fail("provenance.require_citation_for_supports_contradicts must be a bool")

    iq = _require_mapping(cfg, "input_quality")
    if "max_bad_char_ratio" in iq and not (_is_num(iq["max_bad_char_ratio"]) and 0.0 <= iq["max_bad_char_ratio"] <= 1.0):
        _fail("input_quality.max_bad_char_ratio must be a number in [0.0, 1.0]")
    if "max_field_chars" in iq and not (_is_int(iq["max_field_chars"]) and iq["max_field_chars"] >= 1):
        _fail("input_quality.max_field_chars must be an int >= 1")

    ob = _require_mapping(cfg, "output_blocking")
    if "prohibited_fields" in ob:
        pf = ob["prohibited_fields"]
        if not (isinstance(pf, list) and all(isinstance(x, str) for x in pf)):
            _fail("output_blocking.prohibited_fields must be a list of strings")

    if "max_retry" in cfg and not (_is_int(cfg["max_retry"]) and 0 <= cfg["max_retry"] <= 3):
        _fail("max_retry must be an int in [0, 3] (framework backbone ceiling)")

    for boolean_key in ("memory_enabled",):
        if boolean_key in cfg and not isinstance(cfg[boolean_key], bool):
            _fail(f"{boolean_key} must be a bool")
    hitl = _require_mapping(cfg, "hitl")
    if "enabled" in hitl and not isinstance(hitl["enabled"], bool):
        _fail("hitl.enabled must be a bool")


def floor_is_intact() -> tuple[str, ...]:
    """Return the non-removable prohibited floor (for the proving test)."""
    return PROHIBITED_FLOOR
