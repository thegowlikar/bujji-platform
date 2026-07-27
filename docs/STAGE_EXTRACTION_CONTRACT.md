# Stage Extraction Contract — Production Engineering Sprints 1-6

Audits the current state of the Decision Pipeline's seven stages after
five sprints of incremental, zero-behavioral-change refactoring. Every
sprint's own regression comparison report and replay-diff validation
(see `docs/REGRESSION_COMPARISON_REPORT.md`, and the per-sprint chat
history) backs the claims below — this document is a summary, not a new
claim of correctness.

## Classification

| Stage | Status | Module | Rationale |
|---|---|---|---|
| 1. Market Observation | **Pure-extracted** (Sprint 3) | `market_observation.py::classify_candle()` | Classification is a function of two timestamps + an int; side effects (counters, logging) stay in the orchestrator. |
| 2. Market Intelligence | **Wrapped-in-place** (Sprint 1) | `_update_intelligence()` in `orchestrator.py` | Inherently broker-I/O-bound (real bid/ask, option chain, VIX fetches); already isolated by its own try/except and documented as "observation-only, never affects trading logic." Forcing a pure-function split here would separate nothing meaningful from the I/O it exists to perform. |
| 3. Strategy Evaluation | **Partially extracted** (Sprint 5) | `strategy_evaluation.py::evaluate_entry_gate()` | The is_trade/max-trades/clock-trust *gate* is pure-extracted. The ORB-readiness transition and the `_enter()` call + its five-way exception handling remain in the orchestrator — genuinely FSM-coupled, not a gating decision. |
| 4. Risk Validation | **Wrapped-in-place** (Sprint 1) | `order_planning.plan_straddle()` (pre-dates this sprint series) | Calls two broker contract-resolution round-trips and the Capital Management Engine's `approve_trade()` — async I/O throughout, already its own module with a single clear responsibility. No further pure-function boundary exists inside it without splitting resolution from approval, which would change nothing about testability (both already require an event loop and injected callables). |
| 5. Execution Decision | **Pure-extracted** (Sprint 2) | `execution_decision.py::build_execution_decision()` | `DecisionSnapshot`/`ExecutionPlan` construction is pure data assembly — the first and cleanest extraction in this series. |
| 6. Order Dispatch | **Wrapped-in-place, now with full coverage** (Sprint 1 entry side, Sprint 6 exit side) | `_enter()` + `_do_exit()`/`_exit_leg()` in `orchestrator.py` | Real broker order submission — inherently I/O, not a pure-function candidate. Sprint 6 closed a real gap: exit-side dispatch had **zero** stage instrumentation before this sprint (Sprint 1 only wrapped the entry side). Both sides are now observed. |
| 7. Journal Recording | **Pure-extracted (trade half)** (Sprint 4); decision half not extracted | `journal_recording.py::build_trade_record()` | `TradeRecord` construction is pure data assembly, extracted. The decision half (`self._decision_journal.record(snapshot)`) is a single one-line persistence call with nothing to extract — already minimal. |

## Summary

- **3 of 7 stages** have an independently unit-testable pure-function core: Market Observation, Execution Decision, Journal Recording (trade half). Strategy Evaluation is partially there (its gate is extracted; its FSM-coupled dispatch is not).
- **3 of 7 stages** are legitimately I/O-bound and remain wrapped-in-place: Market Intelligence, Risk Validation, Order Dispatch. This is not unfinished work — it is the correct end state for stages whose entire job is talking to a broker or aggregating broker data. Forcing these into "pure functions" would not increase testability; it would just move the same I/O behind a different name.
- **Observability coverage is now complete** across all seven stages, on both the entry and exit code paths, including EOD square-off — verified by `tests/test_exit_order_dispatch_observability.py`.

## Resolved limitation (Sprint 7)

The exit-side `order_dispatch` stage previously reported `outcome="ok"`
even when `_exit_leg()` returned `False` (a business-logic leg-flatten
failure) rather than raising. Sprint 7 closed this gap: `stage()` now
yields a `StageHandle` with `mark_failed(reason)`, called at the exact
point `_do_exit()` already decided the exit was incomplete. This changes
ONLY the logged `outcome` field (now `"failed"` instead of `"ok"`) --
the `return False` / early-return control flow, and therefore every
trading decision downstream of it, is byte-for-byte unchanged (verified
by the same replay-diff methodology as every other sprint in this
series). Every pre-existing `with stage(...):` call site (without
`as handle`) is unaffected -- the extension is opt-in and backward-
compatible.
