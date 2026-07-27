# Decision Pipeline Qualification

**BUJJI Options OS v3 — Engineering Series 38, Sprint 1**

## Status

Deployed. This sprint is **validation only** — it adds zero new
trading logic and modifies none of the seven frozen Trading Brain
modules built across Series 31-37 (Trading Ontology, Evidence
Interpreter, Market State Builder, Strategy Selector, Risk Brain,
Capital Brain, Execution Planner). It lives at
`bujji/trading_brain/qualification/`, and calls each of those modules'
own, unmodified `engine.py` entry points directly.

There is no `taxonomy.py` in this package — per the sprint's own
deliverables list. The small number of finite constants this sprint
needs (`pipeline_status`, stage names) live in `models.py` instead.

## Purpose

Before any broker-facing execution component is built, this harness
proves the complete, broker-independent decision chain behaves as a
single deterministic system: same intelligence in, same decision out,
every replay, with a complete and honest provenance chain from the
final `ExecutionPlan` all the way back to the original published MIC
v2 intelligence.

## Input: One Replayable Intelligence Bundle

`PipelineInput` (models.py) mirrors
`evidence_interpreter/engine.py::interpret()`'s own parameter shape
exactly — the same raw MIC v2 classification strings
(`market_context`, `market_opinion`, `context_stability`,
`calibration`, `governance`, `lifecycle`, `contract`) the Evidence
Interpreter already accepts. This package invents no new input
contract; it reuses the one the frozen Series 32 module already
defines. No broker, live market, execution engine, or order manager is
ever consulted.

## Running the Pipeline

`engine.py::_run_stages()` calls each frozen stage in order, using the
exact same clock for all six calls so a single replay's timestamps are
internally consistent:

```
interpret()  ->  assess() [Market State]  ->  select()
  ->  assess() [Risk]  ->  authorize()  ->  plan()
```

If any stage raises (e.g. an unrecognized classification string), the
chain stops there; `completed_stages` records everything that
succeeded before the failure, and `failed_stage` names where it
stopped. No exception ever escapes `run_qualification()` itself.

## Fingerprinting

`compute_fingerprint()` joins the `id` field of every completed
stage's own output (`interpretation_id`, `assessment_id`,
`decision_id`, `assessment_id`, `decision_id`, `plan_id`, in pipeline
order) and hashes it via `hashlib.md5` — never `uuid4()`. Because every
one of those ids is itself content-derived (each stage's own frozen
`engine.py` builds its id from its inputs, never from `uuid4()` or an
uncontrolled counter), changing any upstream decision necessarily
changes at least one downstream id, and therefore the fingerprint.
Identical inputs, replayed with the same clock, always produce an
identical fingerprint.

## Replay Validation

`run_qualification()` runs the full chain `replay_count` times
(default 10, per the specification's "ten times" requirement) against
the same `PipelineInput` and the same `clock`, then compares every
run's six output objects, its set of completed stages, its failure
point (if any), and its fingerprint for exact equality. `deterministic`
is `True` only if all replays agree on every one of those.

**Determinism depends on the caller supplying a fixed clock.** Every
Trading Brain stage's `clock` parameter defaults to the real wall
clock (the same convention used throughout the Trading Brain since
Series 31) — calling `run_qualification()` with the default clock
means each of the ten replays legitimately sees a different
timestamp, and the harness will correctly report `deterministic=False`
in that case. To validate true replay stability, pass a fixed `clock`
lambda, exactly as this package's own test suite does.

## Provenance Completeness

`_check_provenance()` verifies each stage's own back-reference field
actually names the upstream object that produced it:
`MarketStateAssessment.interpretation_id ==
EvidenceInterpretation.interpretation_id`,
`StrategyDecision.market_state_assessment_id ==
MarketStateAssessment.assessment_id`,
`RiskAssessment.strategy_decision_id == StrategyDecision.decision_id`
(and `.market_state_assessment_id` against the same
`MarketStateAssessment`), `CapitalDecision.risk_assessment_id ==
RiskAssessment.assessment_id`, and
`ExecutionPlan.capital_decision_id`/`.strategy_decision_id` against
their own respective sources. Any mismatch is reported as a
`MISSING_PROVENANCE:<edge>` warning — never silently ignored.

## Confidence Monotonicity

`_check_confidence_monotonic()` does **not** treat the six stages as
one flat line. The Strategy Selector and the Risk Brain each derive
their own confidence independently from the *same* Market State
Builder output — they are siblings, not a chain — so this check
compares confidence only along the pipeline's true dependency edges:

```
Evidence Interpreter -> Market State Builder
Market State Builder -> Strategy Selector
Market State Builder -> Risk Brain
Risk Brain -> Capital Brain
Capital Brain -> Execution Planner
```

using the same finite confidence ordering (`UNKNOWN < VERY_LOW < LOW <
MODERATE < HIGH < VERY_HIGH`) every downstream module already reuses
from the frozen Trading Ontology. Any edge where confidence rises is
reported as a `CONFIDENCE_INCREASE:<edge>` warning.

## Propagation Checks

`_check_propagation()` verifies that a genuinely absent strategy,
denied risk, or denied capital are never silently dropped on the way
downstream: no selected strategy must produce a Risk Brain
`blocking_reason` of `NO_STRATEGY`; a Risk Brain `DENY` must produce a
Capital Brain `allocation_status` of `DENIED`; a `DENIED` capital
allocation must produce an Execution Planner `status` of
`NOT_PLANNED`. Any break in that chain is reported as an
`UNEXPECTED_PROPAGATION:<reason>` warning.

## Pipeline Status

| Status | When |
|---|---|
| `FAILED` | A stage raised an exception, or replay was not deterministic. |
| `COMPLETE_WITH_WARNINGS` | All six stages ran and replay was deterministic, but at least one provenance/confidence/propagation warning was raised. |
| `COMPLETE` | All six stages ran, replay was deterministic, and there are zero warnings. |
| `UNKNOWN` | Declared for taxonomy completeness; not produced by this sprint's own logic (every real run resolves to one of the three statuses above). |

## Validation Trace

`validation_trace` names the completed stages, the failure point (if
any), the determinism verdict, every warning raised, and the final
pipeline status — built entirely from data this function itself
computed, nothing hidden.

## Journaling

`bujji/journal/decision_pipeline_qualification_journal.py::DecisionPipelineQualificationJournal`
— append-only JSONL, own schema (`schema_version` field), independent
of every other journal in the codebase.

## Query API

`qualification/query.py::QualificationIndex` mirrors the established
`*Index` pattern: `ingest()`, then read-only `latest()`, `history()`,
`find_by_id()`, `find_by_status()`, `find_by_fingerprint()`,
`summary()`. No mutation method beyond `ingest`.

## What This Sprint Explicitly Does Not Build

Per the specification's own explicit exclusions, this sprint builds no
Execution Engine, no Broker Adapter, places no orders, calculates no
lots, connects to no broker (FYERS, Zerodha, or otherwise), manages no
positions, monitors no fills, performs no live trading, and modifies
none of the seven existing Trading Brain modules it validates.

## Isolation Guarantees

- No import of any broker SDK, execution engine, order manager, MIC
  v2, live feed, or market data module anywhere in this package —
  only the frozen stage `engine.py` modules and their own frozen
  output models.
- No mutation of any Trading Brain module: every call here is a plain
  function call into an existing, unmodified `engine.py`.
- `run_qualification()` and every check function it calls are pure
  apart from the injectable clock threaded through to the underlying
  stages; every id is `hashlib.md5`-derived, never `uuid4()`.

**The Decision Pipeline Qualification harness proves the broker-independent
Trading Brain, taken as a whole, produces the same decision, for the
same intelligence, with the same reasoning, the same journals, and the
same fingerprint, on every single replay — the precondition the
constitution sets before any execution-facing component may be
built.**
