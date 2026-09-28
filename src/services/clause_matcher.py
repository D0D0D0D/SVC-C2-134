"""Deterministic clause-requirement mapping (internal Tool, inner pre_process).

Pure function of the (version-pinned) input + config — no LLM, no wall-clock, no
randomness. Produces the candidate-clause set that fixes the issue structure: the
LLM later fills only values inside this structure, never which clauses/issues
exist (docs/02_design.md §Issue segmentation).
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[0-9a-z]+")
_STOP = frozenset({
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "is",
    "are", "was", "were", "be", "by", "with", "that", "this", "it", "as", "not",
})


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) >= 3 and t not in _STOP}


def expand_requirements(clause: dict) -> list[dict]:
    """Return the clause's requirements, sorted by requirement_id.

    A clause with no explicit `requirements` derives one implicit requirement
    (`requirement_id == clause_id`, empty `requirement_text` — nothing quotable,
    so provenance for it can only ever be `unknown`).
    """
    cid = clause.get("clause_id", "")
    reqs = clause.get("requirements")
    if not isinstance(reqs, list) or not reqs:
        return [{"requirement_id": cid, "requirement_text": "", "exceptions": []}]
    out = []
    for rq in reqs:
        if not isinstance(rq, dict):
            continue
        out.append({
            "requirement_id": rq.get("requirement_id", ""),
            "requirement_text": rq.get("requirement_text", ""),
            "exceptions": list(rq.get("exceptions") or []),
        })
    return sorted(out, key=lambda r: r["requirement_id"])


def _dispute_text(validated_input: dict) -> str:
    parts = [validated_input.get("accuser_assertion", ""), validated_input.get("defendant_rebuttal", "")]
    for f in validated_input.get("third_party_facts", []) or []:
        if isinstance(f, dict):
            parts.append(f.get("text", ""))
    return "\n".join(p for p in parts if isinstance(p, str))


def map_clauses(validated_input: dict, top_k: int, hybrid_search: bool) -> list[dict]:
    """Return the candidate clauses (ascending clause_id), each with expanded
    requirements. Ranking: overlap score desc, tie-break ascending clause_id,
    keep top_k. Ties are broken deterministically so the ranking is reproducible.
    """
    rulebook = validated_input.get("rulebook", {}) or {}
    clauses = [c for c in (rulebook.get("clauses") or []) if isinstance(c, dict) and c.get("clause_id")]
    if not clauses:
        return []

    dispute = _dispute_text(validated_input)
    dispute_tokens = _tokens(dispute)
    dispute_low = dispute.lower()

    scored: list[tuple[int, str, dict]] = []
    for clause in clauses:
        cid = clause["clause_id"]
        reqs = expand_requirements(clause)
        score = 0
        for rq in reqs:
            score += len(_tokens(rq["requirement_text"]) & dispute_tokens)
            for exc in rq.get("exceptions", []):
                score += len(_tokens(exc) & dispute_tokens)
        if hybrid_search:
            # code-like identifiers cited verbatim in the dispute get a strong boost
            if cid.lower() in dispute_low:
                score += 100
            for rq in reqs:
                if rq["requirement_id"] and rq["requirement_id"].lower() in dispute_low:
                    score += 50
        scored.append((score, cid, {"clause_id": cid, "requirements": reqs}))

    # rank: score desc, clause_id asc; take top_k
    scored.sort(key=lambda t: (-t[0], t[1]))
    k = max(1, int(top_k)) if top_k else len(scored)
    selected = [entry for _, _, entry in scored[:k]]
    # emit in ascending clause_id order (issue segmentation is clause_id-ordered)
    return sorted(selected, key=lambda e: e["clause_id"])
