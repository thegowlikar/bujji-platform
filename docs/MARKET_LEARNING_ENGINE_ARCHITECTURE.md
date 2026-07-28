# Market Learning Engine (MLE) — Series 100, Phase 1.0 Architecture

## What this is

MLE is an **evidence engine**, not a learning or adaptive system. It
converts real trading experience — sourced from `bujji.msi_decision_auditor`
(Series 99)'s real `DecisionOutcomePair` records — into disclosed,
traceable **Knowledge Candidates**. Phase 1.0 delivers the infrastructure
only: the data model, lifecycle manager, evidence accumulator, and
Trading Knowledge Base storage/query layer. It does **not** yet contain
any analysis logic that automatically classifies market days or detects
missed opportunities — that is explicitly out of scope for Phase 1.0's
10 deliverables (package structure, Knowledge Candidate model, Trading
Knowledge Base, evidence accumulator, lifecycle manager, isolation
verification, journal integration, replay compatibility, golden replay
tests, this document) and is a future, separately-reviewed phase.

## First principle

> MLE IS NOT A LEARNING ENGINE. MLE IS AN EVIDENCE ENGINE. Learning
> belongs to engineers. Production belongs to deterministic code.

Every Knowledge Candidate is inert data. Nothing in this package ever
calls a Production decision function, places an order, or writes to
anything Production reads. The only path out of MLE is a human reading
`bujji.msi_market_learning.query`'s output and, at their own discretion,
opening a new, ordinary engineering sprint — the same discipline this
project has used since Series 108.

## Package structure (Deliverable 1)

`bujji/msi_market_learning/`, following this project's established
9-file MSI house convention:

| File | Responsibility |
|---|---|
| `taxonomy.py` | Plain string constants: 12-stage lifecycle (with its forward-only ordering as the enforcement contract), 5-tier evidence ladder, consistency/decay states. |
| `config.py` | Declarative, non-tunable thresholds: the 1/5/20/75/200 occurrence-count tier ladder, decay-ratio constants. Never fit to real trading outcomes. |
| `models.py` | Frozen dataclasses: `KnowledgeCandidate`, `EvidenceDimensions`, `LifecycleTransition`, `KnowledgeCandidateExplanation`. |
| `engine.py` | Pure functions only — `new_candidate`, `record_occurrence`, `compute_evidence_tier`, `advance_lifecycle`, `assess_decay`. Zero IO, zero Production imports (structurally verified, see below). |
| `serialization.py` | Dict/JSON round-trip, house convention. |
| `journal.py` | `KnowledgeBaseJournal` — append-only JSONL, mirrors `bujji.live_shadow_operator.journal.OperatorJournal` exactly (same `failed_writes` tracking). |
| `query.py` | Read-only lookups: `by_lifecycle_stage`, `by_evidence_tier`, `engineering_candidates`, `trace_ancestry`. This is the real Trading Knowledge Base read surface (Deliverable 3). |

## Knowledge Candidate model (Deliverable 2)

Every field the Series 100 spec requires is present: `candidate_id`
(deterministic md5, never uuid4/wall-clock), `observation`, `reasoning`,
`supporting_evidence` (real Series 99 ids — the traceability chain),
`earliest_causal_timestamp`, `counterfactual_analysis` (always `None` in
Phase 1.0 — the field exists so the schema never needs to change once a
Counterfactual Engine is built in a later phase), `replay_support`,
`consistency`, `evidence_strength`, `evidence_dimensions` (the five real
dimensions: repeatability, consistency, causal validity, replay support,
market diversity — never collapsed into one blended score),
`implementation_status`, `lifecycle_history` (append-only), and
`occurrence_count`.

## Evidence accumulator (Deliverable 4)

The exact 1/5/20/75/200 occurrence-count ladder specified, implemented as
a declarative lookup (`compute_evidence_tier`) — never tuned. A single
occurrence is always `TIER_NONE` (test-verified: `test_a_single_
occurrence_is_never_evidence`). `market_diversity` only increments when
a genuinely new market-day classification is observed for the same
pattern, tracked via a disclosed marker rather than a hidden set.

## Knowledge lifecycle manager (Deliverable 5)

`taxonomy.ALL_LIFECYCLE_STAGES`' declared order **is** the enforcement
contract: `advance_lifecycle` raises `ValueError` on any attempt to skip
a stage (test-verified: `test_lifecycle_cannot_skip_a_stage`). Two
disclosed exceptions exist: `ACTIVE` may transition directly to
`DECAYING` or `ARCHIVED`, and `DECAYING` may recover back to `ACTIVE` —
real, expected end states, not skipped stages. `lifecycle_history` is
append-only; no transition is ever overwritten.

## Isolation verification (Deliverable 6)

`tests/test_mle_isolation.py`, mirroring the AST-based precedent already
established for Shadow Safety in `tests/test_live_shadow_operator.py`:

1. `test_no_production_module_imports_mle` — walks every real `.py` file
   under `bujji/` (excluding this package itself) and fails if any
   imports `bujji.msi_market_learning`. This is the literal enforcement
   of the spec's "permanently forbidden" MLE→Production path.
2. `test_mle_never_imports_production_decision_or_execution_packages` —
   the reverse check: MLE itself never imports from `bujji.broker`,
   `bujji.execution`, the Selector, Portfolio Construction, Margin
   Bridge, Execution Planning, `live_pipeline_bridge`, `live_shadow_
   validation`, `app.py`, or `fyers_apiv3`.
3. `test_mle_never_references_order_placing_functions` — AST-level check
   for `place_order`/`submit_and_confirm`/`modify_order`/`cancel_order`
   anywhere in this package's source.
4. `test_mle_has_no_io_side_effects_in_engine_module` — `engine.py`
   contains no `open`/`requests`/`socket` calls; only `journal.py` is
   permitted to touch disk.

These run as part of the normal `pytest` suite — this project's existing
regression gate **is** the CI enforcement the spec calls for; a red test
run is already this project's standing hard stop, so no separate tooling
was introduced.

## Learning Journal integration (Deliverable 7)

`KnowledgeBaseJournal` persists each candidate's full current state on
every real write, append-only, with a `failed_writes` counter (mirrors
`OperatorJournal`'s Sprint-112 hardening — a write failure is tracked,
never silently swallowed; test-verified). Traceability back to Series 99
is real: `KnowledgeCandidate.supporting_evidence` stores real
`bujji.msi_decision_auditor` `decision_id`/`outcome_id`/`pair_id` values,
and `query.trace_ancestry` returns them directly — the concrete
implementation of the spec's `Rule ← Knowledge Object ← Evidence ←
Historical Decisions` chain.

## Replay compatibility (Deliverable 8)

Verified two ways:
1. **Structurally** — the isolation tests above prove MLE cannot be
   reached from any Production call path.
2. **Behaviourally, on the real corpus** — the full 41-day real replay
   (`/tmp/sprint120_replay.py`, the same script used throughout Sprints
   115–122) was re-run with `bujji.msi_market_learning` present in the
   codebase. Result: **zero diffs** across thesis, conviction, selected
   family, construction type, portfolio approval state, margin, and both
   real assessment IDs, on every one of the 41 real days, compared
   against the immediately-prior baseline capture (Sprint 122's
   post-restoration verification run).

## Golden replay tests (Deliverable 9)

`tests/test_msi_market_learning.py::test_golden_replay_mle_construction_
does_not_touch_production_state` — constructs a real `DecisionRecord` via
`bujji.msi_decision_auditor.engine.build_decision_record`, then
constructs a real `KnowledgeCandidate` citing that record's real
`decision_id`, then re-derives the same `DecisionRecord` a second time
and asserts byte-identical equality — a concrete, executable proof that
MLE construction has zero effect on Production's own real record-building
function. `test_golden_replay_two_identical_days_produce_deterministic_
candidate` proves determinism: identical real inputs always produce a
byte-identical candidate.

## What Phase 1.0 deliberately does NOT include

- No automated classification of market days (`RANGE`/`TREND`/etc.) — the
  `market_classification` parameter to `new_candidate`/`record_occurrence`
  is caller-supplied, not derived by this package.
- No Missed Opportunity Detector — the A–E bucket classification from the
  broader mission spec is analysis logic, not infrastructure, and is
  explicitly excluded from this sprint's 10 deliverables.
- No Counterfactual Engine — `counterfactual_analysis` exists as a field
  on `KnowledgeCandidate` (so the schema is forward-compatible) but is
  always `None` in Phase 1.0. Building the engine that invokes real
  Production decision functions to run a counterfactual is a materially
  higher-risk, separately-reviewed future phase, per this sprint's own
  explicit warning: "When implementation is complete, STOP. Do not begin
  Phase 2."

## Verification summary

- Regression: **2874/2874 passing** (2840 pre-existing + 34 new MLE
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.
- Isolation: verified both structurally (AST) and behaviourally (golden
  replay), enforced continuously via the standing pytest gate.
