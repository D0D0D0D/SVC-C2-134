"""Player-PII ID-isation + 要配慮 (sensitive-category) flagging.

Reuses the framework `detect_pii` detector (no hand-rolled PII regex). ID-isation replaces each unique detected PII value with a stable token
`[PII_n]`; the token→original map (`pii_map`) is returned to the caller and kept
in `PreProcessNode` local scope ONLY (never written to State / checkpoint).

要配慮 categories are recorded at source level
as flags for manual escalation — they are NOT auto-processed and NOT auto-rejected.
"""

from __future__ import annotations

import copy
import json
import unicodedata

# input_context key carrying the 要配慮 flags computed on the RAW payload at the
# invoke boundary (see `sensitive_flags_from_raw` / `Graph.invoke`, design D8-bis).
# `input_context` is NOT a framework `_PII_SCAN_FIELD`, so it survives S-2 masking;
# the value carries NO PII (only {source_type, source_ref, category}).
PREMASK_FLAGS_INPUT_CONTEXT_KEY = "_premask_sensitive_flags"

try:  # pragma: no cover - import shape is environment-fixed
    from shared.security.pii_detector import detect_pii
except Exception:  # pragma: no cover - fail-safe: never crash ingress on import
    def detect_pii(text: str) -> list:  # type: ignore
        return []


# Deterministic 要配慮 lexicons (NFKC + casefold applied to both text and terms).
_HEALTH_TERMS = (
    "injury", "injured", "medical", "illness", "diagnosis", "disability",
    "medication", "concussion", "seizure", "mental health",
    "怪我", "けが", "病気", "診断", "持病", "障害", "発作", "通院",
)
_HARASSMENT_TERMS = (
    "harassment", "harass", "abuse", "assault", "threat", "slur", "doxx",
    "stalk", "hate speech", "bullying",
    "ハラスメント", "暴言", "脅迫", "中傷", "誹謗", "晒し", "いじめ",
)

# Genuine contact channels — 要配慮 contact PII *beyond* a bare player handle.
# Sourced from the shared TYPED detector (no hand-rolled contact
# regex): a `contact_pii` flag fires when detect_pii reports an email or a
# validated phone number. The shared phone_jp / phone_us patterns are STRUCTURAL —
# they require a leading 0 / +81 / +1 and separator-delimited digit groups — so a
# match-log numeric (ISO date "2024-08-04", score "16-14", "180 milliseconds",
# timestamp "04:12") does NOT match. That precision is what lets contact_pii cover
# phones without the false-withholding worry (a false contact_pii flag WITHHOLDS the
# issue). The detector's `name` type is deliberately EXCLUDED — a masked player
# handle is protected but is not 要配慮 (see D8).
_CONTACT_PII_TYPES = frozenset({"email", "phone_jp", "phone_us"})


def normalize(text: str) -> str:
    """NFKC + casefold — the canonical form for lexicon matching."""
    return unicodedata.normalize("NFKC", text or "").casefold()


def _detected_values(text: str) -> list[str]:
    """Extract raw PII substrings from detect_pii(), tolerant of its return shape."""
    values: list[str] = []
    try:
        result = detect_pii(text)
    except Exception:  # pragma: no cover - defensive
        return []
    for item in result or []:
        if isinstance(item, str):
            v = item
        elif isinstance(item, dict):
            v = item.get("value") or item.get("text") or item.get("match") or item.get("raw") or item.get("entity")
        elif isinstance(item, (tuple, list)) and item:
            v = item[0]
        else:
            v = None
        if isinstance(v, str) and v and v in text:
            values.append(v)
    # longest-first so overlapping matches replace deterministically
    return sorted(set(values), key=lambda s: (-len(s), s))


def idise_text(text: str, pii_map: dict[str, str]) -> str:
    """Replace detected PII in `text` with stable `[PII_n]` tokens.

    `pii_map` (token→original) is mutated in place; the SAME original always maps
    to the SAME token across the whole request (reverse index carried in the map
    under the sentinel key `__rev__`). Deterministic.
    """
    if not isinstance(text, str) or not text:
        return text
    rev = pii_map.setdefault("__rev__", {})
    out = text
    for value in _detected_values(text):
        token = rev.get(value)
        if token is None:
            token = f"[PII_{len(rev) + 1}]"
            rev[value] = token
            pii_map[token] = value
        out = out.replace(value, token)
    return out


def idise_input(obj: dict) -> tuple[dict, dict]:
    """Return (ID-ised copy of the input, pii_map).

    ID-ises the three player-authored / referee-observed free-text carriers
    (`accuser_assertion`, `defendant_rebuttal`, `third_party_facts[].text`). The
    version-pinned `rulebook` is organiser-authored rule text (not player data)
    and is left verbatim so clause citations quote the exact rule.
    """
    pii_map: dict[str, str] = {}
    out = copy.deepcopy(obj)

    if isinstance(out.get("accuser_assertion"), str):
        out["accuser_assertion"] = idise_text(out["accuser_assertion"], pii_map)
    if isinstance(out.get("defendant_rebuttal"), str):
        out["defendant_rebuttal"] = idise_text(out["defendant_rebuttal"], pii_map)
    facts = out.get("third_party_facts")
    if isinstance(facts, list):
        for f in facts:
            if isinstance(f, dict) and isinstance(f.get("text"), str):
                f["text"] = idise_text(f["text"], pii_map)

    pii_map.pop("__rev__", None)  # drop the reverse index; keep token→original only
    return out, pii_map


def _looks_like_date(value: str) -> bool:
    """True iff `value` is a plausible calendar date (DD-MM-YYYY / MM-DD-YYYY /
    YYYY-MM-DD, `-` or `/` separated). Regex-free (no PII/credential pattern —
    the reuse rule is about PII detection, not this false-positive guard).

    Rationale: the shared `phone_jp` pattern (`0\\d{1,4}-\\d{1,4}-\\d{4}`) matches a
    leading-zero date such as `04-12-2024`. A match-log / dispute date must NOT be
    treated as contact PII — a false `contact_pii` flag WITHHOLDS the issue, which the
    design ranks as worse than a missed escalation. A genuine phone (e.g. `03-1234-5678`)
    fails this check (its groups fall outside month/day ranges), so it still flags.
    """
    for sep in ("-", "/"):
        parts = value.split(sep)
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        nums = [int(p) for p in parts]
        if len(parts[0]) == 4:                       # YYYY-MM-DD
            year, month, day = nums
        elif len(parts[2]) == 4:                     # DD-MM-YYYY or MM-DD-YYYY (either order)
            a, b, year = nums
            month, day = (a, b) if a <= 12 else (b, a)
        else:
            continue
        if 1900 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
            return True
    return False


def _contains_contact_channel(norm_text: str) -> bool:
    """True iff the NFKC-normalised text carries a genuine contact channel.

    Reuses the shared typed `detect_pii` and keeps only the
    contact-channel types (`email` / `phone_jp` / `phone_us`). `email` is
    unambiguous. A `phone_*` match whose value is a plausible calendar date is
    rejected (`_looks_like_date`) so match-log / dispute dates do not cause false
    withholding. `name` matches (a masked handle) are excluded by construction.
    """
    try:
        found = detect_pii(norm_text)
    except Exception:  # pragma: no cover - defensive; detector import already guarded
        return False
    for d in found or []:
        if not isinstance(d, dict):
            continue
        dtype = d.get("type")
        if dtype == "email":
            return True
        if dtype in _CONTACT_PII_TYPES:  # phone_jp / phone_us
            value = d.get("value")
            if isinstance(value, str) and _looks_like_date(value):
                continue  # a date, not a phone → not contact PII
            return True
    return False


def detect_sensitive(text: str) -> list[str]:
    """Return the 要配慮 categories present in `text` (deterministic).

    Run this on the text BEFORE `idise_text` tokenises it.

    contact_pii is "contact PII **beyond a player handle**" (design §Input
    contract): it fires on a genuine contact channel — an email or a validated
    phone number reported by the shared typed `detect_pii` (email / phone_jp /
    phone_us). It does NOT fire on the framework default S-2 gate's `[MASKED]`
    marker nor on a bare name match: the framework masks player/team names
    (handles) too, so keying 要配慮 off a handle would flag EVERY dispute (every
    dispute names its teams) and withhold every party-cited issue — defeating the
    agent. A masked handle is protected but not sensitive; a genuine email/phone is
    the "beyond a handle" signal the design means.
    """
    if not isinstance(text, str) or not text:
        return []
    norm = normalize(text)
    cats: list[str] = []
    if any(normalize(t) in norm for t in _HEALTH_TERMS):
        cats.append("health_condition")
    if any(normalize(t) in norm for t in _HARASSMENT_TERMS):
        cats.append("harassment_or_abuse")
    if _contains_contact_channel(norm):  # normalised → full-width email/phone match too
        cats.append("contact_pii")  # contact PII *beyond* a bare player handle
    return cats


def collect_sensitive_flags(obj: dict) -> list[dict]:
    """Walk the sources (BEFORE ID-isation) and record source-level 要配慮 flags.

    Call with the parsed input prior to `idise_input`. Flags are recorded at
    SOURCE level ({source_type, source_ref, category}) — issue_ids do not exist yet
    at pre_process; post_process maps them onto the issues it builds. Note: the
    framework default S-2 gate may already have masked player handles to `[MASKED]`
    upstream — `detect_sensitive` deliberately does NOT treat that marker as
    contact PII (a masked handle is not 要配慮; only a genuine email/phone is).
    """
    flags: list[dict] = []

    def _emit(source_type: str, source_ref: str, text: str) -> None:
        for cat in detect_sensitive(text):
            flags.append({"source_type": source_type, "source_ref": source_ref, "category": cat})

    if isinstance(obj.get("accuser_assertion"), str):
        _emit("accuser", "accuser", obj["accuser_assertion"])
    if isinstance(obj.get("defendant_rebuttal"), str) and obj["defendant_rebuttal"]:
        _emit("defendant", "defendant", obj["defendant_rebuttal"])
    facts = obj.get("third_party_facts")
    if isinstance(facts, list):
        for f in facts:
            if isinstance(f, dict) and isinstance(f.get("text"), str):
                _emit("third_party", str(f.get("fact_id", "")), f["text"])
    return flags


def _mask_pii(text: str) -> str:
    """Mask detected PII spans in `text` to `[MASKED]`, mirroring the framework S-2 gate.

    Used on a flag's `source_ref` (a caller-controlled `fact_id`) in the RAW path so
    that (a) no raw PII value ever reaches State/checkpoint through the flag, and
    (b) the flag's `source_ref` equals the DOWNSTREAM source_ref that citations carry
    (the framework masks the same `fact_id` inside `user_input`), so escalation
    matching still fires. Constant refs (`"accuser"`/`"defendant"`) pass through
    unchanged.
    """
    if not isinstance(text, str) or not text:
        return text
    try:
        findings = detect_pii(text)
    except Exception:  # pragma: no cover - defensive; detector import already guarded
        return text
    masked = text
    for f in sorted((findings or []), key=lambda x: x.get("start", 0), reverse=True):
        start, end = f.get("start"), f.get("end")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(masked):
            masked = masked[:start] + "[MASKED]" + masked[end:]
    return masked


def sensitive_flags_from_raw(raw: object) -> list[dict]:
    """要配慮 flags computed on the RAW payload, BEFORE the framework S-2 gate masks it.

    Why this exists (design D8-bis): the framework default S-2 gate masks contact PII
    (email / phone) *and* player/team names in `user_input` to `[MASKED]` before ANY
    node's `execute()` runs — `_PII_SCAN_FIELDS` includes `user_input`. So a genuine
    contact channel is already `[MASKED]` by the time `pre_process` sees it, and
    `detect_sensitive` on the masked text can never emit `contact_pii`. The only place
    the raw payload is available is the invoke boundary (`Graph.invoke`), so the
    contact-PII 要配慮 flag must be computed there and threaded through non-scanned
    state (`input_context`). The returned flags carry NO PII values — only
    {source_type, source_ref, category}, and even `source_ref` (a caller-controlled
    `fact_id`) is run through `_mask_pii` so a PII-bearing id cannot leak into State.

    STR-ONLY by design: invoke's declared channel is a JSON `str`. A non-str
    `user_input` (e.g. a pre-parsed dict) is a contract violation that ALSO bypasses
    the framework S-2 masking entirely — the framework only masks `str` fields, so a
    dict payload's PII would reach state un-masked regardless of this function. That
    path is unsupported (server.py always passes a str); it yields no pre-mask flags
    rather than emitting flags whose masked `source_ref` would diverge from the
    framework-un-masked citation refs. Best-effort: returns [] on any parse failure
    (a malformed payload is rejected by `pre_process`'s input gate separately).
    """
    if not isinstance(raw, str) or not raw:
        return []
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(obj, dict):
        return []
    flags = collect_sensitive_flags(obj)
    for fl in flags:  # never let a PII-bearing fact_id ride into State via source_ref
        fl["source_ref"] = _mask_pii(fl.get("source_ref", ""))
    return flags
