"""AgentCore Platform v1.0 — SVC-C2-134 State schema.

ADR-005: State must be a flat TypedDict. LangGraph checkpoints use msgpack
serialization, so only plain serializable fields (primitives, list, dict) are
allowed. No Pydantic / dataclass / arbitrary objects, and no credentials.

All fields below carry ONLY ID-ised (player-PII tokenised) text. The raw invoke
payload is consumed and tokenised in `PreProcessNode` local scope; the token→
original map (`pii_map`) is NEVER a State field (never checkpointed, never
surfaced). See docs/02_design.md §State Definition / State Constraints.
"""

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Dispute-adjudication-support agent state (flat TypedDict).

    Shared fields (`user_input`, `status`, `session_id`, `node_history`,
    `error_log`, `caller_trust_level`, ...) are inherited from `AgentState`.
    """

    # ── Request inputs (ID-ised form only — raw never written here) ──────────
    accuser_assertion: str
    defendant_rebuttal: str          # "" == no rebuttal submitted
    third_party_facts: list          # [{fact_id, text (ID-ised), source_ref}]
    rulebook: dict                   # {version, clauses:[{clause_id, requirements:[...]}]}
    dispute_context: dict            # {match_id, protest_id, rulebook_version}

    # ── Derived (pre_process) ────────────────────────────────────────────────
    validated_input: dict            # normalised, ID-ised input; sole claim carrier downstream
    sensitive_flags: list            # [{source_type, source_ref, category}] — source-level 要配慮 markers
    rejected: list                   # [{field, reason}] — input-gate rejections (with status=error)

    # ── Inner-workflow intermediates (declared as channels so LangGraph threads
    #    them between the inner nodes; StateGraph drops any key not on the schema).
    #    Both are ID-ised / organiser-rule text only — no raw claim text, no pii_map.
    candidate_clauses: list          # inner pre_process → main/post_process (deterministic clause map)
    llm_analysis: dict               # inner main → post_process (LLM values, pre-assembly)
    reconciliation_fallback: bool    # inner main → post_process; code-set, never LLM-authored

    # ── Derived (main / post_process) ────────────────────────────────────────
    dispute_analysis: dict           # draft analysis from the inner workflow
    sensitive_escalation: list       # issue_ids requiring manual escalation (post_process)
    formatted_output: dict           # final gated output (only free-text carrier surfaced)
