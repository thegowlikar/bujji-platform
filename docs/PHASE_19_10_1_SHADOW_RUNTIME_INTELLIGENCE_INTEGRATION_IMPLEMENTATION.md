# Phase 19.10.1 — Shadow Runtime Intelligence Integration Implementation

## Objective

Turn `bujji.shadow_runtime` into Bujji's Market Intelligence heartbeat, per Phase 19.10.0's audit and the
user's own sub-phase plan. **Extends the existing package — no new runtime, no scheduler, no daemon.**

## Files changed

**New, in `bujji/shadow_runtime/`:**
- `lifecycle.py` — `RuntimeStage`, `RuntimeLifecycle`, `LifecycleTransition`
- `intelligence_pipeline_adapter.py` — `build_intelligence_heartbeat_cycle()`, `IntelligenceHeartbeatCycle`
- `health.py` — `RuntimeHealth`, `write_health_heartbeat()`, `read_health_heartbeat()`, `health_for_stage()`

**Modified (additive only):**
- `shadow_session_runner.py` — added `execution_mode`, `health_path`, `session_date` constructor
  parameters (all optional, all default to prior behavior); wired lifecycle/health tracking into
  `start()`; fixed the one hardcoded `EXECUTION_MODE_LIVE`.

**New test file:** `tests/test_shadow_runtime_intelligence_integration.py` (13 tests).

## Architecture

```
                  Shadow Runtime (extended, not replaced)

                         |
        +----------------+----------------+
        |                |                |
        v                v                v

 (existing quote      Intelligence      Health Monitor
  observation loop,    Pipeline          (health.py, NEW)
  unmodified)           Adapter
                        (NEW)
        |                |
        v                v

QuoteObservationStore  MarketRealitySnapshot (caller-supplied)
(existing, unmodified)      |
                             v
                     6 brains (unmodified) -> MarketIntelligenceSnapshot
                             |
                             v
                     DecisionContext -> DecisionIntelligenceSnapshot
                             |
                             v
                     MarketPhenomenaAssessment -> MarketStateNode
                             |
                             v
                     MarketEnvironmentAssessment
                             |
                             v
                     IntelligenceHeartbeatCycle (bundles all 6, returned to caller)
```

## 1. Preserved Boundary — verified, not just claimed

Zero broker/order/execution/strategy imports in any of the 3 new files (structural, AST-verified by test).
`shadow_session_runner.py`'s own pre-existing boundary (no `place_order`/`modify_order`/`cancel_order`/
`get_open_positions` call anywhere) is re-verified by test after the additive edit — same AST-attribute
check, not a naive substring scan (a naive scan would trip on the file's own module docstring, which
already names all four methods to explain it never calls them — caught and avoided this time, having made
exactly that mistake in earlier phases this session).

## 2. Runtime Lifecycle Model

```
INITIALIZING -> WAITING_FOR_SESSION -> COLLECTING -> PROCESSING_INTELLIGENCE -> FINALIZING -> COMPLETED
                                                                                             -> FAILED
```

`RuntimeLifecycle` is immutable — `advance()` returns a new instance, never mutates in place (same
discipline this project has followed since Phase 18.x). Every transition is validated against an explicit,
documented table (`IllegalLifecycleTransition` raised otherwise) and is deterministic/serializable
(`to_dict()`).

**A real bug caught by this phase's own real-caller smoke test, not just unit tests**: the first version of
the transition table only allowed `FINALIZING` from `PROCESSING_INTELLIGENCE`. But
`ShadowSessionRunner.start()`'s own `finally` block always builds the session artifact — "finalizes" —
regardless of which stage a real failure interrupted (a startup validation error never even reaches
`COLLECTING`). Running the actual `ShadowSessionRunner` end-to-end against a deliberately-broken fake
broker raised `IllegalLifecycleTransition: WAITING_FOR_SESSION -> FINALIZING is not a legal transition`
immediately. Fixed by making `FINALIZING` reachable from every non-terminal stage, matching the real code
path rather than an idealized one. This is exactly why the smoke test (§7) was run against the real
`ShadowSessionRunner`, not only the standalone `lifecycle.py` unit tests.

## 3. Intelligence Pipeline Adapter — thin composition, verified against real production data

`build_intelligence_heartbeat_cycle()` composes an already-real `MarketRealitySnapshot` + a window of real
spot candles into all six Phase 19.0–19.9 objects, in order. It:
- Calculates nothing a brain doesn't already calculate — ATM strike selection reuses
  `bujji.intelligence.volatility_policy.select_atm_strike()` verbatim (Phase 19.2.2's own extraction of the
  real `option_chain_adapter.py` precedent), never reimplemented.
- Never modifies a brain, never builds a second snapshot type.
- Never imports `bujji.market_perception.intelligence_adapter` — Phase 19.10.0's own confirmed naming
  collision is avoided by construction, not just by convention.

**Verified against real production data**, not only synthetic fixtures: ran the adapter against the real
`historical_observations.db`'s 2026-08-14 session (670,163-row store, unmodified, read-only) —
`build_market_reality_snapshot('2026-08-14', ..., resolution='FIVE_MINUTE', as_of_time='2026-08-14T15:35:00+05:30')`
produced a real spot close of 24,366.0, 2,190 real option contracts, and a real VIX of 11.27. Feeding that
through the adapter with 75 real 5-minute candles produced:

```
regime: RANGING
decision posture: FAVOR_PREMIUM_ENVIRONMENT
environment: PREMIUM_SELLING_FAVOURABLE
```

— and, critically, **LIVE and HISTORICAL_REPLAY execution modes produced byte-identical fingerprints at
every one of the five downstream identity layers** (`MarketIntelligenceSnapshot`,
`DecisionIntelligenceSnapshot`, `MarketPhenomenaAssessment`, `MarketStateNode`,
`MarketEnvironmentAssessment`), run against this real session. This is the single strongest piece of
evidence in this phase: the full chain, against real market data, is genuinely mode-agnostic.

## 4. Execution Mode Propagation — fixed

The one `IntelligenceContext` construction site inside `shadow_session_runner.py` (the monitoring-pair
liquidity call) previously hardcoded `execution_mode=EXECUTION_MODE_LIVE`. Now reads
`self._execution_mode`, set from a new, optional constructor parameter defaulting to `EXECUTION_MODE_LIVE`
— every pre-existing caller/test is unaffected (confirmed: all 102 pre-existing `shadow_runtime`/
`intelligence_cycle_recorder` tests still pass unchanged).

## 5. Live Health Heartbeat — verified updating during execution, not only after

`health.py`'s `write_health_heartbeat()` overwrites (atomic write-then-`os.replace`, never a torn read) a
small JSON file on every real lifecycle transition — wired into `shadow_session_runner.py` at
`INITIALIZING`, `WAITING_FOR_SESSION`, `COLLECTING` (plus once per completed cycle inside the loop),
`PROCESSING_INTELLIGENCE`, `FINALIZING`, and the terminal `COMPLETED`/`FAILED`. Verified two ways:

1. Unit test: writing all 6 stages in sequence and reading back after each write confirms the file
   reflects each real stage, not only the last one.
2. **Real-caller proof**: ran the actual `ShadowSessionRunner` (with a fake broker) through a full 2-cycle
   session with `health_path` set. First run (deliberately broken mock) produced
   `{"status": "FAILED", "current_stage": "FAILED", "last_error": "unexpected_failure: ..."}`  — the real
   exception message, not a generic one. Second run (correctly-shaped mock) produced
   `{"status": "COMPLETED", "current_stage": "COMPLETED", "last_error": null}` with the real lifecycle
   trace `INITIALIZING -> WAITING_FOR_SESSION -> COLLECTING -> PROCESSING_INTELLIGENCE -> FINALIZING -> COMPLETED`.

Both off by default (`health_path=None`) — zero behavior change for any caller that doesn't opt in.

## 6. Persistence Boundary — preserved

`intelligence_cycle.jsonl` (the pre-existing, `market_perception`-based session log) is untouched by this
phase — still session-scoped logging, still not Phase 19.5's `MarketMemoryEntry`. This phase's new
`IntelligenceHeartbeatCycle` is returned to the caller, not persisted by the adapter itself — persisting it
into `MarketMemoryEntry`/`record_market_memory()` remains the CALLER's responsibility (a future daily
driver, not this phase), consistent with "only compose" and "do not duplicate Market Understanding Memory."

## 7. Testing

`tests/test_shadow_runtime_intelligence_integration.py`, 13 tests, all passing, proving the 6 required
properties (synthetic, isolated fixtures only — never the production DB in the automated test suite, per
this project's own standing discipline since the Phase 18.12 incident; the production-data proof in §3 was
a manual, read-only, one-off verification, not committed as an automated test):

1. **Lifecycle transitions work** — 4 tests: legal sequence succeeds; `FAILED` reachable from every
   non-terminal stage; an illegal transition raises; the model is deterministic/serializable.
2. **Replay mode produces deterministic output** — same inputs → identical `IntelligenceHeartbeatCycle`.
3. **LIVE and HISTORICAL_REPLAY use identical intelligence fingerprints** — verified at all five identity
   layers, both in the automated test (synthetic data) and manually (real 2026-08-14 data, §3).
4. **No broker/execution/strategy imports** — 2 tests: AST import scan across the 3 new files; AST
   attribute-call scan re-confirming `shadow_session_runner.py`'s pre-existing boundary held.
5. **Health heartbeat updates during processing** — 3 tests: all 6 stages individually observable via the
   file; status correctly derived per stage; atomic write leaves no torn temp file — plus the real-caller
   proof in §5.
6. **Full pipeline smoke test** — 2 tests: Reality → Intelligence → Decision → Phenomena → State Graph →
   Environment produces a fully-linked, non-empty identity chain; the adapter refuses to run (raises
   `IntelligencePipelineAdapterError`) when `reality_snapshot.spot` is `None`, never silently fabricating.

One test-authoring bug caught and fixed during this phase (same class of mistake made in earlier phases
this session, caught faster this time): a naive substring check for `place_order`/etc. tripped on
`shadow_session_runner.py`'s own module docstring, which already names those methods to explain their
absence. Fixed with an AST-level attribute-call check.

## Full regression

Baseline before this phase: 5,870 passed (post Phase 19.9). After Phase 19.10.1's additions: **5,883
passed, 0 failed** — exactly the 13 new tests. 3 new files + 1 additively-modified existing file + 1 new
test file; all 102 pre-existing `shadow_runtime`/`intelligence_cycle_recorder` tests re-verified passing
unchanged.

## Known limitations

- No entry point/CLI/driver script was created this phase — per explicit scope, that remains later work.
- No scheduler, daemon, cron, or systemd integration — explicitly out of scope, confirmed absent.
- The intelligence pipeline adapter is not yet called from anywhere inside `_run_market_perception_step()`
  or `_run_one_cycle()` — it exists as a real, tested, standalone composition function a future driver can
  call; wiring it into the runner's own per-cycle loop (vs. a caller invoking it directly against
  `HistoricalObservationStore`) is a real design decision left open, not resolved here.
- `PROCESSING_INTELLIGENCE` is currently a single lifecycle transition wrapping the entire collection loop
  (since today's runner interleaves quote collection and any intelligence work within the same per-cycle
  loop, not as a separate post-collection phase) — an honest, documented simplification of the requested
  6-state model, not a fabricated finer-grained separation the current runner doesn't actually have.
- No live-broker verification was performed — all proof in this phase is against a fake broker (unit/smoke
  tests) or read-only historical data (§3); real-broker behavior remains unverified by this phase.

## Final verdict

Bujji now has one clean intelligence heartbeat path — `Market Data -> Reality -> Understanding -> Memory (caller-invoked) -> Health` — proven twice: once end-to-end against real 2026-08-14 production data (LIVE == HISTORICAL_REPLAY at every layer), and once end-to-end through the real `ShadowSessionRunner` itself (lifecycle + health heartbeat, both failure and success paths). Per the user's own plan: ready for a Shadow Market Intelligence Campaign across 20–30 NSE sessions before any strategy/execution intelligence is added — that campaign itself is a live-observation exercise for the user to run, not something this phase attempted.
