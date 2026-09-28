# SVC-C2-134 — Esports Tournament Dispute Adjudication Support Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: SVC

## Overview

This agent gives an esports tournament referee read-only decision support on a match dispute. It
takes the accuser's assertion, the opposing team's rebuttal, the referee-supplied match facts and
a version-pinned rulebook, and returns a per-issue breakdown: what each source supports, what it
contradicts, what stays unresolved, which rulebook clauses might apply, whether the evidence is
sufficient, and which questions a referee still needs to answer.

It suits protest review where the work is reconciling three accounts against a rule text: a
tournament official triaging a queue of protests, a committee that needs each observation traced
to a quoted source, a referee who wants the evidence gaps named before ruling.

It does **not** decide disputes, determine match results, impose penalties, or change brackets.
Every ruling stays with an authorised human official — and that boundary is structural, not a
matter of prompt wording:

- **Verdict-shaped fields cannot be emitted.** `verdict`, `ruling`, `penalty`, `sanction`,
  `bracket_change`, `eligibility`, `disqualification`, `winner`, `loser` and
  `points_adjustment` are blocked. The block list in configuration is **additive only** — the
  code holds a non-removable floor of the same keys, so an operator cannot open the adjudication
  boundary by editing a file.
- **The output shape is an allowlist at three levels.** Top level, per issue, and per citation.
  A key that is not on the list is rejected, so unguarded assertive prose cannot ride out in an
  unexpected field.
- **A claim without a source is not a claim.** A supports/contradicts position without a citation
  is downgraded to `unknown`, and a citation whose quote is not verbatim in the named source is a
  terminal reject rather than a softened answer.
- **The rulebook is pinned.** The version named in the dispute context must equal the version of
  the rulebook supplied; no wall-clock or random input is read, so the same dispute yields the
  same analysis.
- **Sensitive content withholds the issue.** Contact details beyond a player handle, and other
  sensitive categories, are detected on the raw payload before masking and flagged; an issue
  citing such a source is withheld and escalated for manual handling rather than published.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

Provide:

- an AgentCore runtime;
- a `BaseLLM` implementation, injected through `src.services.llm_seam.set_llm()`. This is a
  module-level seam rather than a constructor argument, because every node must be no-argument
  constructible. The template holds no model id and no provider client in `src/`, and it declares
  no startup secret — the injected client owns its own credentials. With no client injected the
  agent still runs, degrading to a deterministic analysis that says a referee must review it;
- caller authentication in front of the entry adapter.

Runtime parameters in `config/config.yaml`:

| Key | Meaning |
|---|---|
| `llm.provider` / `llm.model` / `llm.temperature` (`0.0`) / `llm.max_tokens` (`4000`) | Declared model settings. They are range- and type-checked at compile, but **nothing in this repository reads them to build a client** — whichever runtime injects the `BaseLLM` decides the model. Treat them as the deployment's recorded intent; temperature 0 is deliberate for adjudication support |
| `clause_match.top_k` (`8`), `clause_match.hybrid_search` (`true`) | Candidate clauses surfaced per issue. Clause ids are code-like, so hybrid search is on; score ties break by ascending `clause_id` for reproducibility |
| `input_quality.max_bad_char_ratio` (`0.10`), `input_quality.max_field_chars` (`20000`) | Free-text quality gate |
| `provenance.min_quote_len` (`12`) | Verbatim quote floor — guards a vacuous empty quote |
| `provenance.require_citation_for_supports_contradicts` (`true`) | An uncited position becomes `unknown` |
| `output_blocking.prohibited_fields` | Merged **onto** the hardcoded floor; entries can be added, never removed |
| `max_retry` (`3`), `timeout_s` (`60`), `budget_usd` (`1.50`) | Execution bounds; the budget is advisory |
| `memory_enabled` (`false`), `hitl.enabled` (`false`) | No cross-invocation memory and no in-graph pause: the human review happens after the call, on the returned analysis |

Every accepted key has a type and a range, validated at graph compile; an out-of-range value is
rejected fail-closed and an unknown key is ignored.

The template does not ingest match video or replays, does not talk to a tournament platform, and
does not maintain its own rulebook — the rulebook is supplied on every call.

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent is designed to run on the platform, which supplies the entry adapter, the authentication middleware and the language-model client. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and the agent degrades deliberately rather than
refusing to start. The standalone server in `src/api/server.py` starts without the platform — the
manifest declares no required secrets — loads `config/config.yaml` and passes it to the graph. It
injects no language model, so the analysis comes back deterministic: the reconciliation falls
back to a non-assertive "requires referee review" result rather than an invented one. Nothing is
persisted; no checkpointer is configured. The test suite runs without a platform connection.

## Input

`POST /invoke` takes `input` — a **JSON string** carrying the dispute bundle — plus a
`session_id`. The bundle's top level is a closed allowlist:

| Field | Contents |
|---|---|
| `accuser_assertion` | The protesting side's claim |
| `defendant_rebuttal` | The opposing side's response (may be empty) |
| `third_party_facts` | `{fact_id, text, source_ref}` entries supplied by the referee |
| `rulebook` | `{version, clauses[]}`, each clause `{clause_id, requirements[]}`, each requirement `{requirement_id, requirement_text, exceptions[]}` |
| `dispute_context` | `{match_id, protest_id, rulebook_version}` |

`dispute_context.rulebook_version` must equal `rulebook.version`. An unknown key at any level, a
wrong type, a duplicate id, a field over the length ceiling, or free text failing the quality
ratio is rejected before analysis begins, with the offending field named in `rejected[]`.

Callers must be authenticated. Trust established by upstream middleware is used as-is; when
`INVOKE_AUTH_TOKEN` is set on the server environment, an otherwise-anonymous caller must present
it as `Authorization: Bearer` or receive **401**. When the token is not set and no middleware
vouched for the caller, the request reaches the graph as `ANONYMOUS` and the S-1 trust gate
refuses it — every node requires `VERIFIED_EXTERNAL`.

```bash
curl -X POST http://127.0.0.1:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{
    "input": "{\"accuser_assertion\": \"Team Alpha asserts Team Bravo used a prohibited software macro in round three.\", \"defendant_rebuttal\": \"Team Bravo states the inputs were performed manually.\", \"third_party_facts\": [{\"fact_id\": \"F1\", \"text\": \"The match log shows a 12-input burst within 180 milliseconds at round three.\", \"source_ref\": \"matchlog\"}], \"rulebook\": {\"version\": \"2026.1\", \"clauses\": [{\"clause_id\": \"C-INPUT-01\", \"requirements\": [{\"requirement_id\": \"R-INPUT-01a\", \"requirement_text\": \"Players must not use hardware or software macros that automate multiple inputs from a single action.\", \"exceptions\": [\"Accessibility remapping approved in advance.\"]}]}]}, \"dispute_context\": {\"match_id\": \"M-1\", \"protest_id\": \"P-1\", \"rulebook_version\": \"2026.1\"}}",
    "session_id": "example-session"
  }'
```

Caller-supplied `input_context` keys are dropped rather than forwarded: the agent consumes
nothing from them, and forwarding them would put caller data into state verbatim.

## Output

The analysis is returned under a five-key top level: `rulebook_version`, `provenance_verified`,
`issues`, `unresolved_notes` and `escalation_required`.

Each issue carries `issue_id`, `issue_summary`, `clause_refs`, `three_way`,
`candidate_rule_applicability`, `evidence_sufficiency`, `referee_questions`, `citations` and
`manual_escalation_required`. Each citation carries `citation_id`, `source_type`, `source_ref`,
`requirement_id`, `source_field`, `exception_index` and `verbatim_quote`.

```json
{
  "rulebook_version": "2026.1",
  "provenance_verified": true,
  "issues": [
    {
      "issue_id": "issue_C-INPUT-01",
      "issue_summary": "The macro allegation turns on whether the input burst was automated.",
      "clause_refs": ["C-INPUT-01"],
      "three_way": [
        {"party": "accuser", "position": "supports", "citation_id": "c1"},
        {"party": "defendant", "position": "contradicts", "citation_id": "c2"}
      ],
      "evidence_sufficiency": "insufficient",
      "referee_questions": ["Was an accessibility remapping approved in advance?"],
      "citations": [
        {"citation_id": "c1", "source_type": "clause", "source_ref": "C-INPUT-01",
         "requirement_id": "R-INPUT-01a", "source_field": "requirement_text",
         "verbatim_quote": "must not use hardware or software macros"}
      ],
      "manual_escalation_required": false
    }
  ],
  "unresolved_notes": [],
  "escalation_required": []
}
```

Values above are illustrative; real output depends on the dispute and the rulebook supplied.

## Security and Limitations

Every node requires verified-external trust. Player personal data is replaced with stable tokens
before analysis, and the token-to-original map lives only in the pre-processing node's local
scope — it is never written to state and never reaches a checkpoint. Sensitive categories are
detected on the raw payload at the one pre-masking seam, and the flags threaded onward carry no
personal data and cannot be supplied by a caller. On the way out, the output gate enforces the
key allowlists, the citation-provenance rules and the prohibited-field floor.

Known limits, recorded rather than hidden:

- **Prose without a citation marker is a residual risk.** An `issue_summary` is not required to
  carry an inline marker — mandating one would reject the legitimate deterministic fallback
  summary and pure-absence summaries. Verdict wording is caught by the three-way guard, the
  assertive-vocabulary check, the dangling-marker reject and the output allowlist; a
  non-verdict marker-less assertion is not.
- **Prompt wording is not regression-testable in CI.** Asserting a prompt string is a constant
  assertion. The CI-side defence is the output gate, not the prompt.
- **A verbatim quote proves the words exist in the named source, not that they support the
  claim.**
- **A calendar date can look like a phone number.** Date-shaped values are excluded from contact
  detection on purpose: a false withholding is treated as worse than a missed escalation.
- **Onboarding decisions are outstanding.** The rulebook source and version-control policy, the
  evidence-admissibility policy, and which body adjudicates must be settled per organiser before
  use; several configuration values are calibrated at that point.

Output is machine-generated and assistive. It supports a referee's review; it does not replace
it, and no part of it is a ruling.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2) with an inner workflow subgraph
- Generation mode: `llm`, with a deterministic fallback when no model is injected
- HITL: not enabled in-graph — the referee reviews the returned analysis after the call
- Persistence: none — no checkpointer is configured
- External API clients: deployment-injected through the module-level seam; none bundled

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

Tests run without a platform connection, using a fake language model through the same injection
seam production uses. Running the agent against a bare `uvicorn src.api.server:app` starts and
answers deterministically, since the standalone adapter injects no model.

## Documentation

`docs/02_design.md` carries the node flow, the state schema, the input and output contracts, the
provenance rules and the security design, together with the decision record and the points where
the implementation refined the design. `docs/03_test_spec.md` records the test specification and
the verification results, including live-model runs.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
