# Phase 19.10.2 — Shadow Runtime Intelligence Loop Integration

## Objective

Wire Phase 19.10.1's intelligence pipeline adapter into `ShadowSessionRunner`'s existing cycle loop, so
every shadow observation cycle automatically flows through the Intelligence Foundation and produces a
durable cycle artifact. Integration only — no redesign, no new runtime, no strategy/execution logic.

## Audit before coding

Confirmed before writing anything:
- `ShadowSessionRunner._run_market_perception_step()` already fetches a `MarketSnapshot` (live spot/vix/
  option-chain/futures) and real spot candles every cycle when `market_perception_enabled`/
  `intelligence_cycle_enabled` are on — this phase reuses that SAME already-fetched data, no new broker call.
- `bujji.state_persistence.store.EventStore`/`PersistedEvent` (Phase 15B) is the existing, generic
  persistence primitive every durable record in this project already uses — reused directly again, no new
  mechanism.
- The health/lifecycle system Phase 19.10.1 built (`lifecycle.py`, `health.py`, already wired into
  `start()`) is reused as-is for failure visibility — no second health mechanism.
- A real, working `FakeBroker`/`make_runner` test fixture already existed
  (`tests/test_shadow_runtime_recovery.py`) — reused for this phase's own smoke tests rather than building
  a second, divergent fake broker.

## Architecture

```
Observation Cycle (existing, unmodified)
        |
        v
market_perception_enabled step (existing, unmodified) -- builds MarketSnapshot + candles
        |
        v
intelligence_pipeline_enabled step (NEW, additive, off by default)
        |
        v
reality_translator.translate_market_snapshot_to_reality_snapshot()  -- MarketSnapshot -> MarketRealitySnapshot
        |
        v
intelligence_pipeline_adapter.build_intelligence_heartbeat_cycle()  (Phase 19.10.1, unmodified)
        |
        v
cycle_artifact.build_cycle_artifact() -> ShadowIntelligenceCycleArtifact
        |
        v
cycle_artifact.record_cycle_artifact() -> EventStore (existing primitive, reused)
```

## Files changed

**New, in `bujji/shadow_runtime/`:**
- `reality_translator.py` — `translate_market_snapshot_to_reality_snapshot()`
- `cycle_artifact.py` — `ShadowIntelligenceCycleArtifact`, `ConfidenceSummary`, `build_cycle_artifact()`,
  `record_cycle_artifact()`, `record_cycle_failure()`, `hydrate_cycle_artifacts()`

**Modified (additive only):** `shadow_session_runner.py` — new optional constructor parameters
(`intelligence_pipeline_enabled=False`, `intelligence_pipeline_event_store_path=None`), new
`_run_intelligence_pipeline_step()` method, one new call site inside `_run_market_perception_step()`.

**New test file:** `tests/test_shadow_runtime_intelligence_loop.py` (10 tests).

## 1. Why this insertion point

Inside `_run_market_perception_step()`, immediately after the existing `intelligence_cycle_enabled` block,
reusing the SAME `snapshot`/`candles` already in scope. Considered and rejected: a separate top-level call
in `_run_one_cycle()` — rejected because it would require re-fetching or re-threading the same data a
second time, duplicating what `_run_market_perception_step()` already produces. This keeps the runner as
pure orchestrator: it never constructs a brain, a `MarketIntelligenceSnapshot`, or any Phase 19.x object
itself — it only calls the already-real adapter and records the already-real result.

## 2. Reality translation — one real, honest limitation

`market_perception.models.MarketSnapshot`'s own `SpotSnapshot` carries only `ltp` (a single live tick),
never a bar's open/high/low — unlike `market_reality_snapshot.models.SpotSnapshot`, which the intelligence
adapter requires. `reality_translator.py` sets `open == high == low == close == ltp` — **not an
approximation of a missing bar**, but the mathematically correct OHLC for a zero-duration, single-instant
observation. Stated explicitly in the module's own docstring rather than silently glossed over.
`completeness` is always set to `PARTIAL` for this reason — a single live tick is honestly never a complete
day's view.

## 3. Strict boundaries — verified structurally

AST-level import scan across both new files confirms zero imports of anything broker/order/position/
strategy-shaped (`broker`, `fyers`, `execution_engine`, `msi_strategy_selector`,
`msi_strategy_selection_foundation`, `msi_trade_construction`, `position_lifecycle`,
`position_management`). A second AST-level attribute-call scan re-confirms `shadow_session_runner.py`'s
pre-existing boundary (`place_order`/`modify_order`/`cancel_order`/`get_open_positions`) held after the
wiring — an AST check, not a naive substring scan, since this file's own module docstring already names
those methods to explain their absence (the exact false-positive already caught and fixed in Phase
19.10.1's own test suite).

## 4. `ShadowIntelligenceCycleArtifact` — deliberately distinct from existing identities

Named and scoped to avoid the three collisions the phase spec itself named:
- Not `DatasetArtifact` (Phase 18.12 — a certified historical dataset publication).
- Not `MarketIntelligenceSnapshot` (Phase 19.3 — the intelligence composition itself, only referenced here
  by its own real `intelligence_snapshot_fingerprint`).
- Not `MarketMemoryEntry`/Market Understanding Memory (Phase 19.5 — permanent cross-session memory; this
  artifact is session-scoped "what the runtime produced this cycle," and does not attempt to be memory).

All 12 required fields present (`cycle_id`, `session_id`, `as_of_time`, `execution_mode`,
`reality_snapshot_fingerprint`, `intelligence_snapshot_fingerprint`, `decision_intelligence_fingerprint`,
`detected_phenomena`, `market_state_transition`, `environment_classification`, `confidence_summary`,
`runtime_health_status`). `fingerprint_payload()` excludes only `cycle_artifact_id` itself
(self-referential) — no `created_at`/process-identifier field exists anywhere on the object to exclude in
the first place, since every other field is real content, not runtime metadata.

## 5. Replay Determinism Proof — verified through the real runner, not only unit fixtures

Ran the actual `ShadowSessionRunner` twice, once with `execution_mode=LIVE` and once with
`execution_mode=HISTORICAL_REPLAY`, against the same production-shaped `FakeBroker` fixture (2 cycles
each):

```
LIVE errors: ()
REPLAY errors: ()
LIVE cycle artifacts: 2
REPLAY cycle artifacts: 2
intelligence_snapshot_fingerprint match: True
environment match: True
```

Both `intelligence_snapshot_fingerprint` and `environment_classification` were byte-identical across modes
for both cycles — confirmed live, then re-verified as an automated test
(`test_live_and_replay_produce_identical_cycle_artifacts`). Only `execution_mode` itself differed, exactly
as required.

## 6. Failure Handling — verified visible, never silent

A genuinely missing spot tick (the real condition `RealityTranslationError` exists for) is:
1. Recorded in the session artifact's `errors` tuple (`"intelligence_pipeline_failed: ..."`).
2. Recorded as a real, separate `SHADOW_INTELLIGENCE_CYCLE_FAILED` event in the `EventStore` — a disclosed
   fact, never a silently-dropped cycle.
3. Reflected in the live health heartbeat's `last_error` field.
4. Never propagated as an exception out of `start()` — the main observation loop is never broken, matching
   every other additive step in this file's own established pattern.

## 7. Recovery Verification

Scenario run: a session where `get_spot()` returns `None` on every cycle (simulating a sustained
intelligence-pipeline failure while the underlying quote-observation loop keeps running). Verified:
- The quote-observation loop itself is entirely unaffected — `QuoteObservationStore` still receives every
  real quote, since the intelligence pipeline step runs strictly after and independently of it.
- Each failed cycle is individually recorded (no corrupted or partial `ShadowIntelligenceCycleArtifact` —
  `build_cycle_artifact()` is never even called when translation fails, so no malformed artifact can exist).
- Health heartbeat reflects the degraded state via `last_error` on every failed cycle, then correctly
  resolves to the session's real terminal status (`COMPLETED` or `FAILED`, per Phase 19.10.1's own
  lifecycle policy) at the end.
- No new recovery mechanism was built — a restarted session simply re-runs cycles and re-records new,
  correctly-idempotent (content-fingerprinted) artifacts; `EventStore`'s own append-only, idempotent-append
  guarantee (Phase 15B) is the real safety net, exactly as it already is for every other durable record in
  this project.

## Testing

`tests/test_shadow_runtime_intelligence_loop.py`, 10 tests, all passing, proving the 7 required properties:

1. **Invocation** — 2 tests: the pipeline runs and records events when enabled; records nothing when
   disabled (off-by-default confirmed).
2. **Production-shaped data flow** — every recorded artifact carries non-empty, real fingerprints at every
   layer (reality → intelligence → decision → environment).
3. **Cycle artifact persistence** — round-trips through `EventStore` byte-identically;
   `.fingerprint()` always matches the stored `cycle_artifact_id`.
4. **LIVE vs HISTORICAL_REPLAY equality** — verified via the real runner (§5), re-proven as an automated test.
5. **Deterministic fingerprints** — two independent, identical runs produce identical artifact id sets.
6. **Failure visibility** — 2 tests: a real induced failure is visible in `errors`, in a
   `SHADOW_INTELLIGENCE_CYCLE_FAILED` event, and in the health heartbeat's `last_error`; a directly-recorded
   failure event round-trips correctly.
7. **No forbidden imports** — 2 tests: AST import scan across both new files; AST attribute-call scan
   re-confirming the runner's pre-existing order/position boundary held.

## Regression

Baseline before this phase: 5,883 passed (post Phase 19.10.1). After Phase 19.10.2's additions: **5,893
passed, 0 failed** — exactly the 10 new tests. All 115 pre-existing `shadow_runtime`/
`intelligence_cycle_recorder`/Phase-19.10.1 tests re-verified passing unchanged before writing new tests.

## Known limitations

- No CLI/driver script exists yet — this phase wires the loop, a future phase still needs an entrypoint to
  actually start a daily session (Phase 19.10.0's own confirmed gap, still open).
- `MarketRealitySnapshot` built by the translator is always `is_final=False`/`COMPLETENESS_PARTIAL` (a
  single live tick, never a settled day) — this is honest, not a defect, but means this loop's own Reality
  view is intentionally thinner than what `capture_market_reality_session.py` + `HistoricalObservationStore`
  produce for a completed day. Reconciling live per-cycle Reality views against the canonical
  post-market-close Reality reconstruction remains open, separate work.
- No integration with Market Understanding Memory (`record_market_memory()`) was added this phase — cycle
  artifacts are session-scoped only, per the phase's own explicit persistence-boundary requirement; wiring
  a daily summary into permanent cross-session memory is real, separate future work.

## Final verdict

After this phase, starting `ShadowSessionRunner` with `intelligence_pipeline_enabled=True` automatically
produces Reality → Understanding → Health artifacts every cycle — verified live through the real runner,
zero manual intelligence invocation required. No trading decision, broker call, or execution logic exists
anywhere in the new code, verified structurally.
