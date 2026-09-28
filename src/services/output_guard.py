"""Output guard — structural adjudication-boundary floor + non-assertion lexicon.

The PRIMARY defense is structural: verdict/penalty/bracket/eligibility fields are
excluded from the output-key allowlist and rejected here. The lexicon is the
SECONDARY net (product-safety framing, NOT legal-compliance) — a phrase-based,
NFKC+casefold check for decisive-ruling wording that a neutral question would not
trip (docs/02_design.md §Non-assertion guard).
"""

from __future__ import annotations

import re
import unicodedata

# Non-removable floor (fail-closed): config may only ADD to this set.
PROHIBITED_FLOOR = (
    "verdict", "ruling", "penalty", "sanction", "bracket_change", "eligibility",
    "disqualification", "winner", "loser", "points_adjustment",
)

# Output-key ALLOWLIST (design §Output contract "allowlist keys only"). The
# prohibited floor above is a denylist net; the allowlist is the primary structural
# gate — a top-level key the schema does not name (e.g. a free-text "recommendation")
# would otherwise ride unguarded assertive content to the caller, because the S-3
# free-text scan only walks the NAMED free-text paths, not arbitrary sibling keys.
# `escalation_required` (top) and `manual_escalation_required` (per issue) are the
# 要配慮 manual-escalation signals produced by post_process (design §Input contract
# sensitive-category handling; recorded as DDR D10).
OUTPUT_TOP_KEYS = (
    "rulebook_version", "provenance_verified", "issues", "unresolved_notes",
    "escalation_required",
)
ISSUE_KEYS = (
    "issue_id", "issue_summary", "clause_refs", "three_way",
    "candidate_rule_applicability", "evidence_sufficiency", "referee_questions",
    "citations", "manual_escalation_required", "reconciliation_mode",
)
CITATION_KEYS = (
    "citation_id", "source_type", "source_ref", "requirement_id",
    "source_field", "exception_index", "verbatim_quote",
)


def non_allowlisted_keys(obj: dict, allowed: tuple) -> list[str]:
    """Return the keys of `obj` not present in `allowed` (allowlist violation)."""
    return [k for k in obj if k not in allowed]

# Decisive-ruling phrases (secondary net). Phrase-based so a neutral question
# ("should the player be disqualified?") does not trip on the bare word.
_ASSERTIVE_PHRASES = (
    "is disqualified", "are disqualified", "is guilty", "found guilty",
    "penalty is imposed", "penalty applies", "must forfeit", "forfeits the match",
    "the winner is", "the loser is", "we rule that", "ruling:", "verdict:",
    "sanction imposed", "is penalized", "is penalised", "match is awarded to",
    "違反が確定", "失格とする", "失格になる", "勝者は", "敗者は",
    "処分を科す", "ペナルティを科す", "反則が確定",
)

_MARKER_RE = re.compile(r"\[([^\[\]]+)\]")
# Reserved masking tokens produced upstream — the framework default S-2 gate emits
# [MASKED] and the ID-isation seam emits [PII_n]. These share the [token] shape but
# are NOT citation references; excluding them stops a legitimate summary that
# mentions a masked entity (e.g. "[MASKED] alleges … [c1]") from being rejected as a
# dangling citation marker.
_RESERVED_TOKEN_RE = re.compile(r"^(?:MASKED|PII_\d+)$")


def is_reserved_token(s: str) -> bool:
    """True if `s` is a reserved upstream masking token ([MASKED] / [PII_n])."""
    return isinstance(s, str) and bool(_RESERVED_TOKEN_RE.match(s.strip()))


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").casefold()


def effective_prohibited(config: dict) -> set[str]:
    """Floor merged with config additions (additive only — floor is non-removable)."""
    extra = (config or {}).get("output_blocking", {}) or {}
    fields = extra.get("prohibited_fields") or []
    result = set(PROHIBITED_FLOOR)
    for f in fields:
        if isinstance(f, str):
            result.add(f)
    return result


def find_prohibited_keys(obj, prohibited: set[str]) -> list[str]:
    """Recursively collect any prohibited dict keys at any nesting level."""
    found: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in prohibited:
                    found.append(k)
                walk(v)
        elif isinstance(o, list):
            for item in o:
                walk(item)

    walk(obj)
    return found


def assertive_violations(pairs: list[tuple[str, str]]) -> list[dict]:
    """Return {path, phrase} for each free-text value containing a decisive-ruling phrase."""
    out: list[dict] = []
    norm_phrases = [(_norm(p), p) for p in _ASSERTIVE_PHRASES]
    for path, text in pairs:
        n = _norm(text)
        for np, orig in norm_phrases:
            if np in n:
                out.append({"path": path, "phrase": orig})
    return out


def extract_markers(text: str) -> list[str]:
    """Return inline [citation_id] markers from a free-text string.

    Reserved masking tokens ([MASKED], [PII_n]) are excluded — they are not citation
    references and would otherwise read as dangling markers.
    """
    if not isinstance(text, str):
        return []
    return [m.strip() for m in _MARKER_RE.findall(text)
            if m.strip() and not is_reserved_token(m)]


def strip_unregistered_markers(text: str, citation_ids: set[str]) -> tuple[str, bool]:
    """Remove bracketed markers that do not name a citation in the same issue."""
    if not isinstance(text, str):
        return text, False

    removed = False

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        marker = match.group(1).strip()
        if not marker or is_reserved_token(marker) or marker in citation_ids:
            return match.group(0)
        removed = True
        return ""

    return _MARKER_RE.sub(replace, text), removed
