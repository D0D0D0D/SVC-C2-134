"""Three-way reconciliation — LLM prompt construction, parsing, and the
deterministic fallback used when no LLM is injected.

The LLM fills only VALUES (three_way values, coverage, sufficiency, free text,
citations) inside the fixed candidate structure; it never decides which clauses,
requirements, or issues exist (docs/02_design.md §Issue segmentation). The prompt
embeds the EXACT output schema and the candidate structure so the model returns a
usable object rather than an empty one (MunicipalContractVariationEvidenceBriefAgent: a schema-less
"respond in the schema" prompt yields `{}` that coerces to an empty brief).
"""

from __future__ import annotations

import json
from typing import Optional

_VALUES = ("supports", "contradicts", "unknown")
FALLBACK_SUMMARY = (
    "Automated reconciliation was not available; all positions are unresolved "
    "and require referee review."
)
FALLBACK_MODE = "deterministic_fallback"


def build_prompt(validated_input: dict, candidate_clauses: list[dict], min_quote_len: int) -> str:
    """Return the reconciliation prompt (system+user folded into one string)."""
    accuser = validated_input.get("accuser_assertion", "")
    defendant = validated_input.get("defendant_rebuttal", "")
    facts = validated_input.get("third_party_facts", []) or []
    rulebook_version = (validated_input.get("rulebook", {}) or {}).get("version", "")

    # candidate structure the model must fill (requirement_ids are fixed inputs)
    structure = [
        {
            "clause_id": c["clause_id"],
            "requirements": [
                {"requirement_id": r["requirement_id"],
                 "requirement_text": r["requirement_text"],
                 "exceptions": r.get("exceptions", [])}
                for r in c["requirements"]
            ],
        }
        for c in candidate_clauses
    ]

    sources = {
        "accuser_assertion": accuser,
        "defendant_rebuttal": defendant or "(no rebuttal submitted)",
        "third_party_facts": [
            {"fact_id": f.get("fact_id", ""), "text": f.get("text", "")}
            for f in facts if isinstance(f, dict)
        ],
    }

    schema_example = {
        "issues": [{
            "clause_id": "<one of the candidate clause_ids>",
            "issue_summary": "Neutral, non-assertive summary of the contested point, with inline [citation_id] markers. Do NOT state a verdict, ruling, penalty, or who wins.",
            "three_way": [{
                "requirement_id": "<requirement_id within this clause>",
                "accuser": {"value": "supports|contradicts|unknown", "citation_ids": ["c1"]},
                "defendant": {"value": "supports|contradicts|unknown", "citation_ids": []},
                "third_party": {"value": "supports|contradicts|unknown", "citation_ids": ["c2"]},
            }],
            "candidate_rule_applicability": [{
                "clause_id": "<clause_id>", "requirement_id": "<requirement_id>",
                "coverage": "covered|partial|unknown", "citation_ids": ["c2"],
            }],
            "evidence_sufficiency": {"status": "sufficient|insufficient|unknown",
                                     "gaps": ["What third-party fact is missing to decide requirement X [c2]"]},
            "referee_questions": ["A concrete question the referee should confirm?"],
            "citations": [{
                "citation_id": "c1", "source_type": "third_party",
                "source_ref": "<fact_id>",
                "verbatim_quote": f"an EXACT substring (>= {min_quote_len} chars) of the named source",
            }],
        }],
        "unresolved_notes": ["Anything you could not map to a clause."],
    }

    rules = f"""You support an esports tournament referee at a hard-to-judge dispute point. You are a
NON-FINAL analysis aid. You MUST NOT decide the result, a penalty, a bracket change, or
eligibility. Never output verdict/ruling/penalty/sanction/bracket_change/eligibility/
disqualification/winner/loser/points_adjustment fields or assertive rulings.

For EACH candidate clause below, produce exactly one issue. For EACH requirement of that
clause produce exactly one three_way row and one candidate_rule_applicability record
(keyed by requirement_id). Classify each party (accuser / defendant / third_party) as:
  - "supports"    the requirement is met per that party's material,
  - "contradicts" that party's material is inconsistent with the requirement,
  - "unknown"     insufficient basis.
Rules that make the output trustworthy:
  - Every "supports"/"contradicts" cell MUST reference at least one citation_id whose quote
    backs it. If you cannot cite a source, use "unknown".
  - When the defendant submitted no rebuttal, the defendant column is "unknown".
  - Every citation's verbatim_quote MUST be an EXACT substring (>= {min_quote_len} chars) of
    the named source. For source_type=="clause" you MUST name requirement_id and, to quote an
    exception, set source_field="exception" and exception_index. Do not invent facts or quotes.
  - Free text (issue_summary, gaps, referee_questions) must stay neutral and cite with inline
    [citation_id] markers. Every bracketed marker MUST be a citation_id registered in that same
    issue. Never leave placeholders such as [clause], [fact], or [citation] in free text.

CITATION OBJECT SCHEMA BY source_type (keys not shown for a type MUST be omitted):
  - accuser: citation_id, source_type="accuser", source_ref="accuser", verbatim_quote
  - defendant: citation_id, source_type="defendant", source_ref="defendant", verbatim_quote
  - third_party: citation_id, source_type="third_party", source_ref=<fact_id>, verbatim_quote
  - clause requirement_text: citation_id, source_type="clause", source_ref=<clause_id>,
    requirement_id, source_field="requirement_text", verbatim_quote
  - clause exception: citation_id, source_type="clause", source_ref=<clause_id>, requirement_id,
    source_field="exception", exception_index, verbatim_quote
  - requirement_id, source_field, and exception_index are forbidden on accuser, defendant, and
    third_party citations.

Return ONLY a JSON object exactly matching this schema (no prose, no code fence):"""

    return (
        rules
        + "\n\nOUTPUT SCHEMA:\n" + json.dumps(schema_example, ensure_ascii=False, indent=2)
        + "\n\nRULEBOOK VERSION: " + rulebook_version
        + "\n\nCANDIDATE CLAUSES (fill values for these — do not add or drop clauses/requirements):\n"
        + json.dumps(structure, ensure_ascii=False, indent=2)
        + "\n\nSOURCES:\n" + json.dumps(sources, ensure_ascii=False, indent=2)
        + "\n\nRespond with the JSON object now:"
    )


def parse_response(text: str) -> Optional[dict]:
    """Extract the JSON object from an LLM response, tolerant of code fences /
    leading prose. Returns None if no object can be parsed."""
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    if s.startswith("```"):
        # strip a ```json ... ``` fence
        s = s.split("```", 2)
        s = s[1] if len(s) > 1 else text
        if s.lstrip().lower().startswith("json"):
            s = s.lstrip()[4:]
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(s[start:end + 1])
    except (ValueError, TypeError):
        return None
    return obj if isinstance(obj, dict) else None


def fallback_analysis(candidate_clauses: list[dict]) -> dict:
    """Deterministic all-`unknown` analysis when no LLM is injected.

    Produces the fixed structure with every party `unknown`, no citations, and no
    asserted free text — a genuinely non-assertive, always-provenance-valid draft.
    """
    issues = []
    for c in candidate_clauses:
        cid = c["clause_id"]
        three_way = []
        applic = []
        for r in c["requirements"]:
            rid = r["requirement_id"]
            three_way.append({
                "requirement_id": rid,
                "accuser": {"value": "unknown", "citation_ids": []},
                "defendant": {"value": "unknown", "citation_ids": []},
                "third_party": {"value": "unknown", "citation_ids": []},
            })
            applic.append({"clause_id": cid, "requirement_id": rid, "coverage": "unknown", "citation_ids": []})
        issues.append({
            "clause_id": cid,
            "issue_summary": FALLBACK_SUMMARY,
            "three_way": three_way,
            "candidate_rule_applicability": applic,
            "evidence_sufficiency": {"status": "unknown", "gaps": []},
            "referee_questions": [],
            "citations": [],
            "reconciliation_mode": FALLBACK_MODE,
        })
    return {"issues": issues, "unresolved_notes": []}
