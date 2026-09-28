# Test Specification — SVC-C2-134

## Test Strategy
- Test types: Unit / Integration / Proof-of-Boundary.
- Focus: the S-3 Provenance contract (verbatim + requirement-level locator +
  bidirectional coverage), the structural adjudication-boundary (output blocking),
  input validation (allowlist / version-pin / quality), and the Cat 2 GraphNode
  end-to-end invoke. Business-logic *accuracy* requires domain-specific evaluation and
  is not asserted here.

## Test file inventory

| File | Covers |
|------|--------|
| `tests/unit/test_input_gate.py` | Input contract: allowlist, type/shape, unique ids, version-pin, quality gate, non-JSON body |
| `tests/unit/test_provenance.py` | S-3 Provenance: verbatim substring, min_quote_len, requirement-level locator (missing/unknown/mismatched/shape), exception locator, non-clause-carrying-locator, dangling refs, prohibited field, assertive lexicon, coverage downgrade, gate_check, **output-key allowlist reject (top/issue/citation) + escalation-key allow (D10)**, **unhashable clause source_ref → terminal not crash** |
| `tests/unit/test_clause_matcher.py` | Deterministic clause mapping, ascending/tie-break, top_k, implicit requirement, hybrid boost |
| `tests/unit/test_config_validation.py` | Config defaults valid, out-of-range rejected at compile, prohibited floor non-removable/additive, **bool rejected for int keys (D10)** |
| `tests/unit/test_output_guard.py` | Prohibited floor, phrase-based assertive lexicon (question does not trip), markers |
| `tests/unit/test_pre_process_node.py` | Validation → validated_input, empty/invalid reject, gate leniency on absent input, no-rebuttal default, 要配慮 flagging (health/harassment/contact email+phone via typed detector), match-log/masked-handle non-flag, **`sensitive_flags_from_raw` raw-payload contact detection + bad-payload tolerance (D8-bis)** |
| `tests/unit/test_issue_draft_node.py` | Deterministic issue segmentation, no-rebuttal ⇒ defendant unknown |
| `tests/integration/test_graph_invoke.py` | Full outer-graph invoke (GraphNode inner workflow): success (normal dispute NOT withheld — masked-handle regression guard), deterministic fallback, invalid reject, 要配慮 issue withheld while request succeeds + non-sensitive issue returned, **contact-PII (email + phone) issue withheld + escalated end-to-end (D8-bis)**, **caller-supplied `input_context` flags cannot spoof escalation (D8-bis)**, cross-requirement mismatch ⇒ terminal status=error |
| `tests/integration/test_startup.py` | Server import compiles the graph and config.yaml reaches the nodes; TrustLevel enum not lowercased; **/invoke route driven end-to-end (D10): authed success, bad-token 401, unauthenticated → S-1 trust-gate denies (status=error)** |
| `tests/proof_of_boundary/test_pb1_audit_events.py` | PB-1 domain audit event per node (module-binding patch — framework auto-events excluded); **+ reject path (provenance_rejected) and GraphNode upstream-error passthrough emit (D10)** |
| `tests/proof_of_boundary/test_pb2_pb5_state_safety.py` | PB-2 primitives-only output; PB-5 no checkpointer in prod + latest checkpoint clean (deserialised) + pii_map isolation + **pii_map absent from EVERY checkpoint (D10)** |
| `tests/proof_of_boundary/test_import_isolation.py` | PB-4 import isolation (framework boundary test) |
| `tests/proof_of_boundary/test_pb_invoke_order.py` | PB-6 invoke execution order (framework boundary test) |
| `tests/proof_of_boundary/test_state_safety.py` | PB-2/PB-5 state file AST scan (framework boundary test) |
| `tests/proof_of_boundary/test_pb7_hitl_interrupt_propagation.py` | PB-7 (auto-skipped — non-HITL) |
| `tests/unit/test_framework_compliance_tc06_tc07.py` | TC-06/07 @final gate override raises TypeError (framework compliance test) |

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | No Pydantic/dataclass — `src/schemas/state.py` primitives only | PASS |
| TC-02 | SecurityViolationError / reject on invalid input | Invalid input → `status=error`, `rejected[]` (test_input_gate, test_pre_process_node) | PASS |
| TC-03 | No JWT/Credential in State | CI の credential スキャン: 0 violations | PASS (CI) |
| TC-04 | InvocationContext via configurable only | No `InvocationContext(...)` in nodes; `from_state` used by framework | PASS |
| TC-05 | S-4: no duplicate lifecycle events in execute() | Only domain events emitted in execute() | PASS |
| TC-06 | S-2 `_security_gate_input()` not overridden | TypeError if overridden (framework compliance test) | PASS |
| TC-07 | S-3 `_security_gate_output()` not overridden | TypeError if overridden (framework compliance test) | PASS |
| TC-08 | `required_trust_level` enforced | Insufficient trust → refused (PB-6 sets trust; nodes declare VERIFIED_EXTERNAL) | PASS |
| TC-09 | `_extra_security_gate_input()` non-trivial | Allowlist/version-pin/quality reject executes (test_pre_process_node) | PASS |
| TC-10 | `_extra_security_gate_output()` non-trivial | Provenance gate_check executes (test_provenance) | PASS |
| TC-11 | ≥1 domain `emit_trace_event()` per execute() | PB-1 proves per node | PASS |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Result |
|-------|----------|--------|
| PB-1 | BaseNode → AuditLogger — domain event per node (framework events excluded) | PASS |
| PB-2 | State serialization — post-invoke output primitives only | PASS |
| PB-3 | L2 → external service | N/A — no external service; LLM is an injected seam (deterministic fallback when absent). Live-LLM smoke below covers the real-client path. |
| PB-4 | Import isolation — no Level 0 imports | PASS (boundary test + CI AST scan) |
| PB-5 | Checkpoint safety — no raw/pii_map; prod checkpointer-less | PASS |
| PB-6 | Invoke execution order | PASS (boundary test) |
| PB-7 | HITL interrupt propagation | Auto-waived — non-HITL (`hitl.enabled: false`) |

## Live-LLM smoke (push-time, not in CI)

A tailored live check injects a real `BaseLLM`
via `src.services.llm_seam.set_llm` and runs one real invoke on the sample dispute.
(The generic live-check harness assumes a constructor-injected
`llm=`/`kb=` and a `kind/degradation` output envelope; this template uses a
module-level seam and a `formatted_output` envelope, so the equivalent check is run
by hand.) Recorded here:

- Date: 2026-08-04 · LLM: an Azure OpenAI model as an injected `BaseLLM`.
- Result: **1 LLM call · status=success · 2 issues · `provenance_verified=True` ·
  6 citations · enriched (non-`unknown` three_way cells present) · no withholding.**
  The prompt (which embeds the exact output schema + the fixed candidate structure)
  elicited real, provenance-valid structured output — not the always-on deterministic
  fallback and not an empty `{}` (the MunicipalContractVariationEvidenceBriefAgent empty-schema class of defect does not
  occur here). The strict provenance gate remains fail-closed: a real client that
  *paraphrases* (non-substring quote) would yield a *terminal reject* (status=error),
  which is the intended safety behaviour, not a bug.
- **Re-run 2026-08-05 (D8-bis)** · LLM: an Azure OpenAI model, injected `BaseLLM`.
  Two real invokes through the `llm_seam` path: **NORMAL dispute → status=success · 2
  issues · `provenance_verified=True` · non-empty (not degraded) · `escalation_required==[]`**;
  **PHONE-in-accuser dispute → status=success · 2 issues · `escalation_required==['issue_C-INPUT-01']`**
  (contact-PII withholding fires end-to-end with a real LLM, confirming D8-bis through
  the full stack — invoke-boundary detection → withhold → escalate).

## Test Execution Summary
- Executed via `python -m pytest tests/ -v` (CI `run-tests`) + `pytest -q` at repo root.
- Local CI (`work/scripts/local_ci.py`) + `ruff check src tests` + live-LLM smoke run
  pre-push; the results are summarised above.

## 実 LLM 1 回転検証

モックだけで固めた実装は実 LLM を繋いだ瞬間に動かないことがあるため、注入シームへ実 LLM クライアントを刺して 1 回 invoke し、**中身のある出力が出ること**を確認している。

| 項目 | 内容 |
|---|---|
| 実施日 | 2026-08-18 |
| 使用 LLM | Azure OpenAI のモデルを `BaseLLM` 実装として注入 |
| 注入シーム | `live_llm_injector` |
| 実 LLM 呼び出し | 1 回 |
| 出力 | ['rulebook_version', 'provenance_verified', 'issues'] が非空。縮退理由なし |

本番の LLM は注入されたクライアントで、これは検証用シーム。テンプレの `src/` は変更していない（一時テストを生成→実行→削除する方式）。CI には入れていない（外部モデルの鍵が要るため）。
