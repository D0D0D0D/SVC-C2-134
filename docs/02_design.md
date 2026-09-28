# Template Design Specification — SVC-C2-134

> Esports Tournament Dispute Adjudication Support Agent
> This document defines the agent's value core, safety boundaries, and implementation contract.
> Cat 2 · Industry SVC · L1-direct inheritance (`AgentBaseGraph`) · GraphNode inner workflow.

## Position in AgentCore Architecture

- **Agent Class**: `Graph` (outer, `src/graph/graph.py`) — outer `AgentBaseGraph`; the domain
  workflow is an inner `AgentBaseGraph` (`src/graph/domain_workflow_graph.py`) wrapped by a
  `GraphNode` in the outer `main` slot.
- **L1 Base**: **AgentBaseGraph** (circle one) — L1-direct. No Level 2 base; this is a dedicated
  GraphNode domain workflow (matches none of RAG/Chat/ReAct/DocGen patterns).
- **Three-Layer Separation**:
  - State: flat TypedDict composition (no Pydantic — msgpack incompatible)
  - Node: L1 inheritance (Template Method: `execute(self, state: dict) -> dict` override only)
  - Graph: composition (`register_nodes()` for node substitution; `add_edges()` NOT overridden)

## Architecture Overview

### Node Configuration — outer graph (fixed 5-node backbone)

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version, session_id, trust_level | raw input | — | InitializeNode (default) |
| pre_process | Input validation + allowlist/quality gate; version-pin check (`dispute_context.rulebook_version == rulebook.version`); player-PII ID-isation (raw→ID-ised in local scope); 要配慮 (sensitive) category flagging at source level | `accuser_assertion`, `defendant_rebuttal`, `third_party_facts`, `rulebook`, `dispute_context` | `validated_input`, `sensitive_flags`, `rejected`, `status` | `PreProcessNode(FunctionNode)`; S-2 via `_extra_security_gate_input()` |
| main | Runs the inner domain workflow (5 steps) and merges its result | `validated_input` | `dispute_analysis` (draft) | `DisputeWorkflowGraphNode(GraphNode)` — see inner table |
| post_process | Output blocking (structural exclusion of verdict/penalty/bracket/eligibility), citation-ID enforcement, non-assertion guard on **all** free-text fields, provenance verification | `dispute_analysis` (draft) | `formatted_output`, `status` | `PostProcessNode(FunctionNode)`; S-3 via `_extra_security_gate_output()` |
| finalize | response_metadata, total_time_ms | — | `final_output` | FinalizeNode (default) |

### Node Configuration — inner domain workflow (`domain_workflow_graph.py`, `AgentBaseGraph`)

Inner graph fills the same 3 mandatory slots (`pre_process` / `main` / `post_process`). It
receives a **string** `user_input` + `ctx` only (GraphNode contract — outer dict does not
auto-propagate); `extract_input(state)` serialises `validated_input` via
`json.dumps({...}, default=str)` and the inner `pre_process` parses it.

| Inner node | Step | Responsibility | LLM / deterministic |
|------|------|----------------|---------------------|
| pre_process | ① + ②a | timeline / evidence index (internal Tool); clause-requirement candidate mapping (deterministic search, `clause_match.top_k`, `hybrid_search`) | deterministic |
| main | ②b + ③ + ④ | LLM applicability of facts to clause requirements/exceptions; three-way `supports/contradicts/unknown` per party per requirement; evidence-sufficiency evaluation (falls to `unknown` when unsupported) | LLM (`shared.services.llm`) |
| post_process | ⑤ | issue drafting: `candidate_rule_applicability` (non-assertive) + referee questions, each with source citation IDs | LLM + deterministic assembly |

### Data Flow

```
START → initialize → pre_process → main(GraphNode→inner workflow) → {route} → post_process → finalize → END
                                             ↓ (retry, max 3)
                                           pre_process
```
`route()` (framework-owned, NOT overridden) reads `state["status"]` after `main`:
`success`→post_process / `retry`→pre_process / else→finalize. `pre→main` is an
**unconditional edge**; `DisputeWorkflowGraphNode.execute()` is overridden to passthrough an
upstream error (`return {}` when `state["status"] == "error"`) so a rejected input does not
run the LLM workflow.

### State Definition (flat TypedDict, `AgentState` subtype)

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `accuser_assertion` | `str` | Accuser team's claim, **ID-ised form** (raw consumed at ingress, never written to State — see Constraints) | Yes |
| `defendant_rebuttal` | `str` | Defendant rebuttal, **ID-ised form**; **`""` = not submitted** | No (adapter injects `""`) |
| `third_party_facts` | `list[dict]` | `[{fact_id:str, text:str (ID-ised), source_ref:str}]`; `fact_id` unique within the list | Yes (≥1) |
| `rulebook` | `dict` | `{version:str, clauses:[{clause_id:str, requirements:[{requirement_id:str, requirement_text:str, exceptions:[str,...]}]}]}`. A clause with no explicit `requirements` derives one implicit requirement (`requirement_id == clause_id`); `requirement_id` unique within the rulebook. | Yes |
| `dispute_context` | `dict` | `{match_id:str, protest_id:str, rulebook_version:str}` — one match/one protest/one version | Yes |
| `validated_input` | `dict` | Normalised, ID-ised input produced by pre_process (the only claim-text carrier downstream) | (derived) |
| `sensitive_flags` | `list[dict]` | `[{source_type, source_ref, category}]` — 要配慮 markers recorded by pre_process at source level (issue_ids do not exist yet at pre_process) | (derived) |
| `sensitive_escalation` | `list[str]` | issue_ids for manual escalation — populated by **post_process** by mapping `sensitive_flags` onto the issues it builds | (derived) |
| `rejected` | `list[dict]` | `[{field:str, reason:str}]` — input-gate rejections (set with `status="error"`) | (derived) |
| `candidate_clauses` | `list[dict]` | Inner deterministic clause map (inner pre_process → main/post_process). **Declared as a State channel** so LangGraph threads it between inner nodes — `StateGraph` drops any key not on the schema. Organiser-rule text only (no PII). | (derived, inner) |
| `llm_analysis` | `dict` | Inner LLM values before deterministic assembly (inner main → post_process). Declared as a channel for the same reason. ID-ised citations only. | (derived, inner) |
| `dispute_analysis` | `dict` | Draft analysis from the inner workflow (see Output Contract) | (derived) |
| `formatted_output` | `dict` | Final, gated output; the only free-text carrier surfaced to the caller | (derived) |
| `status` | `str` | `AgentStatus` value (lowercase: `success`/`error`/`retry`) | (framework) |

> `pii_map` (token→original) is **not a State field** — it is built and consumed in `pre_process`
> local scope only, never written to State, never checkpointed, never surfaced.

**State Constraints (mandatory):**
- Flat TypedDict only (primitives + JSON-serializable types)
- **Ingress boundary (resolves the raw-vs-ID-ised question):** the raw invoke payload arrives as a
  JSON string in the framework's single input channel `user_input` (D5). `pre_process` parses it,
  ID-ises player-PII **in local scope**, writes back **only the ID-ised form** to the named State
  fields (`accuser_assertion` / `defendant_rebuttal` / `third_party_facts[].text`), and **clears
  `user_input`**. From `pre_process` onward — every downstream node and every `verbatim_quote` in
  `citations` — the source text is **ID-ised**, so downstream State and citations carry no raw PII.
- **Checkpoint safety (accurate scope, DDR D10 — corrects an earlier overclaim):** production compiles
  **checkpointer-less** (`memory_enabled=false`, `hitl.enabled=false`), so **nothing is persisted at
  rest** — the strongest honest guarantee. The one transient exposure is by framework necessity: the
  raw bundle sits in `user_input` between the framework `initialize` node and `pre_process` (which
  clears it); under a hypothetical checkpointer the pre-parse snapshot would hold it. This is exactly
  **why production is checkpointer-less** (there is no separate "scrubber" — the checkpointer itself is
  absent). `pii_map` (token→original) is **never a State field** and therefore absent from **every**
  snapshot even under a checkpointer (PB-5 asserts this across all checkpoints, not just the latest).
- No JWT/API keys/credentials in State (checkpoint DB leakage); secrets via `ctx.secrets.require()`
- InvocationContext via `config["configurable"]` only (never in State; never constructed directly)
- No Pydantic models, dataclass, arbitrary Python objects (msgpack incompatible)

## Input / Output / Config — executable contract

> Every "optional / defaulted / prohibited / §-referenced" statement below is made executable
> (default value + allowlist + reject rule + determinism + proving test). This section exists to
> answer the five executable-contract questions before review (avoids cases where
> a capability is declared without a contract).

### Input contract

| Field | Absent → adapter injects | Type/shape reject rule (pre_process, S-2) | Determinism |
|-------|--------------------------|-------------------------------------------|-------------|
| `accuser_assertion` | — (**required**; absent → `status=error`, no default) | non-`str` or empty → reject | echoed verbatim into citations |
| `defendant_rebuttal` | `""` (**on the accepted-value allowlist**; means "no rebuttal submitted") | non-`str` → reject | `""` ⇒ per-issue defendant column = `unknown` (never `contradicts`) |
| `third_party_facts` | — (**required, ≥1**) | not a list, any element missing/mis-typed `fact_id`/`text`/`source_ref`, duplicate `fact_id`, or `len<1` → reject | fact_ids echoed into citations |
| `rulebook` | — (**required**) | missing `version`/`clauses`; any clause missing `clause_id`; a `requirements` element missing `requirement_id`/`requirement_text`; duplicate `requirement_id` → reject | clauses are the *only* rule source; no external rule fetch |
| `dispute_context` | — (**required**) | missing `match_id`/`protest_id`/`rulebook_version` (each non-empty `str`) → reject; **`rulebook_version != rulebook.version` → reject** (version-pin mismatch) | pins the rule set for the whole request; stable across hosts |

**Reject / normalisation rules (S-2 `_extra_security_gate_input`, recursive over the whole input tree):**
- **Unknown keys** at the top level *and* in every nested object — `dispute_context`,
  `third_party_facts[]`, `rulebook` (`version`/`clauses`), `rulebook.clauses[]`,
  `rulebook.clauses[].requirements[]`, and `exceptions[]` (list of `str`) — are rejected against a
  per-object key **allowlist** (not denylist). Wrong scalar types at any level → rejected.
- **No binary / file-upload channel** (parser-less choice): the adapter accepts a JSON object only.
  A non-JSON-decodable or non-object body is rejected at the boundary (magic-byte / content-type
  self-declaration is not trusted). Free-text fields (`accuser_assertion`, `defendant_rebuttal`,
  `third_party_facts[].text`, clause `requirement_text`) also pass a **quality gate**: an entry whose
  control-char ratio or U+FFFD (replacement-char) ratio ≥ `input_quality.max_bad_char_ratio`
  (config default `0.10`) or whose length > `input_quality.max_field_chars` (default `20000`) is
  rejected. Rejections are surfaced as **`state["rejected"]: [{field, reason}]`** together with
  `status="error"` (the request does not partially proceed).
- **要配慮 (sensitive) categories** (health condition, harassment/abuse allegation, contact PII beyond
  a player handle) are recorded at **source level** as
  `state["sensitive_flags"]: [{source_type, source_ref, category}]` — issue_ids do not exist yet at
  pre_process. Health/harassment lexicons are detected in `pre_process`; **contact PII (email/phone)
  is detected on the raw payload at the invoke boundary** (the framework S-2 gate masks it to
  `[MASKED]` before any node runs — see **D8-bis**) and threaded PII-free via `input_context`, then
  adopted into `sensitive_flags` by `pre_process`. `post_process`, which builds the issues, maps each flag onto the issue(s) that cite the
  flagged source and (a) appends those `issue_id`s to `state["sensitive_escalation"]`, (b) withholds
  the flagged issue's free-text body from output and marks it **manual-escalation required** (the agent
  still returns non-sensitive issues). This makes the escalation temporally consistent.
- **Determinism**: the effective rulebook is fixed at the pre_process boundary (version-pin equality
  check) and cannot change during a request or across host timezones; no wall-clock/random input
  affects the classification.

### Output contract (`formatted_output` — allowlist keys only)

```
formatted_output = {
  "rulebook_version": str,                      # echo (citation anchor)
  "provenance_verified": bool,                  # set by post_process execute() (see Provenance)
  "issues": [ {
     "issue_id": str,                            # deterministic — see Issue segmentation below
     "issue_summary": str,                       # FREE TEXT — inline [citation_id] markers required
     "clause_refs": [clause_id, ...],
     "three_way": [ {                            # one row per applicable requirement (keyed by requirement_id)
         "requirement_id": str,
         "accuser":     { "value": "supports"|"contradicts"|"unknown", "citation_ids": [str,...] },
         "defendant":   { "value": "supports"|"contradicts"|"unknown", "citation_ids": [str,...] },
         "third_party": { "value": "supports"|"contradicts"|"unknown", "citation_ids": [str,...] } } ],
     "candidate_rule_applicability": [ {         # NOT a violation conclusion
         "clause_id": str, "requirement_id": str,
         "coverage": "covered"|"partial"|"unknown",
         "citation_ids": [str, ...] } ],
     "evidence_sufficiency": { "status": "sufficient"|"insufficient"|"unknown",
                               "gaps": [str, ...] },      # FREE TEXT — inline [citation_id] markers
     "referee_questions": [str, ...],            # FREE TEXT — questions to resolve
     "manual_escalation_required": bool,         # OPTIONAL, present+true only on a withheld 要配慮 issue
     "reconciliation_mode": "deterministic_fallback", # OPTIONAL closed enum; issue was degraded
     "citations": [ { "citation_id": str,
                      "source_type": "accuser"|"defendant"|"third_party"|"clause",
                      "source_ref": str,          # fact_id / clause_id / party tag (must exist in input)
                      "requirement_id": str,      # REQUIRED when source_type=="clause" — the exact requirement
                                                  #   within source_ref's clause (a clause may hold many
                                                  #   requirements[]; clause_id alone is ambiguous). Must exist in
                                                  #   the rulebook AND belong to source_ref. Omit for non-clause.
                      "source_field": "requirement_text"|"exception",  # which text of that requirement is quoted
                                                  #   (default "requirement_text"). "exception" ⇒ exception_index required
                      "exception_index": int,     # REQUIRED iff source_field=="exception" — 0-based index into
                                                  #   requirement.exceptions[]; missing / out-of-range → reject
                      "verbatim_quote": str } ]   # ID-ised source substring, len ≥ min_quote_len
  } ],
  "unresolved_notes": [str, ...],
  "escalation_required": [issue_id, ...]        # 要配慮 manual-escalation signal (§Input contract);
                                                #   lists the issue_ids whose free text was withheld
}
```

**Enforced output-key allowlist (S-3 — primary structural gate, DDR D10):** the prohibited-field set
below is a *denylist net*; the *allowlist* is the primary gate. `post_process.gate_check` rejects any
key not named at three levels — top-level (`OUTPUT_TOP_KEYS`: `rulebook_version`, `provenance_verified`,
`issues`, `unresolved_notes`, `escalation_required`), per-issue (`ISSUE_KEYS`: the eight base issue fields
above + optional `manual_escalation_required` / `reconciliation_mode`), and per-citation (`CITATION_KEYS`: the seven citation fields).
A never-listed key (e.g. a free-text `recommendation`) is rejected even though it is not on the
denylist — otherwise it would ride unguarded assertive content to the caller, since the S-3 free-text
scan only walks the *named* free-text paths. All three sets live in `src/services/output_guard.py`.

**Citation reference grammar:** every free-text field (`issue_summary`, `evidence_sufficiency.gaps[]`,
`referee_questions[]`) and every `three_way.*.citation_ids` / `candidate_rule_applicability[].citation_ids`
references `citation_id`s that MUST exist in the same issue's `citations`. Free-text carries the refs as
inline `[<citation_id>]` markers; structured fields carry them as `citation_ids` lists. An unresolved
free-text marker degrades that issue to deterministic fallback; a dangling structured ref remains an
S-3 terminal reject.

**Issue segmentation (deterministic — structure is input-derived, not LLM-chosen):**
1. The inner `pre_process` runs the **deterministic** clause-requirement mapping (clause_match retrieval
   over all rulebook clauses, `top_k`, tie-break by ascending `clause_id`). Its output is the
   **candidate-clause set** — a pure function of the (fixed) input + config.
2. **One issue per candidate clause**, emitted in **ascending `clause_id` order**;
   `issue_id = "issue_" + clause_id`; `clause_refs == [clause_id]`.
3. Within each issue, emit **exactly one `three_way` row and one `candidate_rule_applicability` record
   per requirement of that clause** (all requirements, sorted by `requirement_id`). A requirement the
   facts do not reach still appears with all parties `unknown` / `coverage: unknown`.
4. The LLM fills only the **values** (`value`, `coverage`, free text) inside this fixed structure; it
   never decides which clauses/requirements/issues exist. So `issue_id`, issue count, row count, and
   ordering are reproducible across runs for the same input.

**Prohibited output keys (structural adjudication boundary):** `verdict`, `ruling`, `penalty`, `sanction`,
`bracket_change`, `eligibility`, `disqualification`, `winner`, `loser`, `points_adjustment` — plus any
operator-added entries. Presence of any (at any nesting level) → S-3 reject (`status=error`). These are
human-only decision fields reserved for tournament officials; the agent never emits them.
**Non-removable floor:** the prohibited set is a hardcoded constant in `post_process`; `config.output_blocking.prohibited_fields`
can only **add** to it — removing a floor entry via config has no effect (fail-closed; an operator cannot
open the adjudication boundary by editing config).

### Config contract (`config/config.yaml`)

Every accepted key has a type/range and a hardcoded default; the graph validates the loaded config at
graph compile and rejects (fail-closed) any value out of range. Unknown keys are ignored.

| Key | Type / range (default) | Read by | Effect if changed |
|-----|------------------------|---------|-------------------|
| `llm.provider` | enum `anthropic`\|`openai`\|`bedrock` (`anthropic`) | inner `main` LLM node | selects `shared.services.llm.*` client |
| `llm.model` | str (`claude-sonnet-5`) | inner `main` LLM node | model id passed to client |
| `llm.temperature` | float 0.0–1.0 (`0.0`) | inner `main` LLM node | `0.0` for high-responsibility support |
| `llm.max_tokens` | int ≥1 (`4000`) | inner `main` LLM node | completion length ceiling |
| `timeout_s` | int ≥1 (`60`) | node execution | per-invoke timeout |
| `budget_usd` | float ≥0 or `null` (`1.50`) | advisory | soft cost ceiling (advisory for AgentBaseGraph) |
| `clause_match.top_k` (int ≥1) / `hybrid_search` (bool) | inner `pre_process` clause mapping | candidate-clause recall. **Deterministic tie-break:** equal-score clauses are ordered by ascending `clause_id` so ranking is reproducible. |
| `provenance.min_quote_len` (int ≥1) | post_process provenance verify | quote-length floor (guards vacuous `""` match) |
| `provenance.require_citation_for_supports_contradicts` (bool) | post_process | `true` ⇒ a `supports`/`contradicts` cell with no citation is downgraded to `unknown` |
| `input_quality.max_bad_char_ratio` (0–1) / `max_field_chars` (int) | pre_process quality gate | free-text reject thresholds |
| `output_blocking.prohibited_fields` (list[str], default `[]` add-set) | post_process S-3 | **additive only** — merged onto the hardcoded non-removable floor; config cannot remove a floor entry |
| `max_retry` (int 0–3, default `3`) | framework backbone | framework ceiling is 3 (`RETRY, max 3`); a value outside 0–3 is **rejected at compile** (consistent with the fail-closed range rule above) — not a free knob |
| `hitl.enabled` (bool `false`) / `memory_enabled` (bool `false`) | graph compile | no checkpointer / no in-graph suspend |

A proving test (`test_config_validation`) asserts each key's default, an in-range value, and that an
out-of-range/wrong-type value is rejected at compile (and that a removed floor entry stays prohibited).

## Provenance contract (S-3 Provenance — not "ID-exists")

ID existence alone does **not** guarantee fidelity. The gate enforces, per issue:

1. **Verbatim quote — resolved to a single named source**: each `citations[].verbatim_quote` is a
   literal substring of its **uniquely resolved** source, verified
   `quote in source AND len(quote) >= provenance.min_quote_len` (empty/1-char quotes are vacuous —
   `"" in text` is always true — so a length floor is mandatory). Source resolution by `source_type`:
   - `accuser` → `accuser_assertion`; `defendant` → `defendant_rebuttal`; `third_party` → the
     `third_party_facts[]` element whose `fact_id == source_ref`.
   - `clause` → the **single** rulebook text named by
     `(source_ref, requirement_id, source_field[, exception_index])`: the requirement whose
     `requirement_id == citation.requirement_id` **within clause `source_ref`**, then either its
     `requirement_text` (default) or `requirement.exceptions[exception_index]`. **Because one clause may
     hold multiple `requirements[]`, `clause_id` alone is ambiguous** — the `requirement_id` locator
     (already the key of `three_way`/`candidate_rule_applicability` rows) is what makes the substring
     check executable and deterministic. The quote is matched against **only** that one resolved text;
     a quote that is verbatim in a *different* requirement of the same clause does **not** pass.

   **Locator shape (canonical emitted form — normalised then validated):** for a
   `source_type=="clause"` citation, `source_field` is normalised to `"requirement_text"` when omitted,
   and MUST otherwise be exactly one of the two enum values (`"requirement_text"` | `"exception"`) — any
   other value → reject. `exception_index` MUST be present **iff** `source_field=="exception"`, and MUST
   be a **non-negative** integer **in range** for that requirement's `exceptions[]`; it MUST be **absent**
   when `source_field=="requirement_text"` (a negative index such as `-1` is rejected explicitly — it is
   a valid Python index but not a valid locator). A **non-clause** citation's inapplicable
   `requirement_id` / `source_field` / `exception_index` keys are removed before verification.

   **Locator reject rules (fail-closed floor — NOT config-tunable, mirrors the prohibited-field floor):**
   for a `source_type=="clause"` citation, provenance verification rejects (`status=error`,
   `provenance_verified=false`) when `requirement_id` is **missing**, **unknown** (no such
   `requirement_id` in the version-pinned rulebook), or **mismatched** (that `requirement_id` does not
   belong to the `source_ref` clause); or when the locator shape above is violated. There is no config
   key to relax this — the locator is mandatory whenever a clause is cited.

   > `citation.requirement_id` (where the cited quote lives) and a `three_way`/`candidate_rule_applicability`
   > row's `requirement_id` (which requirement is being *analysed*) share the same id namespace but play
   > different roles — they are **not** forced equal. A quote drawn from requirement R1 may legitimately
   > support the analysis of a different requirement, so the gate never requires the two to match; it only
   > requires the cited quote to be verbatim in the requirement the *citation* names.
2. **Claims → source (fidelity)**: every structured `citation_id` must exist in that issue's
   `citations`, and its quote must pass (1). An unresolved marker in free text is removed and the
   complete issue is downgraded to deterministic fallback; a structured absent ID or false quote rejects.
3. **Source → claims (coverage / bidirectional)**: any `three_way` cell that is `supports` or
   `contradicts` MUST reference at least one supporting citation; only `unknown` may lack one. This
   is the direction that a one-sided ID check misses — it stops the model from asserting a
   position that no cited source backs (which would otherwise read as a de-facto ruling).

**Three-stage failure semantics:** (1) normalise non-clause locator-key drift, then (2) downgrade an
issue with unresolved prose markers (or an uncited positional cell) to the documented unknown shape,
then (3) terminal-reject false sources/quotes/locators, dangling structured IDs, prohibited fields, and assertive rulings.

> This AG derives no numeric forecasts, so numeric NFKC/漢数字 extraction is not the central risk;
> the central risk is an *unsourced positional claim*, which (2)+(3) close. Any quantitative token
> in free text (score/round/timestamp) is still required to cite a `third_party` fact.

**Where verification runs (S-3 hook receives `result` only — no `state`):** the substring check
`verbatim_quote ∈ source` needs the source text, which lives in `state`. Therefore **`post_process.execute(self, state)`**
performs the full provenance verification (it has both `state` sources and the draft `citations`) and
**declares the outcome** as `formatted_output["provenance_verified"]`, applying the three-stage
semantics above: a terminal failure sets `status=error` / `provenance_verified=false`; normalisation
and deterministic fallback never suppress source/quote/locator authentication failures.
(Canonicalising an omitted clause `source_field` to `"requirement_text"` is a **pre-validation shape
normalisation**, not a failure sanitisation — it happens before the reject checks, and that canonical
value is what the emitted citation carries.) The
unknown / mismatched-locator resolution and the substring match need the rulebook text, which lives in
`state`, so they run in `execute()` and are folded into `provenance_verified`. The S-3
`_extra_security_gate_output(result)` hook then enforces the **declared** result on `result` alone —
`provenance_verified is True`, no dangling `citation_id`, every `verbatim_quote` length ≥ `min_quote_len`,
**every `source_type=="clause"` citation carries a `requirement_id` with a well-formed
`source_field` / `exception_index` shape (and emitted non-clause citations carry none of the three)**,
no prohibited field, no assertive lexicon in free text —
and `raise`s otherwise. The gate never reads `state`, so it cannot be tricked by a state-shape gap
(pattern: verify-in-execute, enforce-in-gate).

## Framework Utilization

### Shared Components Used
- [x] InvocationContext (correlation_id, session_id, trust_level)
- [x] `shared.services.llm.*` (`BaseLLM.complete()`) — **injection seam** (`src/services/llm_seam.py`):
      the platform injects a fully-configured `BaseLLM` (which carries its own credentials); the
      template holds no model id and **no LLM secret** — `requires.secrets` is `[]` and the code never
      calls `ctx.secrets.require()` for an LLM key (DDR D10 corrects an earlier manifest⇄code mismatch
      where `LLM_API_KEY` was declared but never required). **No bespoke LLM client** — the framework's own is reused.
- [x] `shared.security.pii_detector.detect_pii` — reused for player-PII ID-isation (no hand-rolled regex)
- [x] SecurityViolationError
- [x] S-2: `_extra_security_gate_input()` on `PreProcessNode` — input-key allowlist, type/shape reject,
      version-pin check, free-text quality gate, 要配慮-category flagging（手動エスカレーション用のフラグであり、自動処理はしない）
- [x] S-3: `_extra_security_gate_output()` on `PostProcessNode` — output-key allowlist +
      prohibited-field rejection + non-assertion guard on **all** free-text paths + provenance verify.
      The hook receives `result` only (no `state`) — post_process `execute()` **declares** the
      needed values inside `formatted_output`; the gate branches on those, never on `state`.
- [x] S-4: `emit_trace_event()` — ≥1 domain event inside **every** node's `execute()`, including
      reject / upstream-error-passthrough / downgrade-to-unknown paths (not only the success path).

> **S-2/S-3 by node type:** `PreProcessNode`/`PostProcessNode` are `FunctionNode` subclasses → the
> `@final` gate runs automatically; we extend via `_extra_*` only. `DisputeWorkflowGraphNode` is a
> `GraphNode` → both gates are a deliberate no-op (the inner FunctionNodes already gate; upstream
> pre_process already ran S-2).

### Non-assertion guard — all free-text paths (S-3 backdoor closure)

The verdict/assertion guard is applied to **every** LLM-authored free-text field, not just
`issue_summary` (a guard on the main field alone leaks through side fields):

```
_FREE_TEXT_PATHS = (
    ("issues", "*", "issue_summary"),
    ("issues", "*", "evidence_sufficiency", "gaps", "*"),
    ("issues", "*", "referee_questions", "*"),
    ("unresolved_notes", "*"),
)   # "*" = list expansion; applied recursively
```
The guard is **product-safety framing, not legal compliance**: the *primary* defense is structural
(prohibited fields excluded from the schema allowlist); the free-text lexicon (assertive
ruling/verdict/imperative terms) is the secondary net. It does not establish regulatory compliance.

## EU AI Act Art.13 Design-Time Evidence (Advisory — `Not in scope`, SVC)

| Evidence item | Design reference / description |
|---------------|--------------------------------|
| Intended purpose and operating context | Read-only, non-final adjudication *support* for a human referee at hard-to-judge dispute points; one match/one protest/one version-pinned rulebook. Not administration of justice. |
| System capabilities and limitations | Reconciles three parties × clause requirements with source citations; does NOT decide results, penalties, brackets, eligibility; no video/audio analysis, no cheat detection, no intent inference. |
| User-facing transparency information | Every output is source-cited and marked non-assertive; verdict/penalty/bracket/eligibility fields are structurally absent; final ruling is the referee's. |
| Human oversight mechanism | The referee reviews the returned analysis and makes the final ruling downstream of invoke (no in-graph interrupt required — see DDR A-3). |

## Entry Points
- **`src/api/server.py`** — standalone FastAPI `/invoke` adapter (STG / self-hosted).
- **`cli.py`** — AGENTIC STAR Marketplace entry adapter. Subclasses `Graph` as `MarketplaceGraph`:
  forwards `user_input` unmodified (the input contract above is a recursive key allowlist, so no
  envelope or `mode` key may be added), drops the runner-supplied `input_context`, forces
  `hitl_allowed=False`, and restricts the secret provider to the three Azure connection values.
  The `BaseLLM` is installed into the `src/services/llm_seam` module seam by `main()` — it is
  **not** passed via `config["llm"]`, which `config_spec.validate_config` requires to be a mapping.

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: `framework/` and `shared/` only (no `agents/base/` required)

## Composition Pattern
- **Pattern**: GraphNode (in-process subgraph — inner domain workflow)
- **Composition target**: `DisputeWorkflowGraph` (inner `AgentBaseGraph`, `src/graph/domain_workflow_graph.py`)
- **Mandatory GraphNode abstract methods** (all three implemented):
  `extract_input(state) -> str` (serialise `validated_input`, `json.dumps(..., default=str)`),
  `get_subgraph() -> BaseGraph` (compiled `DisputeWorkflowGraph`), `merge_output(state, sub_result) -> dict`
  (map inner result → `dispute_analysis` **with explicit field mapping**, no raw pass-through).
- **Error propagation strategy**: `propagate` (default) — an inner-workflow error surfaces as
  `status=error`; the outer graph does not return a partial analysis. Additionally, an *upstream*
  (pre_process) error is short-circuited by overriding `execute()` to `return {}` on `status=error`.

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | Fixed multi-step workflow; no self-directed loop (Cat 2). |
| Composition pattern | Standalone | GraphNode | **GraphNode** | Domain complexity (5-step reconciliation) encapsulated in an inner graph; keeps the outer 5-node backbone clean. |
| HITL (A-3) | in-graph `interrupt` | none (downstream review) | **none** | Human review is *after* invoke returns (referee reads the analysis); no graph-internal suspend/resume necessity (MunicipalContractVariationEvidenceBriefAgent ruling: decide HITL by in-graph-suspend necessity, not "a human is involved"). Safety net is structural output blocking, not an interrupt. |
| Adjudication boundary | soft (prompt only) | structural (schema exclusion) | **structural** | Verdict/penalty/bracket/eligibility excluded from the output-key allowlist + S-3 reject; enforces the human-adjudication boundary. |
| Provenance | ID-exists | verbatim + bidirectional | **verbatim + bidirectional** | ID existence does not guarantee fidelity; supports/contradicts without a cited source downgraded to unknown. |
| Clause provenance locator (**high-severity review finding**) | `clause_id` only | requirement-level locator (`requirement_id` + `source_field`/`exception_index`) | **requirement-level locator** | A clause may hold multiple `requirements[]`, so `clause_id` alone cannot name which `requirement_text`/exception a quote came from — the S-3 substring check was non-deterministic and admitted a false-pass (a quote verbatim in a *different* requirement of the same clause). `requirement_id` is already the key of `three_way`/`candidate_rule_applicability`, so this only propagates the existing locator into `citations`; missing/unknown/mismatched locator → reject (fail-closed floor, not config-tunable). |

### 先回りセット照合（設計段階のチェック）

> Cross-check against the six findings that recurred on sibling proposals, applied here
> before review. "該当" = applies to this template.

| # | 先回り項目 | 該当 | 対処（本設計での反映） |
|---|-----------|------|------------------------|
| 1 | provenance を「ID実在」で終わらせない | ✅ 該当 | §Provenance contract: verbatim-quote (`min_quote_len`) + bidirectional (claims→source fidelity **and** source→claims coverage). supports/contradicts without citation → downgrade to unknown. **Clause citations carry a requirement-level locator (`requirement_id` + `source_field`/`exception_index`) so the substring source is unique even when a clause holds multiple requirements (high-severity review finding).** PB adds an "existing-but-irrelevant citation_id" case and a "cross-requirement quote mismatch" case. |
| 2 | 永続KBを持つなら統治契約を design 入場条件に (tenant+org fail-closed) | ⛔ 非該当 | **No persistent KB / no `/ingest`.** One match / one protest / one version-pinned rulebook per invoke; all inputs caller-supplied and request-scoped. No cross-invoke accumulation, so tenant/org KB governance does not apply. |
| 3 | PoC 検証計画を proposal に | ⚠️ 限定 | Benefit indicators (referee prep-time reduction, citation coverage, remand rate) are recorded as *value* signals, **not** as release acceptance conditions. Buyer names and PoC plans are deliberately excluded from `docs/01`; governance and data items are honest *advancement conditions* for deployment, not design blockers. |
| 4 | §1「継承先」は継承クラスのみ | ✅ 該当 | agent.yaml `base_type: AgentBaseGraph`; README `Inherits: AgentBaseGraph (L1 direct)`. GraphNode / inner graph live in this §Composition Pattern, not in the inheritance field. |
| 5 | 外部ソース確認は実施日を明記 | ⛔ 非該当 | **No external API / data source dependency.** Inputs are organiser-supplied (protests, match logs, referee observation, version-pinned rulebook). Data-holding is a Proposal advancement condition (§12), not a live external connection to confirm. |
| 6 | 入力の allowlist + 品質ゲート | ✅ 該当 | §Input contract: top-level + nested key allowlist, type/shape reject, no binary/file channel (parser-less), free-text quality gate (control-char / U+FFFD ratio → `state["rejected"]`). |

### Implementation-confirmed deviations — recorded in the same revision

> To keep the design and implementation aligned in the same revision, the
> following are the points where the implementation confirmed or refined the design.
> None change the security contract; each is a construction/verification choice.

| # | Design statement | Implementation | Rationale |
|---|------------------|----------------|-----------|
| D1 | inner `main` = LLM three-way; inner `post_process` = LLM issue drafting (two LLM steps) | **One** structured LLM call in inner `main` (`reconcile_node`) produces values + free text + citations together; inner `post_process` (`issue_draft_node`) does **deterministic** issue segmentation/assembly + non-assertive framing (no second LLM call) | A single call keeps `citation_id`s coherent across structured values and free text; two calls would need cross-call citation reconciliation. The LLM still never decides which clauses/requirements/issues exist. |
| D2 | `defendant_rebuttal=="" ⇒ defendant column unknown (never contradicts)` | Enforced **deterministically** in `issue_draft_node` (forces every defendant cell to `unknown` when no rebuttal), not left to the LLM prompt | Makes the "never contradicts" guarantee code-enforced, not model-dependent. |
| D3 | S-2 via `_extra_security_gate_input()` | The hook rejects **present-but-invalid** input (fail-closed, short-circuits `execute`) but is **lenient on absent input** (execute returns the empty-input error) | Keeps the framework PB-6 bare-state order intact (a gate that rejected the bare probe state would break the invoke-order boundary test). |
| D4 | `main` slot is a `GraphNode` (`src/nodes/main_node.py` in the initial layout) | `DisputeWorkflowGraphNode` lives in **`src/graph/`**, not `src/nodes/` | PB-6 discovers and single-steps every node under `src/nodes/`; a GraphNode there would run a whole subgraph inside the single-node order check. Matches the Cat 2 sample (GraphNode defined in the graph layer). |
| D5 | Ingress: raw payload received by adapter, passed to pre_process | The structured dispute bundle is passed as a **JSON string in `user_input`** (invoke's only input channel); `pre_process` parses, ID-ises, and **clears `user_input`** after parsing | `AgentBaseGraph.invoke(user_input: str)` seeds only `user_input`; a single JSON string is the deterministic channel. Clearing it keeps the raw request blob out of the final state. |
| D6 | PB-5: no raw / no pii_map in checkpoint | Production compiles **checkpointer-less** (`memory_enabled=false`, `hitl.enabled=false`) → nothing persisted at rest. PB-5 asserts (a) no production checkpointer, (b) the deserialised latest checkpoint (under an InMemorySaver) has `user_input` cleared and no `pii_map`, (c) ID-isation isolates `pii_map` from the emitted structure | Honest to the design's "never checkpointed" (there is no checkpointer) while still proving at-rest cleanliness under a hypothetical checkpointer. |
| D7 | Inner workflow threads `candidate_clauses` (pre→main→post) and `llm_analysis` (main→post) | Both are **declared as `State` channels** (`src/schemas/state.py`). LangGraph's `StateGraph(state_schema)` only persists keys present on the TypedDict; an undeclared intermediate returned by an inner node is silently dropped, so the inner post_process saw zero candidates and the agent emitted **0 issues**. Declaring the two channels restores the flow. | Framework channel model — an intermediate that crosses inner nodes MUST be on the schema. Both carry only ID-ised / organiser-rule text (no raw claim text, no `pii_map`), so State-safety/PB-5 are unaffected. |
| D8-bis | 要配慮 `contact_pii` = "contact PII **beyond a player handle**", detected on the RAW payload at the invoke boundary | **Corrects the original D8 (email-only, `_CONTACT_RE`), which was never functional end-to-end.** Root cause: `_PII_SCAN_FIELDS` includes `user_input`, so the framework default S-2 gate masks **every** contact channel — email **and** phone — to `[MASKED]` *before any node's `execute()` runs* (verified live: `>> collect_sensitive_flags sees '…reach our manager at [MASKED]…' → emitted []`). So `detect_sensitive` on the text `pre_process` sees can never emit `contact_pii`; the original `test_sensitive_contact_pii_on_email_only` only exercised `detect_sensitive` on **raw** text, hiding the gap. Fix: (a) `pii.detect_sensitive` now sources contact channels from the shared **typed** `detect_pii` (no hand-rolled regex), keeping `email`/`phone_jp`/`phone_us` (the `name` type — a masked handle — is excluded, so no over-flagging; the structural phone patterns require leading `0`/`+81`/`+1` + separator groups, so match-log numerics do not misfire). (b) `pii.sensitive_flags_from_raw` runs this on the **raw payload** at the only pre-mask seam — `Graph.invoke` — and threads the **PII-free** flags (`{source_type, source_ref, category}` only) through `input_context`, which is **not** a `_PII_SCAN_FIELD` (survives masking) and **cannot be set by a client** (`server.py`'s `InvokeRequest` exposes only `input`/`session_id`); `invoke` **always overwrites** the key so a direct caller cannot spoof the escalation set. `pre_process` then adopts those flags into `state["sensitive_flags"]` (fallback: detect on the parsed obj for direct-node callers — health/harassment survive masking, contact PII does not). `post_process` maps them onto citing issues → withhold + escalate, unchanged. | Makes the design's promised contact-PII escalation actually fire (email path too, not just phone — the medium-severity review gap), while keeping the "beyond a player handle" qualifier (names excluded) and avoiding false withholding. Regression-guarded end-to-end by `test_full_invoke_success` (normal dispute → `escalation_required==[]`), `test_email_contact_pii_withholds_issue`, `test_phone_contact_pii_withholds_issue`, `test_caller_supplied_sensitive_flags_cannot_spoof_escalation`; unit by `test_masked_handle_is_not_contact_pii`, `test_match_log_numerics_are_not_contact_pii`, `test_sensitive_contact_pii_email_and_phone`, `test_sensitive_flags_from_raw_*`. **Live-LLM confirmed** (real LLM: NORMAL → 2 issues / `escalation==[]`, PHONE → `escalation==['issue_C-INPUT-01']`). **Implementation judgment item B-3 (sensitive-category handling) is reflected below.** |
| D8-ter | D8-bis hardened over two pre-push adversarial rounds | Fail-closed tightenings on the raw-detection path: **(a)** `sensitive_flags_from_raw` runs each flag's `source_ref` through `_mask_pii` — a caller-controlled `fact_id` bearing PII (e.g. `fact_id: "witness@example.com"`) can no longer ride raw PII into `input_context`/`sensitive_flags`/checkpoint (State-safety), and the masked ref equals the framework-masked downstream citation `source_ref`, so third-party escalation still matches. **(b)** `_looks_like_date` excludes calendar-date-shaped values (`04-12-2024` etc.) from `phone_*` `contact_pii` — the shared `phone_jp` pattern matches a leading-zero `DD-MM-YYYY`, which would falsely WITHHOLD an issue citing a source that merely mentions a date (the design ranks a false withhold as worse than a missed escalation); a genuine phone (out-of-range groups) still flags. **(c)** `sensitive_flags_from_raw` is **str-only** — a non-str `user_input` (dict) is a contract violation that ALSO bypasses framework masking, so it yields no pre-mask flags rather than emitting flags whose masked ref would diverge from the un-masked citation ref. **(d)** `Graph.invoke` passes an `input_context` carrying ONLY the PII-free flags key and **drops caller-supplied keys** — `input_context` is not a `_PII_SCAN_FIELD`, so forwarding arbitrary caller keys would checkpoint caller PII verbatim; this agent consumes nothing from `input_context`, so dropping is safe and also makes escalation un-spoofable. | Each narrows what passes / closes a leak; none relaxes a contract. Regression: `test_sensitive_flags_from_raw_never_leaks_pii_in_source_ref`, `test_third_party_contact_pii_withholds_cited_issue`, `test_date_shaped_numerics_are_not_contact_pii`, `test_sensitive_flags_from_raw_tolerant_of_bad_payload` (dict→[]), `test_caller_supplied_input_context_cannot_spoof_or_leak`. |
| D9 | S-3 hardening confirmed during pre-push adversarial review | Six tightenings, all fail-closed and design-faithful: (a) the non-assertion guard now also covers top-level `unresolved_notes` (a side free-text path the design's `_FREE_TEXT_PATHS` lists but the first draft missed); (b) `citations` are rebuilt to the 7-key output allowlist in `issue_draft_node` so an off-schema key (e.g. a free-text `metadata`) cannot ride unguarded content to output; (c) party citations MUST carry `source_ref == "accuser"/"defendant"` (design "party tag") — this also keeps the `(source_type, source_ref)` key aligned with the 要配慮 flag so a bogus ref cannot dodge withholding; (d) `gate_check` is fail-closed when a non-error result lacks a `formatted_output` dict; (e) reserved masking tokens `[MASKED]`/`[PII_n]` are excluded from citation-marker extraction (they share the `[token]` shape but are not references); (f) config validation rejects non-mapping sections and type-checks `llm.model`. | Each tightening only narrows what passes; none relaxes a contract. Regression tests added in `test_provenance.py` / `test_issue_draft_node.py` / `test_config_validation.py`. |

| # | Design statement | Implementation | Rationale |
|---|------------------|----------------|-----------|
| D10 | S-3 gate rejects prohibited fields (denylist) + provenance; second adversarial round (pre-push) | Six further tightenings, all fail-closed: **(a)** `gate_check` now enforces an **output-key allowlist** at three levels (top / issue / citation) — a never-listed key such as a free-text `recommendation` is rejected, not only the prohibited denylist (a High finding: denylist-only S-3 let an unlisted assertive key ride to output); **(b)** `escalation_required` (top) and `manual_escalation_required` (issue) are added to the documented allowlist (they were emitted by `post_process` but absent from the §Output-contract shape); **(c)** a clause citation with an unhashable/empty `source_ref` is a clean terminal reject, not a `TypeError` crash in the `by_clause_req` tuple-key lookup; **(d)** config int-range keys reject `bool` (`isinstance(True, int)` is True) via `_is_int`; **(e)** `agent.yaml requires.secrets` reduced to `[]` (the injected `BaseLLM` carries its own auth; the code never `require()`s an LLM key — F5 sub-finding); **(f)** PB-5 checks `pii_map` absence across **every** checkpoint (not only the latest) and the checkpoint-safety claim is corrected to the accurate checkpointer-less scope. | Each narrows what passes; none relaxes a contract. Regression tests: `test_provenance.py` (allowlist + source_ref), `test_config_validation.py` (bool), `test_pb2_pb5_state_safety.py` (all-checkpoint), `test_startup.py` (route+auth), `test_pb1_audit_events.py` (reject/passthrough emit). |
| D11 (rejected findings) | The adversarial review also raised: **(i)** "issue_summary must carry an inline citation marker" and **(ii)** "production always degrades because the adapter never wires an LLM". | **Both rejected with rationale (recorded).** (i) A blanket "every issue_summary must cite" rule would reject the **deterministic offline fallback** summary (`reconciliation.fallback_analysis` — the production default when no LLM is injected, a genuinely non-assertive "requires referee review" line that cites nothing) *and* legitimate pure-absence summaries; positional conclusions live in `three_way` (bidirectionally guarded ③) and the assertive-lexicon net covers verdict wording. The enforced free-text guarantees are: dangling-marker reject, bidirectional three_way, assertive lexicon, and the output-key allowlist. A non-verdict prose assertion with no marker is a **documented residual risk**, not silently ignored. (ii) The keyless **injection seam is the deployment integration point** (`llm_seam.py` / `server.py` document it): the platform injects the LLM, and the pre-push live-LLM check confirmed the seam *enriches* (2 issues, provenance_verified, non-empty three_way) when a `BaseLLM` is injected. The offline fallback is a deliberate degrade, not a defect. | Adversarial findings may be rejected when implementation behaviour supports a documented rationale. |

#### 実装フェーズの判断反映（論点 / 判断 / 反映先）

| 論点 | 判断（人間ゲート） | 反映先 |
|------|-------------------|--------|
| **B-3(a) S-3 free-text guard strictness** — should `issue_summary` be forced to carry an inline citation marker? (re-raise of D11(i)) | **現行維持（keep current）** — do **not** mandate a citation marker. A blanket rule rejects the legitimate deterministic offline-fallback summary and pure-absence summaries; verdict wording is already netted by `three_way` (bidirectional) + assertive-lexicon + dangling-marker reject + output-key allowlist. | No code change. Confirms **D11(i)** (rejected finding stands). Non-verdict marker-less prose remains the documented residual risk described below. |
| **B-3(b) sensitive-category handling — contact PII** — the original D8 (email-only, detect in `pre_process`) never fired end-to-end because S-2 masks email/phone before `execute()`. | **Make it functional** (not "drop to masking-only"): detect on the raw payload at the invoke boundary; keep the framework masking as defense-in-depth. Chosen because the design and the medium-severity review finding both commit to *escalation* (human review of the disputing party's off-platform-contact signal), which masking alone does not provide. | See **D8-bis**. Surfaced to the human as a DECISION (A: make it work — implemented / B: masking-only scope reduction) so the alternative is on record. |

> **Residual (recorded honestly, not a blocker):** `pii.idise_input` / `detect_pii` **fail-open** on
> detector import/runtime failure (returns no tokens rather than crashing ingress). This is a
> *secondary* PII layer — the **framework default S-2 gate is the enforced masking** (it masks
> player/team names to `[MASKED]` *before* pre_process, verified in the live-LLM run). A raw-PII
> leak would require the shared `pii_detector` to be wholly absent, which also disables the
> framework gate (a platform-level failure outside this template's remit). Recorded here as a residual
> risk; hardening to fail-closed here would break minimal-env imports for no added protection while
> the framework gate stands.
>
> **Residual — full-width contact PII (framework-detector gap, D8-ter):** the shared `detect_pii`
> matches ASCII `@` / digits only (no NFKC normalisation), so a **full-width** email/phone
> (`witness＠example．com`) passes the **framework** S-2 gate un-masked into state — independent of this
> template. Our contact-PII *escalation* still fires (`_contains_contact_channel` NFKC-normalises before
> detecting, so the full-width channel IS flagged and the citing issue withheld), and `_mask_pii`
> deliberately mirrors the framework (raw, no NFKC) so the flag's `source_ref` stays equal to the
> framework-un-masked citation ref — NFKC-normalising `_mask_pii` would break that join without removing
> the value the framework already left in state. Fixing the un-masked full-width value at rest is a
> shared detector update, not a template fix.

### Test obligations (proving the contracts above)

- No-rebuttal request (`defendant_rebuttal=""`) succeeds with defendant column `unknown`; a non-`str`
  rebuttal is rejected in pre_process (`status=error`, `execute()` not run in main).
- Version-pin mismatch (`dispute_context.rulebook_version != rulebook.version`) → reject.
- Unknown top-level key / wrong scalar type → reject; garbled free text → `rejected[]`.
- Provenance: a `supports` cell with no citation → downgraded to `unknown`; an existing-but-irrelevant
  `citation_id` and a sub-`min_quote_len` quote → reject.
- **Cross-requirement quote mismatch (high-severity finding):** a multi-requirement clause (≥2 `requirements[]`)
  where a `clause` citation's `verbatim_quote` is verbatim in requirement **R2** but the citation's
  `requirement_id` names **R1** (the quote is absent from R1's `requirement_text` and exceptions) →
  **terminal reject** — the test asserts both the rejection **and** final `status=="error"` /
  `provenance_verified is False` (not a sanitised success with the citation silently dropped).
  Companion locator cases, each a terminal reject: **missing** `requirement_id`; **unknown**
  `requirement_id`; a `requirement_id` belonging to a **different clause** than `source_ref`;
  `source_field` set to an **unknown** value; `source_field=="exception"` with a **missing / out-of-range /
  negative** `exception_index`; `exception_index` supplied together with `source_field=="requirement_text"`;
  and a **non-clause** citation carrying any of `requirement_id`/`source_field`/`exception_index`.
  Positive controls that pass: a quote matching the named requirement's `requirement_text`; a quote
  matching the named `exceptions[exception_index]`; and a citation that omits `source_field` (normalised
  to `requirement_text`) whose quote matches that requirement_text.
- Output blocking: an injected `verdict`/`penalty`/`bracket_change` key → S-3 reject.
- Non-assertion guard fires on `evidence_sufficiency.gaps` / `referee_questions` (side free-text), not
  only `issue_summary`.
- S-4 domain event emitted on reject / upstream-error-passthrough / downgrade paths (PB-1 patches the
  node-module binding to observe the domain emit — framework auto-events are not proof).
- PB-2/PB-5: post-invoke State / checkpoint contain no raw (un-ID-ised) claim text or `pii_map` values.

### Open items (recorded during onboarding in `docs/07`, not design blockers)

- Target-organiser rulebook/version source & version-control policy; evidence-admissibility policy;
  the human adjudicating body (committee vs Tournament Official); dispute operational volume.
  These governance and data premises must be confirmed before introduction; they do not block Design.
  **These onboarding decisions are recorded in the operation guide, which is a blank template today.**
- `clause_match.top_k`, `provenance.min_quote_len`, `input_quality.*`, and any *additions* to
  `output_blocking.prohibited_fields` are config-tunable and calibrated per organiser at onboarding.

> This design specification is the canonical source for the value core, safety boundaries,
> and implementation requirements above.
