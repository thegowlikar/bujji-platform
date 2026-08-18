# Phase 19.11 — Market Data Continuity & Runtime Reliability

## Objective

Make Bujji's market observation system operate continuously every NSE session without manual
intervention. Reliability engineering only — no intelligence logic, no strategy, no execution, no
modification to any Reality model.

Addresses the central risk Phase 19.9.5 confirmed: daily capture depends on manual execution today; a
missed session creates a permanent memory gap.

## Files changed

**New, in `bujji/shadow_runtime/`:**
- `daily_session.py` — `DailySessionStage`, `DailySessionLifecycle`, `DailySessionHeartbeat`,
  `write_daily_heartbeat()`/`read_daily_heartbeat()`, `CaptureResult`, `IntelligenceRunResult`,
  `DailySessionReport`, `DailySessionRuntime`
- `completeness.py` — `EndOfDayCompletenessReport`, `validate_end_of_day_completeness()`

**New, at repo root:** `run_daily_intelligence_session.py` — the single authoritative daily entrypoint.

**New test file:** `tests/test_daily_session_runtime.py` (11 tests).

No existing file was modified this phase — `DailySessionRuntime` is a new orchestrator that calls
already-existing pieces (capture scripts, `ShadowSessionRunner`) via dependency injection; it does not
touch `shadow_session_runner.py`, `HistoricalObservationStore`, or any Reality model.

## 1. Single authoritative daily runtime entrypoint

`DailySessionRuntime` never captures data or runs a brain itself — it orchestrates two injected async
callables (`capture_fn`, `intelligence_fn`), the same dependency-injection convention `ShadowSessionRunner`
already established for `clock`/`sleep_fn`/`broker` (Phase-5 onward). This is a deliberate, disclosed
design choice: production wiring (`run_daily_intelligence_session.py`) points `capture_fn` at the
already-existing, unmodified `capture_market_reality_session.py`/`capture_options_reality_session.py`
scripts (subprocess-invoked, never reimplemented) and measures real rows captured via
`HistoricalObservationStore.count()` before/after — no new counting logic. `intelligence_fn` is
**explicitly NOT wired to a real broker** by this phase — it fails loudly and honestly
(`"intelligence_fn not wired to a real broker"`) rather than silently pretending to run a cycle it never
performed, the same "shadow mode only, real auth is the operator's own responsibility" boundary
`run_live_shadow.py` already established (Phase 19.9.6's own finding). `--dry-run` uses safe no-op
functions for verifying the orchestration wiring itself without touching any broker or subprocess —
verified live: a dry run correctly reports both steps as honestly skipped and exits non-zero, never
falsely claiming success.

## 2. Session lifecycle management

```
PRE_MARKET -> MARKET_OPEN -> CAPTURING -> INTELLIGENCE_RUNNING -> SESSION_COMPLETE
                                                                 -> FAILED
```

Same immutable, transition-validated design pattern `shadow_runtime.lifecycle.RuntimeStage`
(Phase 19.10.1) already established — a distinct state vocabulary (session-level, not cycle-level), so a
separate table, not a shared one. `FAILED` is reachable from every non-terminal stage; an illegal
transition raises `IllegalDailySessionTransition` rather than being silently allowed — verified by test.

## 3. Heartbeat monitoring — exactly the 5 required fields

```json
{
  "session_date": "...", "runtime_status": "...",
  "last_observation_timestamp": "...", "last_intelligence_cycle_timestamp": "...",
  "rows_captured_today": 0, "last_error": null
}
```

Written atomically (write-to-temp-then-`os.replace`) — the identical technique
`shadow_runtime.health.write_health_heartbeat()` already uses (Phase 19.10.1), reused here rather than a
second mechanism. Verified by test that the heartbeat reflects real `capture_fn`/`intelligence_fn` results
(row counts, timestamps), not placeholder values.

## 4. Automatic recovery

Verified via a real crash-then-restart test: a `DailySessionRuntime` whose `capture_fn` raises produces a
`FAILED` report with the real exception recorded in the heartbeat's `last_error`; a **fresh**
`DailySessionRuntime` instance (simulating a process restart) for the same `session_date`, with a working
`capture_fn`, then completes cleanly, and the heartbeat correctly updates to `SESSION_COMPLETE` with
`last_error: null`. No duplicate-observation risk was invented to solve — `HistoricalObservationStore`'s
own already-proven append/conflict-guard discipline (Phase 17H+) and `EventStore`'s own already-proven
idempotent-append discipline (Phase 15B, reused again in Phase 19.10.2) are the real safety nets;
verified directly by test that writing the identical observation twice produces zero new rows.

## 5. End-of-day completeness validation

`validate_end_of_day_completeness()` reuses `build_market_reality_snapshot()` (Phase 18.1, unmodified)
directly rather than re-deriving presence/certification logic a second time from raw
`HistoricalObservationStore` rows. Verified by two tests against a real, isolated store: a day with only a
spot observation is honestly reported `is_complete=False` (options/VIX correctly absent, never assumed
present); a day with real spot + VIX + option rows is reported `is_complete=True`, with
`certification_refs` and `completeness` carried through verbatim from the real snapshot.

## 6. Preserved boundaries — verified structurally

AST-level import scan across both new `shadow_runtime` files confirms zero imports of anything broker/
order/position/strategy-shaped. A second AST-level attribute-call scan confirms neither file calls
`place_order`/`modify_order`/`cancel_order`/`get_open_positions`. `run_daily_intelligence_session.py`
itself never imports a broker module either — its real capture path is a subprocess call to an
already-existing script, and its intelligence path is an explicit, disclosed non-wiring.

## 7. Testing

`tests/test_daily_session_runtime.py`, 11 tests, all passing, proving all 6 required scenarios:

1. **Two consecutive trading days** — independent `DailySessionRuntime` runs for 2026-08-13 and
   2026-08-14 both complete cleanly with their own correct `session_date`.
2. **Simulated crash recovery** — 2 tests: a crash-then-fresh-restart sequence (§4); an illegal lifecycle
   transition is never silently allowed.
3. **Missing data detection** — 2 tests: a genuinely incomplete day is honestly flagged incomplete; a
   genuinely complete day is correctly flagged complete.
4. **Heartbeat correctness** — real capture/intelligence results (row counts, timestamps) are reflected
   verbatim in the persisted heartbeat file.
5. **No duplicate capture** — writing the identical real observation twice produces zero new rows,
   verified directly against `HistoricalObservationStore`'s own real dedup guarantee.
6. **Clean shutdown** — 2 tests: `run()` never raises even when `capture_fn` raises an arbitrary
   exception, always returning a terminal `DailySessionReport`; a session with no heartbeat path
   configured (opt-in, off by default) still completes cleanly.

Plus 2 structural boundary tests (AST-level, not naive substring — this session's own now-familiar
discipline, applied proactively this time rather than caught after a false failure).

## Regression

Baseline before this phase: 5,893 passed (post Phase 19.10.2). After Phase 19.11's additions: **5,904
passed, 0 failed** — exactly the 11 new tests. 3 new files (2 in `shadow_runtime/`, 1 entrypoint at repo
root) + 1 new test file; zero existing files modified, zero existing tests affected.

## Known limitations

- `intelligence_fn`'s real broker wiring is explicitly not attempted this phase — `DailySessionRuntime`
  and its entrypoint are structurally ready to accept it, but connecting a real, authenticated
  `ShadowSessionRunner` is deliberately left as disclosed, unstarted work (matching this project's own
  standing discipline against silently pretending unfinished wiring is complete).
- No cron/systemd/scheduler was added — per the phase's own implicit scope (and every prior phase's
  explicit "no new scheduler" instruction) — `run_daily_intelligence_session.py` must still be triggered
  manually or by infrastructure the user sets up separately. This phase makes ONE reliable, idempotent,
  observable entrypoint to trigger — it does not decide how triggering itself becomes automatic.
- `CaptureResult.rows_captured` in the real (non-dry-run) wiring is computed as a before/after row-count
  delta across BOTH capture scripts combined, not per-script — a real, disclosed simplification; a future
  phase could report per-instrument-type counts if that granularity becomes operationally necessary.
- Crash-recovery testing in this phase is at the `DailySessionRuntime` orchestration level (a fresh
  instance resumes cleanly) — it does not simulate an OS-level process kill/restart of
  `run_daily_intelligence_session.py` itself, since that is an infrastructure-level concern (process
  supervision) explicitly out of this phase's scope, consistent with "no scheduler/daemon" instructions
  received across this entire phase series.

## Final verdict

Bujji now has one real, tested, reliability-focused daily orchestrator: `DailySessionRuntime`, fronted by
`run_daily_intelligence_session.py`. It never captures data or runs intelligence itself — it calls the
already-existing, unmodified pieces this session's entire Phase 19.x series already built and verified,
records a real heartbeat throughout (not only at the end), never raises on failure, and validates
end-of-day completeness honestly. The remaining gap — closing the loop from "one reliable entrypoint
exists" to "it runs automatically every trading day" — is an infrastructure/scheduling decision left
explicitly to the user, not attempted here.
