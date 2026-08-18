# Phase 19.13 — Live Intelligence Data Bridge

## Objective

Connect the production daily runtime (Phase 19.11/19.12's `DailySessionRuntime`) with the
existing live market observation and Intelligence Foundation pipeline (Phase 18/19.0–19.9).
Integration only — no changes to strategy logic, execution logic, broker order code, or the risk
engine.

## Audit finding: most of the flow already existed

Before writing anything, the existing codebase was read in full. Phase 19.10.1's
`intelligence_pipeline_adapter.build_intelligence_heartbeat_cycle()` already composes the entire
required chain — `MarketRealitySnapshot → MarketIntelligenceSnapshot → DecisionContext →
DecisionIntelligenceSnapshot → MarketPhenomenaAssessment → MarketStateNode →
MarketEnvironmentAssessment` — and Phase 19.10.2's `reality_translator.py`/`cycle_artifact.py`
already bridge a live `MarketSnapshot` into that composition and persist a fingerprinted result.
`ShadowSessionRunner._run_intelligence_pipeline_step()` already wires all of it together against
a real broker — but entangled with that runner's own watchlist/quote-observation machinery, which
`DailySessionRuntime` has no use for.

Phase 19.13's actual, genuinely new work was therefore: (1) a standalone version of that wiring
usable by the daily runtime without the unrelated machinery, (2) the completeness gate the task
explicitly asked for (which did not exist as a standalone check), (3) full-payload artifact
storage (the existing cycle artifact only stores fingerprints, by design — task 4 asked for the
full reality/intelligence/phenomena/state/health content), (4) a reusable LIVE vs REPLAY
equivalence validator, and (5) wiring `run_daily_intelligence_session.py`'s `intelligence_fn` to
all of the above against a real, order-disabled broker connection.

## What was built

### 1. `bujji/shadow_runtime/completeness_gate.py` — Task 5

`evaluate_completeness_gate(reality_snapshot) -> CompletenessGateResult`. Sits **before**
`build_intelligence_heartbeat_cycle()` is ever called. Deliberately does **not** gate on
`completeness == COMPLETENESS_PARTIAL` — `reality_translator.py` always marks a live single-tick
snapshot `PARTIAL` by honest design (a point observation is never a full day's OHLC), so gating on
that would refuse every live cycle unconditionally. Instead it refuses composition when the
reality snapshot has no spot, no options chain, or `completeness == COMPLETENESS_EMPTY` — the two
genuinely disqualifying conditions `IntelligencePipelineAdapterError` already treats as "nothing
to compose from" deep inside composition, checked earlier so a caller gets a structured,
honest reason instead of a generic exception.

### 2. `bujji/shadow_runtime/daily_intelligence_artifact.py` — Task 4

`DailyIntelligenceArtifact` / `build_daily_intelligence_artifact()` / persisted via the same
`EventStore` (Phase 15B) every durable record in this project already uses. Stores the **full
real payload** of each layer via each object's own already-existing `to_dict()` — reality,
intelligence, decision intelligence, phenomena, market state, environment — not just fingerprints
(`ShadowIntelligenceCycleArtifact` from Phase 19.10.2 already covers fingerprint-only identity/
replay-proof needs and is reused unmodified alongside this new artifact, not replaced by it).
`completeness_gate_passed` is a first-class field so a reader can distinguish a real, gate-passed
cycle from one that never should have been (and, by construction, never is) recorded as confident.

### 3. `bujji/shadow_runtime/replay_equivalence.py` — Task 3

`validate_live_replay_equivalence()` runs the same `build_intelligence_heartbeat_cycle()` twice
against identical inputs — once `execution_mode=LIVE`, once `HISTORICAL_REPLAY` — and compares
exactly the three things the task named: intelligence fingerprint
(`MarketIntelligenceSnapshot.intelligence_snapshot_id`, which already deliberately excludes
`execution_mode` from its own fingerprint payload — confirmed by reading `models.py` directly),
environment classification, and `DecisionIntelligenceSnapshot.recommended_posture`. This
formalizes, as a reusable callable, the same proof technique Phase 19.10.1's own real-data
validation already used ad hoc.

### 4. `bujji/shadow_runtime/live_intelligence_cycle.py` — Task 1 + Task 2

`run_live_intelligence_cycle()` — the authoritative live cycle function:

```
MarketDataAdapter.build_snapshot() + fetch_spot_candles()
  → translate_market_snapshot_to_reality_snapshot()          (Phase 19.10.2, unmodified)
  → evaluate_completeness_gate()                              (Phase 19.13, new)
  → build_intelligence_heartbeat_cycle()                      (Phase 19.10.1, unmodified)
  → build_cycle_artifact() + build_daily_intelligence_artifact()
```

Every cycle carries: a real session/`as_of_time` timestamp (never wall-clock — caller-injected
`clock`), observation lineage (the reality payload's own real content, plus
`reality_snapshot_reference`/fingerprints threaded through `IntelligenceContext`), an intelligence
fingerprint (`intelligence_snapshot_id`), and a health status (`runtime_health_status`, passed
through to both artifact types). Never raises — every failure mode (broker/adapter IO error,
reality translation failure, completeness gate refusal, composition error) is caught and returned
as a structured `LiveIntelligenceCycleResult`, matching the "never raises" discipline every
Phase 19.x runtime component already follows.

### 5. `run_daily_intelligence_session.py` — wiring

`_make_real_intelligence_fn()` now constructs a real broker using the **exact same** pattern
`run_live_shadow.py` already uses in production: `disable_live_execution(FyersBroker(cfg, log))`
(`bujji.broker.guard`) — wrapped **before** the broker is ever touched, so `place_order`/
`modify_order`/`cancel_order`/position/margin/funds calls remain structurally unreachable. If
`FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` are unset, it fails with an honest error rather than silently
skipping the cycle. One cycle per daily invocation (this entrypoint's own established
granularity) — intraday multi-cycle observation remains `ShadowSessionRunner`'s separate
responsibility, untouched. Two new CLI flags, `--cycle-artifact-store-path` and
`--daily-artifact-store-path`, control where the two artifact types persist (defaults under
`data/`); `deploy/bujji-daily-intelligence.service`'s `ExecStart` updated to pass them explicitly.

## Scope boundary — verified, not assumed

AST-level import checks (this session's established discipline — naive substring scans have
repeatedly false-positived on files' own docstrings in earlier phases) confirm zero
`order`/`position`/`strategy`/`execution` imports in all four new modules. The one file that
legitimately imports broker modules for read-only market data
(`run_daily_intelligence_session.py`) is checked precisely instead: an AST walk over every
`ast.Attribute` node confirms no `.place_order(`/`.modify_order(`/`.cancel_order(` call is
reachable anywhere in its source, and `disable_live_execution` is confirmed present — proving the
broker is wrapped before use, not just described as wrapped. Phase 19.12's own equivalent test was
narrowed the same way (removing the blanket "broker" substring ban, which was correct for that
infrastructure-only phase but would have wrongly blocked this phase's legitimate read-only market
access) — the precise call-level check replaces it as the real safety guarantee.

## Verification performed

| Requirement | Test / evidence |
|---|---|
| Real production-shaped data end-to-end | `test_production_shaped_data_flows_end_to_end` — full chain via `run_live_intelligence_cycle` against a production-shaped `FakeBroker` (same fixture pattern as `tests/test_shadow_runtime_intelligence_loop.py`); asserts session timestamp, lineage, fingerprint, health status, and all five preserved full payloads present. |
| LIVE vs REPLAY equivalence | `test_live_replay_equivalence_same_fingerprint_classification_posture` — same fingerprint, classification, and decision posture across both modes from identical inputs. |
| Incomplete data handling | `test_completeness_gate_fails_closed_on_empty_reality`, `test_completeness_gate_fails_closed_on_missing_options`, `test_completeness_gate_passes_on_real_complete_data`, `test_live_cycle_never_composes_intelligence_when_gate_fails` (options chain genuinely emptied via the same monkeypatch technique the existing `MarketDataAdapter` test suite uses — confirms `build_intelligence_heartbeat_cycle` is never reached and nothing is persisted), `test_live_cycle_reports_honest_failure_when_spot_missing`. |
| Restart recovery | `test_restart_recovery_hydrates_prior_process_artifacts` — a fresh `EventStore` instance (simulating a genuinely restarted process, no shared in-memory state) hydrates the prior process's already-persisted artifact correctly. |
| No trading-decision imports / no order calls | `test_no_trading_decision_imports` (4 new files) + `test_run_daily_intelligence_session_never_places_orders` (precise AST call check + `disable_live_execution` presence). |

**Test results**: 13/13 new Phase 19.13 tests pass. Phase 19.12's own test suite re-verified
after the narrowed boundary check: 12/12 still pass. Full regression: see below.

## Known, now-resolved limitation

Phase 19.11/19.12's disclosed limitation — "`intelligence_fn` not wired to a real broker" — is
resolved by this phase. The daily runtime, once the systemd service is installed (still the
operator's own action per Phase 19.12's confirmed decision), will now compose one real live
intelligence cycle per day against real FYERS market data, gated by completeness, with full
LIVE/REPLAY equivalence proven and full-payload artifacts persisted for later inspection.
