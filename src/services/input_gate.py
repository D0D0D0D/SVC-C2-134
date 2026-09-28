"""Input validation gate (S-2 domain layer) — deterministic, fail-closed.

Implements the design's Input contract (docs/02_design.md §Input contract):
recursive per-object key *allowlist* (not denylist), scalar type/shape checks,
required/optional fields, unique ids, version-pin equality, and a free-text
quality gate (control-char / U+FFFD ratio, length). No binary/file channel: the
body must be a JSON object.

`validate()` is a pure function — it never mutates State and never raises; it
returns the list of `{field, reason}` rejections. Determinism: the effective
rulebook is pinned here (version equality) and no wall-clock/random input is read.
"""

from __future__ import annotations

import json
import unicodedata
from typing import Any

# Per-object key allowlists (unknown key at any level → reject).
_TOP_KEYS = {"accuser_assertion", "defendant_rebuttal", "third_party_facts", "rulebook", "dispute_context"}
_CTX_KEYS = {"match_id", "protest_id", "rulebook_version"}
_FACT_KEYS = {"fact_id", "text", "source_ref"}
_RULEBOOK_KEYS = {"version", "clauses"}
_CLAUSE_KEYS = {"clause_id", "requirements"}
_REQUIREMENT_KEYS = {"requirement_id", "requirement_text", "exceptions"}


def parse_body(raw: Any) -> tuple[dict | None, list[dict]]:
    """Decode the request body to a JSON object.

    `raw` may be a JSON string (the invoke `user_input` channel) or an already
    decoded dict. A non-JSON-decodable body or a non-object → reject (the
    parser-less no-binary-channel choice; magic-byte / content-type is not
    trusted). Returns (obj_or_None, rejections).
    """
    if isinstance(raw, dict):
        return raw, []
    if not isinstance(raw, str) or not raw.strip():
        return None, [{"field": "body", "reason": "empty or non-string request body"}]
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return None, [{"field": "body", "reason": "body is not JSON-decodable (no binary/file channel)"}]
    if not isinstance(obj, dict):
        return None, [{"field": "body", "reason": "request body must be a JSON object"}]
    return obj, []


def _bad_char_ratio(text: str) -> float:
    if not text:
        return 0.0
    bad = 0
    for ch in text:
        if ch in ("\t", "\n", "\r"):
            continue
        if ch == "�" or unicodedata.category(ch) == "Cc":
            bad += 1
    return bad / len(text)


def _quality_reject(field: str, text: str, max_bad_ratio: float, max_chars: int) -> list[dict]:
    out: list[dict] = []
    if len(text) > max_chars:
        out.append({"field": field, "reason": f"free-text length {len(text)} > max_field_chars {max_chars}"})
    ratio = _bad_char_ratio(text)
    if ratio >= max_bad_ratio:
        out.append({"field": field, "reason": f"bad-char ratio {ratio:.3f} >= max_bad_char_ratio {max_bad_ratio}"})
    return out


def validate(raw: Any, config: dict) -> tuple[bool, list[dict], dict]:
    """Validate the request body. Returns (ok, rejected, normalized_obj).

    On any rejection ok=False; normalized_obj is the parsed object (may be {} if
    the body itself was undecodable). The caller sets status=error when not ok.
    """
    iq = (config or {}).get("input_quality", {}) or {}
    max_bad = float(iq.get("max_bad_char_ratio", 0.10))
    max_chars = int(iq.get("max_field_chars", 20000))

    obj, rej = parse_body(raw)
    if obj is None:
        return False, rej, {}

    rejected: list[dict] = []

    # ── top-level key allowlist ──────────────────────────────────────────────
    for k in obj:
        if k not in _TOP_KEYS:
            rejected.append({"field": k, "reason": "unknown top-level key"})

    # ── accuser_assertion (required, non-empty str) ──────────────────────────
    acc = obj.get("accuser_assertion")
    if not isinstance(acc, str) or not acc.strip():
        rejected.append({"field": "accuser_assertion", "reason": "required non-empty string"})
    elif isinstance(acc, str):
        rejected += _quality_reject("accuser_assertion", acc, max_bad, max_chars)

    # ── defendant_rebuttal (optional; "" == no rebuttal) ─────────────────────
    if "defendant_rebuttal" in obj:
        dr = obj.get("defendant_rebuttal")
        if not isinstance(dr, str):
            rejected.append({"field": "defendant_rebuttal", "reason": "must be a string ('' == no rebuttal)"})
        elif dr:
            rejected += _quality_reject("defendant_rebuttal", dr, max_bad, max_chars)

    # ── third_party_facts (required list, >=1, unique fact_id) ───────────────
    facts = obj.get("third_party_facts")
    if not isinstance(facts, list) or len(facts) < 1:
        rejected.append({"field": "third_party_facts", "reason": "required list with >=1 element"})
    else:
        seen_fact: set[str] = set()
        for i, f in enumerate(facts):
            if not isinstance(f, dict):
                rejected.append({"field": f"third_party_facts[{i}]", "reason": "must be an object"})
                continue
            for k in f:
                if k not in _FACT_KEYS:
                    rejected.append({"field": f"third_party_facts[{i}].{k}", "reason": "unknown key"})
            fid = f.get("fact_id")
            txt = f.get("text")
            sref = f.get("source_ref")
            if not isinstance(fid, str) or not fid.strip():
                rejected.append({"field": f"third_party_facts[{i}].fact_id", "reason": "required non-empty string"})
            else:
                if fid in seen_fact:
                    rejected.append({"field": f"third_party_facts[{i}].fact_id", "reason": f"duplicate fact_id '{fid}'"})
                seen_fact.add(fid)
            if not isinstance(txt, str) or not txt.strip():
                rejected.append({"field": f"third_party_facts[{i}].text", "reason": "required non-empty string"})
            elif isinstance(txt, str):
                rejected += _quality_reject(f"third_party_facts[{i}].text", txt, max_bad, max_chars)
            if not isinstance(sref, str) or not sref.strip():
                rejected.append({"field": f"third_party_facts[{i}].source_ref", "reason": "required non-empty string"})

    # ── rulebook (required) ──────────────────────────────────────────────────
    rb = obj.get("rulebook")
    rb_version = None
    if not isinstance(rb, dict):
        rejected.append({"field": "rulebook", "reason": "required object"})
    else:
        for k in rb:
            if k not in _RULEBOOK_KEYS:
                rejected.append({"field": f"rulebook.{k}", "reason": "unknown key"})
        rb_version = rb.get("version")
        if not isinstance(rb_version, str) or not rb_version.strip():
            rejected.append({"field": "rulebook.version", "reason": "required non-empty string"})
        clauses = rb.get("clauses")
        if not isinstance(clauses, list) or len(clauses) < 1:
            rejected.append({"field": "rulebook.clauses", "reason": "required list with >=1 element"})
        else:
            seen_req: set[str] = set()
            seen_clause: set[str] = set()
            for ci, cl in enumerate(clauses):
                if not isinstance(cl, dict):
                    rejected.append({"field": f"rulebook.clauses[{ci}]", "reason": "must be an object"})
                    continue
                for k in cl:
                    if k not in _CLAUSE_KEYS:
                        rejected.append({"field": f"rulebook.clauses[{ci}].{k}", "reason": "unknown key"})
                cid = cl.get("clause_id")
                if not isinstance(cid, str) or not cid.strip():
                    rejected.append({"field": f"rulebook.clauses[{ci}].clause_id", "reason": "required non-empty string"})
                else:
                    if cid in seen_clause:
                        rejected.append({"field": f"rulebook.clauses[{ci}].clause_id", "reason": f"duplicate clause_id '{cid}'"})
                    seen_clause.add(cid)
                reqs = cl.get("requirements")
                if reqs is None:
                    # implicit requirement derived (requirement_id == clause_id)
                    if isinstance(cid, str) and cid.strip():
                        if cid in seen_req:
                            rejected.append({"field": f"rulebook.clauses[{ci}]", "reason": f"implicit requirement_id '{cid}' duplicates another requirement_id"})
                        seen_req.add(cid)
                    continue
                if not isinstance(reqs, list) or len(reqs) < 1:
                    rejected.append({"field": f"rulebook.clauses[{ci}].requirements", "reason": "must be a non-empty list when present"})
                    continue
                for ri, rq in enumerate(reqs):
                    if not isinstance(rq, dict):
                        rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}]", "reason": "must be an object"})
                        continue
                    for k in rq:
                        if k not in _REQUIREMENT_KEYS:
                            rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}].{k}", "reason": "unknown key"})
                    rid = rq.get("requirement_id")
                    rtext = rq.get("requirement_text")
                    if not isinstance(rid, str) or not rid.strip():
                        rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}].requirement_id", "reason": "required non-empty string"})
                    else:
                        if rid in seen_req:
                            rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}].requirement_id", "reason": f"duplicate requirement_id '{rid}'"})
                        seen_req.add(rid)
                    if not isinstance(rtext, str) or not rtext.strip():
                        rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}].requirement_text", "reason": "required non-empty string"})
                    elif isinstance(rtext, str):
                        rejected += _quality_reject(f"rulebook.clauses[{ci}].requirements[{ri}].requirement_text", rtext, max_bad, max_chars)
                    exc = rq.get("exceptions")
                    if exc is not None:
                        if not isinstance(exc, list) or not all(isinstance(e, str) for e in exc):
                            rejected.append({"field": f"rulebook.clauses[{ci}].requirements[{ri}].exceptions", "reason": "must be a list of strings"})

    # ── dispute_context (required) + version-pin ─────────────────────────────
    ctx = obj.get("dispute_context")
    if not isinstance(ctx, dict):
        rejected.append({"field": "dispute_context", "reason": "required object"})
    else:
        for k in ctx:
            if k not in _CTX_KEYS:
                rejected.append({"field": f"dispute_context.{k}", "reason": "unknown key"})
        for k in ("match_id", "protest_id", "rulebook_version"):
            v = ctx.get(k)
            if not isinstance(v, str) or not v.strip():
                rejected.append({"field": f"dispute_context.{k}", "reason": "required non-empty string"})
        cv = ctx.get("rulebook_version")
        if isinstance(cv, str) and isinstance(rb_version, str) and cv != rb_version:
            rejected.append({
                "field": "dispute_context.rulebook_version",
                "reason": f"version-pin mismatch: dispute_context '{cv}' != rulebook.version '{rb_version}'",
            })

    return (len(rejected) == 0), rejected, obj
