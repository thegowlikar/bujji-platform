# End-to-End Replay Qualification

**BUJJI Options OS v3 — Engineering Series 46, Sprint 1**

## Status

Deployed. This sprint is **verification only** — it adds zero new
trading logic and modifies none of the twelve frozen pipeline modules
(Series 32–45) it drives end to end. It lives at
`bujji/qualification/` (inside the app's own `bujji` package — a
distinct location from the pre-existing `/opt/bujji/qualification/`
baseline-fingerprint directory this project has maintained since
Campaign 1; the two do not conflict).

## Replay Architecture

`replay_runner.py::run_replay()` drives the complete pipeline against
one `ReplayScenario` — a frozen bundle of every external input the
chain needs (MIC-shape classification strings, a NIFTY spot snapshot,
an option chain snapshot, a capital policy, a lot specification, a
sizing config, an execution policy, a trading configuration). It calls
each of the twelve stages in **true dependency order**, not the
specification's own illustrative diagram order:

```
evidence_interpreter → market_state_builder → strategy_selector →
risk_brain → capital_brain → execution_planner → execution_engine →
broker_adapter → nifty_contract_builder → position_sizing →
order_construction → runtime_execution
```

The specification's own pipeline diagram interleaves the NIFTY
Contract Builder / Position Sizing / Order Construction / Runtime
Execution Orchestrator *after* the Broker Adapter. This replay runner
does not reproduce that literal ordering, because it is not the actual
dependency graph: the NIFTY Contract Builder (Series 42) consumes
`StrategyDecision` + `CapitalDecision` directly, never the Broker
Adapter's output (see Series 42's own architecture doc). Running
stages in their true dependency order — rather than the diagram's
illustrative one — is itself part of proving the pipeline behaves as
actually designed, not as loosely sketched.

Every stage call is a direct, unmodified call into that stage's own
frozen `engine.py`. No stage is reimplemented, wrapped, or bypassed.

## The Paper Executor Stub

`PaperExecutorStub` is the qualification-only stand-in for the
diagram's final "Paper Executor Stub" box. It satisfies
`runtime_execution.engine.ExecutionEngineInterface`'s
`submit_and_confirm(order_request)` shape, records every call it
receives, and returns `None` — deterministic, no randomness, no I/O.
It is **not** a copy of, and never imports, production's own
`bujji.broker.paper.PaperBroker` — this package's own scope is
"qualification utilities only, no production logic," and this stub
exists solely to let the Runtime Execution Orchestrator's `dispatch()`
step reach a terminal state (`DISPATCHED` or `ABORTED`) during replay.

## Replay Assumptions

- All market data is historical or synthetic, supplied by the caller
  via `ReplayScenario` — this package fetches nothing itself.
- `PaperExecutorStub` stands in for any real broker interaction;
  nothing in this package can reach a live broker even if misconfigured.
- Determinism requires the caller to supply a **fixed clock** — every
  underlying stage's own `clock` parameter defaults to the real wall
  clock (the established convention since Series 31), so replaying
  with the default clock will honestly report `deterministic=False`
  because each call legitimately observes a different timestamp. This
  mirrors the same caveat documented in Series 38's Decision Pipeline
  Qualification.
- Performance measurements (`elapsed_seconds`, `stage_timings`) use
  `time.perf_counter()`, which is explicitly exempted from the
  "no system clock dependency" invariant: it is diagnostic metadata
  only, never part of any artifact's identity, and is excluded from
  every determinism/equality comparison this package performs.

## Replay Limitations

- This package validates the chain from Evidence Interpreter through
  Runtime Execution Orchestrator. It does not validate MIC v2 itself
  (frozen, out of scope) or any real Broker Adapter → Broker SDK →
  Exchange interaction (out of scope by design — no such component may
  exist in this package).
- Chain-consistency validation (`_check_chain_consistency()`) only
  runs when every one of the twelve stages completes successfully; a
  scenario that fails partway through has no further chain to validate
  beyond "did it fail atomically and cleanly," which is checked
  separately (see Negative Qualification below).
- `PaperExecutorStub` never simulates a fill, a rejection, or a partial
  fill — those production-side outcomes remain entirely
  `bujji.broker.PaperBroker`'s (or `bujji.execution.engine.ExecutionEngine`'s)
  concern, never this package's.

## Qualification Methodology

For each `ReplayScenario`, `run_replay()`:

1. Runs the full chain `replay_count` times (default 3) against the
   same scenario and the same clock.
2. Compares every run's completed-stage set, failure point, artifact
   equality, and fingerprint for exact agreement — `deterministic` is
   `True` only if every replay agrees on all four.
3. If every stage completed, runs `_check_chain_consistency()` —
   verifying each stage's own back-reference field actually names the
   upstream artifact that produced it, that the constructed strategy
   matches the Strategy Selector's own decision, that the sized
   contracts match the NIFTY Contract Builder's output, and that the
   final `ExecutionSession`'s orders match the Order Construction
   Service's own requests.
4. Records artifact counts (contracts produced, orders produced,
   stages completed) and per-stage timings, informational only.

## Acceptance Criteria

A scenario **passes** qualification when `failed_stage is None` and
`chain_valid is True`. A qualification **session** (a set of scenarios)
is considered ready for the next phase (per the Series 41 roadmap) only
when every scenario passes, every scenario's `deterministic` flag is
`True` under a fixed clock, and every negative scenario fails at
exactly its expected stage with zero fabricated downstream artifacts.

## Deliverable 5/6: Artifact Chain and Pipeline Consistency

`_check_chain_consistency()` verifies, for a fully-completed run:

| Link | Check |
|---|---|
| Market State ← Evidence | `market_state_builder.interpretation_id == evidence_interpreter.interpretation_id` |
| Strategy ← Market State | `strategy_selector.market_state_assessment_id == market_state_builder.assessment_id` |
| Risk ← Strategy | `risk_brain.strategy_decision_id == strategy_selector.decision_id` |
| Capital ← Risk | `capital_brain.risk_assessment_id == risk_brain.assessment_id` |
| Execution Plan ← Capital | `execution_planner.capital_decision_id == capital_brain.decision_id` |
| Execution Instructions ← Plan | `execution_engine.plan_id == execution_planner.plan_id` |
| Broker Request ← Instructions | `broker_adapter.instruction_set_id == execution_engine.instruction_set_id` |
| Contracts match Strategy | `nifty_contract_builder.strategy_id == strategy_selector.selected_strategy` |
| Sizing matches Capital Intent | `position_sizing.capital_decision_id == capital_brain.decision_id` and `capital_intent` values match |
| Sizing matches Contracts | `position_sizing.contracts == nifty_contract_builder.contracts` |
| Orders ← Position Plan | `order_construction.position_plan_id == position_sizing.plan_id` |
| Session matches Orders | `runtime_execution.order_requests == order_construction.requests` |

Any violation is reported as a warning string on the `ReplayRunResult`
and sets `chain_valid = False` — never silently passed.

## Deliverable 7: Negative Qualification

Named negative scenarios and their expected failure point. Three run
through the full twelve-stage `run_replay()`; three are exercised
directly at the relevant stage's own `engine.py`, because the
specific artifact combination they require does not arise naturally
from any Evidence-Interpreter-shaped input (a disclosed limitation,
not an oversight — see below the table):

| Scenario | How it's exercised | Expected `failed_stage` / outcome |
|---|---|---|
| All Evidence Interpreter inputs `None` (insufficient market data) | Full replay | `nifty_contract_builder` (`INSUFFICIENT_DATA`) — Market State Builder resolves `INSUFFICIENT_EVIDENCE`, Strategy Selector honestly returns `NO_STRATEGY`, and the Contract Builder correctly refuses to build a contract set for no selected strategy |
| Missing option chain | Full replay (real strategy selected, `option_chain=None`) | `nifty_contract_builder` (`MISSING_OPTION_CHAIN`) |
| Invalid capital policy | Full replay (`CapitalPolicy.policy` unrecognized) | `position_sizing` (`INVALID_CONFIGURATION`) |
| Unknown/untemplated strategy | Direct call to `nifty_contract_builder.engine.build_contracts()` with a `StrategyDecision` naming e.g. `LONG_STRADDLE` | `UNKNOWN_STRATEGY` |
| Empty position plan | Direct call to `order_construction.engine.construct_orders()` with a `PositionPlan` carrying zero contracts | `EMPTY_POSITION_PLAN` |
| Duplicate client order IDs | Direct call to `runtime_execution.engine.build_session()` with two `OrderRequest`s sharing a `client_order_id` | `DUPLICATE_CLIENT_ORDER_ID` |

**An honest note on the three direct-call cases**: reaching them
through the full pipeline would require, for example, a
`CapitalDecision.capital_intent == NONE` while `StrategyDecision`
still names a real strategy — but Capital Brain's own policy (Series
36) only ever produces `NONE` when Risk Brain denies approval, and
Risk Brain's own policy (Series 35) only ever denies approval when the
market character is `UNCERTAIN` or `MIXED`, both of which force
`StrategyDecision.selected_strategy` to `None` at the Strategy
Selector one layer earlier. That specific combination is structurally
unreachable from real upstream data — exactly the kind of disclosed,
by-design limitation this project has documented at every layer since
MIC v2's first sprint, not a gap silently assumed away.

Every negative scenario — whether run through the full replay or
called directly — is checked to fail **atomically**: for the
full-replay cases, `completed_stages` stops exactly at the last stage
that succeeded and zero artifacts exist for anything after it; for the
direct-call cases, the failing stage's own result carries zero
constructed contracts/orders/dispatch instructions.

## Deliverable 8: Performance (Informational Only)

`ReplayRunResult.elapsed_seconds` and `.stage_timings` record wall-time
via `time.perf_counter()`. `.artifact_counts` records the number of
completed stages, contracts produced, and order requests produced.
None of these figures inform any decision, comparison, or pass/fail
determination beyond determinism itself (which compares artifacts, not
timings) — no optimization is performed or implied by this sprint.

## Deliverable 9: Qualification Report

`replay_report.py::build_qualification_report()` produces a
`QualificationReport` summarizing a set of `ReplayRunResult`s:
scenario counts (total/passed/failed/deterministic/chain-valid) and
the invariant results from `replay_statistics.py::compute_invariants()`.
Given the same run results and the same clock, always byte-identical.

## Deliverable 10: Production Readiness Assessment

**Proven this sprint** (once qualification passes consistently):
- The complete decision chain (Evidence Interpreter → Runtime
  Execution Orchestrator) is deterministic, replayable, and internally
  consistent under historical/synthetic data and paper execution only.
- Every artifact's provenance chain is verifiable end to end.
- Failures propagate atomically with zero fabricated downstream
  artifacts, for every negative scenario exercised.

**Remaining operational work** (per the Series 41 roadmap, unchanged
by this sprint): authentication, session management, token refresh,
real order submission, order status polling, fill reconciliation,
circuit breakers, rate limiting, and the first live broker interaction.

**Known limitations**:
- Qualification here uses synthetic/historical `ReplayScenario`s
  constructed by this package's own tests, not a corpus of genuine
  historical FYERS market sessions — building that corpus is separate
  future work.
- `PaperExecutorStub` cannot exercise `bujji.execution.engine.ExecutionEngine`'s
  own retry/idempotency/reconciliation logic (Series 41's own audit
  findings) — only production's real `PaperBroker`, wired in via
  `ExecutionEngineInterface`, can do that, and that wiring is
  explicitly future work, not this sprint's.

**Explicit "not yet implemented" items**: circuit breaker, rate
limiter, cross-restart idempotency-cache persistence, and FYERS
order-placement path verification (all identified as gaps in the
Series 41 audit and still open).

## Required Tests Covered

Complete successful replay, replay determinism, repeated replay byte
identity, negative scenarios (six named cases above), artifact
integrity (chain consistency checks), journal integrity (append-only,
round-trip), immutable models (frozen dataclasses throughout), no
broker SDK imports, no network, no authentication, no live execution.

## Isolation Guarantees

- No import of a broker SDK, `bujji.broker`, `bujji.execution`'s
  concrete `ExecutionEngine`, FYERS, or Zerodha anywhere in this
  package — only the frozen stage `engine.py` modules and their own
  frozen models.
- No retries, no circuit breakers, no rate limiting — this sprint
  explicitly does not implement any of the three, per its own scope.
- `run_replay()` and every function in this package are pure apart
  from the injectable clock and diagnostic `time.perf_counter()` calls;
  every id is `hashlib.md5`-derived, never `uuid4()`.

**This replay qualification proves the complete deterministic decision
pipeline behaves correctly, deterministically, and repeatably under
historical replay and paper execution — the precondition the project
sets, per the Series 41 roadmap, before introducing operational
infrastructure (authentication, broker connectivity, retries, circuit
breakers) and, eventually, the first live broker interaction.**
