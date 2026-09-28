"""Prompt and deterministic-fallback regressions for reconciliation."""

from src.services import reconciliation


def test_prompt_has_type_specific_citation_schema_and_forbids_placeholders():
    prompt = reconciliation.build_prompt(
        {"rulebook": {"version": "2026.1"}},
        [{"clause_id": "C-1", "requirements": [{
            "requirement_id": "R-1", "requirement_text": "A sufficiently long requirement.",
        }]}],
        12,
    )
    assert "CITATION OBJECT SCHEMA BY source_type" in prompt
    assert 'third_party: citation_id, source_type="third_party", source_ref=<fact_id>, verbatim_quote' in prompt
    assert "requirement_id, source_field, and exception_index are forbidden" in prompt
    assert "Never leave placeholders such as [clause]" in prompt


def test_fallback_analysis_marks_closed_mode():
    analysis = reconciliation.fallback_analysis([
        {"clause_id": "C-1", "requirements": [{"requirement_id": "R-1"}]},
    ])
    assert analysis["issues"][0]["reconciliation_mode"] == "deterministic_fallback"
