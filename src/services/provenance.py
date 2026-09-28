"""S-3 Provenance — verbatim + requirement-level locator + bidirectional coverage.

Two entry points, mirroring the verify-in-execute / enforce-in-gate split
(docs/02_design.md §Provenance contract):

* `verify_with_sources(analysis, sources, config)` runs in `post_process.execute()`
  (it has the `state` source text) and DECLARES `provenance_verified`. It applies
  the normalise/downgrade/terminal semantics. Harmless non-clause locator drift is
  stripped, an issue with an unresolved prose marker becomes deterministic fallback,
  and false source claims remain terminal.
* `gate_check(result, config)` runs in `_extra_security_gate_output(result)` (no
  `state`) and enforces the DECLARED result on `result` alone — so a state-shape gap
  cannot trick it.
"""

from __future__ import annotations

import copy

from .clause_matcher import expand_requirements
from .output_guard import (
    CITATION_KEYS,
    ISSUE_KEYS,
    OUTPUT_TOP_KEYS,
    assertive_violations,
    effective_prohibited,
    extract_markers,
    find_prohibited_keys,
    is_reserved_token,
    non_allowlisted_keys,
    strip_unregistered_markers,
)
from .reconciliation import FALLBACK_MODE, FALLBACK_SUMMARY

_VALUES = ("supports", "contradicts", "unknown")
_PARTIES = ("accuser", "defendant", "third_party")
_CLAUSE_ONLY_KEYS = ("requirement_id", "source_field", "exception_index")
_RECONCILIATION_MODES = (FALLBACK_MODE,)


class ProvenanceError(Exception):
    """Raised by gate_check when the declared result violates the S-3 contract."""


# ── rulebook indexing ────────────────────────────────────────────────────────

def _index_rulebook(rulebook: dict) -> tuple[dict, dict]:
    """Return (by_clause_req, req_to_clause).

    by_clause_req[(clause_id, requirement_id)] -> requirement dict
    req_to_clause[requirement_id] -> clause_id (first clause owning it)
    """
    by_clause_req: dict[tuple[str, str], dict] = {}
    req_to_clause: dict[str, str] = {}
    for clause in (rulebook.get("clauses") or []):
        if not isinstance(clause, dict):
            continue
        cid = clause.get("clause_id", "")
        for req in expand_requirements(clause):
            rid = req["requirement_id"]
            by_clause_req[(cid, rid)] = req
            req_to_clause.setdefault(rid, cid)
    return by_clause_req, req_to_clause


# ── citation verification (execute-time; needs source text) ──────────────────

def _resolve_and_check_citation(cit: dict, sources: dict, min_quote_len: int,
                                by_clause_req: dict, req_to_clause: dict) -> str | None:
    """Return a terminal reason string, or None if the citation passes."""
    if not isinstance(cit, dict):
        return "citation is not an object"
    st = cit.get("source_type")
    sref = cit.get("source_ref")
    quote = cit.get("verbatim_quote")
    cid_raw = cit.get("citation_id")
    cid = cid_raw if isinstance(cid_raw, str) else "?"
    # citation_id MUST be a non-empty string (schema) — also stops an unhashable
    # value like [] crashing the cite_ids set build with an uncaught TypeError.
    if not isinstance(cid_raw, str) or not cid_raw.strip():
        return "citation: citation_id must be a non-empty string"
    if st not in ("accuser", "defendant", "third_party", "clause"):
        return f"citation {cid}: invalid source_type {st!r}"
    # citation_id must not collide with the reserved masking-token namespace
    # ([MASKED]/[PII_n]) — those are excluded from marker extraction, so a citation
    # named "PII_1" would make its references unverifiable.
    if is_reserved_token(cid_raw):
        return f"citation {cid}: citation_id must not be a reserved masking token"
    if not isinstance(quote, str):
        return f"citation {cid}: verbatim_quote must be a string"

    if st == "clause":
        # source_ref must be a non-empty string clause_id — a list/dict here would
        # crash the by_clause_req.get((sref, rid)) tuple-key lookup with an
        # unhashable-type TypeError instead of returning a clean terminal reject.
        if not isinstance(sref, str) or not sref.strip():
            return f"citation {cid}: clause source_ref must be a non-empty string, got {type(sref).__name__}"
        rid = cit.get("requirement_id")
        if not isinstance(rid, str) or not rid.strip():
            return f"citation {cid}: clause citation missing requirement_id"
        sf = cit.get("source_field")
        if sf is None:
            sf = "requirement_text"
        if sf not in ("requirement_text", "exception"):
            return f"citation {cid}: invalid source_field {sf!r}"
        exc_idx = cit.get("exception_index")
        if sf == "requirement_text":
            if exc_idx is not None:
                return f"citation {cid}: exception_index present with source_field=requirement_text"
        else:  # exception
            if not isinstance(exc_idx, int) or isinstance(exc_idx, bool):
                return f"citation {cid}: exception_index required as int for source_field=exception"
            if exc_idx < 0:
                return f"citation {cid}: negative exception_index {exc_idx}"
        req = by_clause_req.get((sref, rid))
        if req is None:
            owner = req_to_clause.get(rid)
            if owner is not None:
                return f"citation {cid}: requirement_id {rid!r} belongs to clause {owner!r}, not source_ref {sref!r} (mismatched locator)"
            return f"citation {cid}: unknown requirement_id {rid!r} for clause {sref!r}"
        if sf == "requirement_text":
            source_text = req.get("requirement_text", "")
        else:
            exceptions = req.get("exceptions", []) or []
            if exc_idx >= len(exceptions):
                return f"citation {cid}: exception_index {exc_idx} out of range ({len(exceptions)} exceptions)"
            source_text = exceptions[exc_idx]
    else:
        # Normalization removes these from production output. Keeping this check
        # fail-closed protects direct callers that bypass that stage.
        for k in _CLAUSE_ONLY_KEYS:
            if k in cit:
                return f"citation {cid}: non-clause citation carries clause-only key {k!r}"
        if st == "accuser":
            # source_ref MUST be the party tag "accuser" (design Output contract:
            # "party tag (must exist in input)"). Enforcing it also keeps the
            # (source_type, source_ref) key aligned with the source-level 要配慮 flag
            # so a sensitive accuser issue cannot dodge withholding via a bogus ref.
            if sref != "accuser":
                return f"citation {cid}: accuser source_ref must be 'accuser', got {sref!r}"
            source_text = sources.get("accuser_assertion", "")
        elif st == "defendant":
            if sref != "defendant":
                return f"citation {cid}: defendant source_ref must be 'defendant', got {sref!r}"
            source_text = sources.get("defendant_rebuttal", "")
        else:  # third_party
            match = next((f for f in (sources.get("third_party_facts") or [])
                          if isinstance(f, dict) and f.get("fact_id") == sref), None)
            if match is None:
                return f"citation {cid}: third_party source_ref {sref!r} matches no fact_id"
            source_text = match.get("text", "")

    if len(quote) < min_quote_len:
        return f"citation {cid}: verbatim_quote shorter than min_quote_len {min_quote_len}"
    if quote not in (source_text or ""):
        return f"citation {cid}: verbatim_quote is not a substring of its resolved source"
    return None


def _note_pairs(analysis_or_output: dict) -> list[tuple[str, str]]:
    """(path, text) for the top-level LLM-authored `unresolved_notes` free text.

    The design's non-assertion guard covers `unresolved_notes` too (§Non-assertion
    guard `_FREE_TEXT_PATHS`); a guard scoped to per-issue fields alone would leak an
    assertive ruling through this side field (`unresolved_notes=["The winner is …"]`).
    """
    notes = analysis_or_output.get("unresolved_notes")
    if isinstance(notes, str):  # a bare string would otherwise iterate per-character
        return [("unresolved_notes", notes)]
    return [
        (f"unresolved_notes[{i}]", n)
        for i, n in enumerate(notes or [])
        if isinstance(n, str)
    ]


def _free_text_pairs(issue: dict) -> list[tuple[str, str]]:
    """(path, text) for every free-text field of an issue (guard + marker check)."""
    pairs: list[tuple[str, str]] = []
    iid = issue.get("issue_id") or issue.get("clause_id", "?")
    if isinstance(issue.get("issue_summary"), str):
        pairs.append((f"{iid}.issue_summary", issue["issue_summary"]))
    es = issue.get("evidence_sufficiency", {})
    if isinstance(es, dict):
        for i, g in enumerate(es.get("gaps", []) or []):
            if isinstance(g, str):
                pairs.append((f"{iid}.evidence_sufficiency.gaps[{i}]", g))
    for i, q in enumerate(issue.get("referee_questions", []) or []):
        if isinstance(q, str):
            pairs.append((f"{iid}.referee_questions[{i}]", q))
    return pairs


def normalize_before_verification(analysis: dict) -> tuple[dict, set[int], int]:
    """Normalize known LLM format drift before provenance verification.

    Returns a deep-copied analysis, the indexes of issues that require deterministic
    fallback, and the number of clause-only keys removed from non-clause citations.
    The separate issue-index set lets verification authenticate every supplied
    citation before replacing a degraded issue, so fallback cannot hide a false quote.
    """
    normalized = copy.deepcopy(analysis)
    fallback_indexes: set[int] = set()
    stripped_keys = 0

    for index, issue in enumerate(normalized.get("issues") or []):
        if not isinstance(issue, dict):
            continue
        citations = issue.get("citations") or []
        for citation in citations:
            if not isinstance(citation, dict) or citation.get("source_type") == "clause":
                continue
            for key in _CLAUSE_ONLY_KEYS:
                if key in citation:
                    citation.pop(key)
                    stripped_keys += 1

        citation_ids: set[str] = set()
        for citation in citations:
            citation_id = citation.get("citation_id") if isinstance(citation, dict) else None
            if isinstance(citation_id, str):
                citation_ids.add(citation_id)
        marker_removed = False

        summary = issue.get("issue_summary")
        if isinstance(summary, str):
            issue["issue_summary"], removed = strip_unregistered_markers(summary, citation_ids)
            marker_removed |= removed

        sufficiency = issue.get("evidence_sufficiency")
        if isinstance(sufficiency, dict):
            gaps = []
            for gap in sufficiency.get("gaps", []) or []:
                if isinstance(gap, str):
                    gap, removed = strip_unregistered_markers(gap, citation_ids)
                    marker_removed |= removed
                gaps.append(gap)
            sufficiency["gaps"] = gaps

        questions = []
        for question in issue.get("referee_questions", []) or []:
            if isinstance(question, str):
                question, removed = strip_unregistered_markers(question, citation_ids)
                marker_removed |= removed
            questions.append(question)
        issue["referee_questions"] = questions

        if marker_removed:
            fallback_indexes.add(index)

    return normalized, fallback_indexes, stripped_keys


def _fallback_issue(issue: dict) -> dict:
    """Return the same all-unknown issue shape as reconciliation.fallback_analysis."""
    fallback = dict(issue)
    fallback["issue_summary"] = FALLBACK_SUMMARY
    fallback["three_way"] = [
        {
            "requirement_id": row.get("requirement_id"),
            **{
                party: {"value": "unknown", "citation_ids": []}
                for party in _PARTIES
            },
        }
        for row in (issue.get("three_way") or [])
        if isinstance(row, dict)
    ]
    fallback["candidate_rule_applicability"] = [
        {
            "clause_id": record.get("clause_id"),
            "requirement_id": record.get("requirement_id"),
            "coverage": "unknown",
            "citation_ids": [],
        }
        for record in (issue.get("candidate_rule_applicability") or [])
        if isinstance(record, dict)
    ]
    fallback["evidence_sufficiency"] = {"status": "unknown", "gaps": []}
    fallback["referee_questions"] = []
    fallback["citations"] = []
    fallback["reconciliation_mode"] = FALLBACK_MODE
    return fallback


def verify_with_sources(analysis: dict, sources: dict, config: dict) -> dict:
    """Full provenance verification. Returns:

        {"terminal": <reason or None>, "issues": [...], "downgrades": int,
         "issue_downgrades": int, "normalizations": int}

    When terminal is not None the caller MUST set status=error /
    provenance_verified=false and return NO sanitised output.
    """
    analysis, fallback_indexes, stripped_keys = normalize_before_verification(analysis)
    prov = (config or {}).get("provenance", {}) or {}
    min_quote_len = int(prov.get("min_quote_len", 12))
    require_cite = bool(prov.get("require_citation_for_supports_contradicts", True))
    rulebook = sources.get("rulebook", {}) or {}
    by_clause_req, req_to_clause = _index_rulebook(rulebook)
    prohibited = effective_prohibited(config)

    # structural adjudication-boundary: prohibited field anywhere → terminal
    bad = find_prohibited_keys(analysis, prohibited)
    if bad:
        return {"terminal": f"prohibited output field(s): {sorted(set(bad))}", "issues": [], "downgrades": 0}

    # non-assertion guard on the top-level unresolved_notes (side free-text path)
    note_av = assertive_violations(_note_pairs(analysis))
    if note_av:
        return {"terminal": f"assertive ruling wording in {note_av[0]['path']}", "issues": [], "downgrades": 0}

    verified_issues: list[dict] = []
    downgrades = 0
    for issue_index, issue in enumerate(analysis.get("issues") or []):
        if not isinstance(issue, dict):
            return {"terminal": "issue is not an object", "issues": [], "downgrades": 0}
        cites = issue.get("citations") or []
        # only str citation_ids (a non-str/unhashable id is rejected in the resolve loop)
        cite_ids = {c.get("citation_id") for c in cites
                    if isinstance(c, dict) and isinstance(c.get("citation_id"), str)}

        # (1) every citation resolves + verbatim substring
        for c in cites:
            reason = _resolve_and_check_citation(c, sources, min_quote_len, by_clause_req, req_to_clause)
            if reason:
                return {"terminal": reason, "issues": [], "downgrades": 0}

        # (2) claims→source: normalization already removed unresolved prose markers;
        # structured citation_ids still fail closed because they claim a source link.
        for path, text in _free_text_pairs(issue):
            for m in extract_markers(text):
                if m not in cite_ids:
                    return {"terminal": f"dangling citation marker [{m}] in {path}", "issues": [], "downgrades": 0}
        # assertive-lexicon net (terminal)
        av = assertive_violations(_free_text_pairs(issue))
        if av:
            return {"terminal": f"assertive ruling wording: {av[0]}", "issues": [], "downgrades": 0}

        new_three_way = []
        for cell in (issue.get("three_way") or []):
            if not isinstance(cell, dict):
                return {"terminal": "three_way row is not an object", "issues": [], "downgrades": 0}
            row = {"requirement_id": cell.get("requirement_id")}
            for party in _PARTIES:
                pc = cell.get(party) or {}
                value = pc.get("value")
                ids = list(pc.get("citation_ids") or [])
                for cid in ids:
                    if cid not in cite_ids:
                        return {"terminal": f"dangling three_way citation_id {cid!r}", "issues": [], "downgrades": 0}
                # (3) coverage: uncited supports/contradicts → downgrade to unknown
                if value in ("supports", "contradicts") and not ids and require_cite:
                    value = "unknown"
                    downgrades += 1
                if value not in _VALUES:
                    value = "unknown"
                row[party] = {"value": value, "citation_ids": ids}
            new_three_way.append(row)

        for rec in (issue.get("candidate_rule_applicability") or []):
            if isinstance(rec, dict):
                for cid in (rec.get("citation_ids") or []):
                    if cid not in cite_ids:
                        return {"terminal": f"dangling applicability citation_id {cid!r}", "issues": [], "downgrades": 0}

        v_issue = _fallback_issue(issue) if issue_index in fallback_indexes else dict(issue)
        if issue_index not in fallback_indexes:
            v_issue["three_way"] = new_three_way
        verified_issues.append(v_issue)

    return {
        "terminal": None,
        "issues": verified_issues,
        "downgrades": downgrades,
        "issue_downgrades": len(fallback_indexes),
        "normalizations": stripped_keys,
    }


# ── gate-time re-check (result only; NO state) ───────────────────────────────

def gate_check(result: dict, config: dict) -> None:
    """Enforce the DECLARED result on `result` alone. Raise ProvenanceError on any
    violation. Cannot do substring resolution (no source) — that ran in execute()
    and is folded into provenance_verified, which this re-asserts."""
    # A terminal reject already declared status=error (and provenance_verified=false);
    # do not re-raise on it — the error outcome is already set.
    if result.get("status") == "error":
        return
    fo = result.get("formatted_output")
    if not isinstance(fo, dict):
        # fail-closed: a non-error (success) result MUST carry a verified
        # formatted_output dict. Early-returning here would let a success with no
        # declared/verified output slip through the enforce-in-gate half.
        raise ProvenanceError("successful result carries no formatted_output dict")
    prov = (config or {}).get("provenance", {}) or {}
    min_quote_len = int(prov.get("min_quote_len", 12))
    prohibited = effective_prohibited(config)

    if fo.get("provenance_verified") is not True:
        raise ProvenanceError("formatted_output.provenance_verified is not True")

    # output-key ALLOWLIST (primary structural gate): reject any top-level key the
    # schema does not name. A denylist alone (find_prohibited_keys below) lets a
    # never-listed key (e.g. "recommendation": "The winner is …") ride unguarded
    # assertive content to the caller — the free-text scan only walks named paths.
    top_extra = non_allowlisted_keys(fo, OUTPUT_TOP_KEYS)
    if top_extra:
        raise ProvenanceError(f"formatted_output carries non-allowlisted key(s): {sorted(top_extra)}")

    bad = find_prohibited_keys(fo, prohibited)
    if bad:
        raise ProvenanceError(f"prohibited output field(s) in result: {sorted(set(bad))}")

    # non-assertion guard on top-level unresolved_notes (side free-text path)
    note_av = assertive_violations(_note_pairs(fo))
    if note_av:
        raise ProvenanceError(f"assertive ruling wording in {note_av[0]['path']}")

    for issue in (fo.get("issues") or []):
        if not isinstance(issue, dict):
            raise ProvenanceError("issue is not an object")
        issue_extra = non_allowlisted_keys(issue, ISSUE_KEYS)
        if issue_extra:
            raise ProvenanceError(f"issue carries non-allowlisted key(s): {sorted(issue_extra)}")
        mode = issue.get("reconciliation_mode")
        if mode is not None and mode not in _RECONCILIATION_MODES:
            raise ProvenanceError(f"invalid reconciliation_mode {mode!r}")
        cite_ids = {c.get("citation_id") for c in (issue.get("citations") or [])
                    if isinstance(c, dict) and isinstance(c.get("citation_id"), str)}
        for c in (issue.get("citations") or []):
            if not isinstance(c, dict):
                raise ProvenanceError("citation is not an object")
            cit_extra = non_allowlisted_keys(c, CITATION_KEYS)
            if cit_extra:
                raise ProvenanceError(f"citation carries non-allowlisted key(s): {sorted(cit_extra)}")
            cid_raw = c.get("citation_id")
            if not isinstance(cid_raw, str) or not cid_raw.strip():
                raise ProvenanceError("citation_id must be a non-empty string")
            if is_reserved_token(cid_raw):
                raise ProvenanceError("citation_id must not be a reserved masking token")
            q = c.get("verbatim_quote")
            if not isinstance(q, str) or len(q) < min_quote_len:
                raise ProvenanceError(f"verbatim_quote below min_quote_len {min_quote_len}")
            if c.get("source_type") == "clause":
                rid = c.get("requirement_id")
                if not isinstance(rid, str) or not rid.strip():
                    raise ProvenanceError("clause citation missing requirement_id")
                sf = c.get("source_field")
                if sf is None:
                    sf = "requirement_text"
                if sf not in ("requirement_text", "exception"):
                    raise ProvenanceError(f"invalid source_field {sf!r}")
                ei = c.get("exception_index")
                if sf == "requirement_text" and ei is not None:
                    raise ProvenanceError("exception_index present with requirement_text")
                if sf == "exception" and (not isinstance(ei, int) or isinstance(ei, bool) or ei < 0):
                    raise ProvenanceError("exception source_field requires non-negative exception_index")
            else:
                for k in _CLAUSE_ONLY_KEYS:
                    if k in c:
                        raise ProvenanceError(f"non-clause citation carries clause-only key {k!r}")
        # dangling markers / structured refs
        for path, text in _free_text_pairs(issue):
            for m in extract_markers(text):
                if m not in cite_ids:
                    raise ProvenanceError(f"dangling citation marker [{m}] in {path}")
        av = assertive_violations(_free_text_pairs(issue))
        if av:
            raise ProvenanceError(f"assertive ruling wording: {av[0]}")
        for cell in (issue.get("three_way") or []):
            if not isinstance(cell, dict):
                raise ProvenanceError("three_way row is not an object")
            for party in _PARTIES:
                pc = cell.get(party) or {}
                for cid in (pc.get("citation_ids") or []):
                    if cid not in cite_ids:
                        raise ProvenanceError(f"dangling three_way citation_id {cid!r}")
        for rec in (issue.get("candidate_rule_applicability") or []):
            if isinstance(rec, dict):
                for cid in (rec.get("citation_ids") or []):
                    if cid not in cite_ids:
                        raise ProvenanceError(f"dangling applicability citation_id {cid!r}")
