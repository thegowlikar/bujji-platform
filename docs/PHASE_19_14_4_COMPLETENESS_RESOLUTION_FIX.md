# Phase 19.14.4 — EOD Completeness Resolution Fix

Fixes the single remaining software defect found by Phase 19.14.3's re-verification gate. No
systemd action taken — confirmed `not-found` for both units before and after this phase.

## The defect (recap)

`bujji.shadow_runtime.completeness.validate_end_of_day_completeness()` called
`build_market_reality_snapshot()` without a `resolution` argument, defaulting to
`RESOLUTION_DAILY`. The only real writers to `HistoricalObservationStore`
(`scripts/capture_market_reality_session.py`/`capture_options_reality_session.py`) always write
`resolution=FIVE_MINUTE` rows — never `DAILY`. So `_make_real_completeness_fn` in
`run_daily_intelligence_session.py`, as wired since Phase 19.14.1, would report every real,
successfully-captured trading day as `EMPTY`/incomplete.

## The fix

**`bujji/shadow_runtime/completeness.py`** — `validate_end_of_day_completeness()` gains two
additive parameters, both defaulting to the exact prior behavior:
```python
def validate_end_of_day_completeness(
    date: str, *, historical_store: HistoricalObservationStore, now: datetime.datetime,
    resolution: str = RESOLUTION_DAILY, as_of_time: Optional[str] = None,
) -> EndOfDayCompletenessReport:
    ...
    snapshot = build_market_reality_snapshot(
        date, historical_store=historical_store, now=now, resolution=resolution, as_of_time=as_of_time,
    )
```
`build_market_reality_snapshot()` itself already supported `resolution=RESOLUTION_FIVE_MINUTE`
since Phase 18.1 — this module simply never exposed it. No new composition logic, no second
completeness mechanism.

**`run_daily_intelligence_session.py`** — `_make_real_completeness_fn` now passes
`resolution=RESOLUTION_FIVE_MINUTE` and a real end-of-day `as_of_time`
(`f"{date_str}T15:40:00+05:30"`, matching `capture_market_reality_session.py`'s own
`MARKET_CLOSE = datetime.time(15, 40)` constant — reused, not re-derived).

## Verification

**Before the fix** (Phase 19.14.3's own finding, re-confirmed): the real, deployed
`_make_real_completeness_fn('2026-08-14')`, run as `bujji`, returned
`is_complete=False status=EMPTY missing=('spot', 'options', 'vix')` for a day independently proven
(Phase 19.14.0/19.14.1/19.14.3) to have real, complete data (spot 24395.55, VIX 11.32, 2,190 real
option contracts).

**After the fix**, the same real, deployed function, run as `bujji`, against the same real data:
```
production completeness_fn() result AFTER FIX:
 ran= True is_complete= True status= COMPLETE missing= ()
```

**New tests** (`tests/test_phase_19_14_4_completeness_resolution_fix.py`, 4 tests, all against real
production data on the VPS, none mocked):
1. `test_default_resolution_unchanged_still_reports_empty_for_five_minute_only_data` — proves the
   fix is additive: the *old* default (no `resolution`/`as_of_time` passed) still behaves exactly
   as before, so no other caller of this function is silently changed.
2. `test_five_minute_resolution_correctly_finds_real_captured_data` — proves the fix itself: with
   `resolution=RESOLUTION_FIVE_MINUTE` + a real `as_of_time`, the real 2026-08-14 data is found and
   correctly classified `COMPLETE`.
3. `test_production_completeness_fn_reports_complete_after_fix` — calls the exact, real, deployed
   `_make_real_completeness_fn`, not a re-implementation.
4. `test_validate_end_of_day_completeness_still_defaults_to_resolution_daily` — signature-level
   guarantee that the default parameter values are unchanged.

All 4 pass, plus the full pre-existing `tests/test_daily_session_runtime.py` suite (11 tests)
re-run clean alongside them — **15/15 passed**, confirming this change did not disturb any
existing completeness/daily-session behavior.

## A second, independent defect found and fixed during this phase's own verification

The first full-regression run after this fix reported 3 unexpected failures, all in test files
entirely unrelated to completeness or resolution logic: `test_sprint112_hardening.py::test_health_overall_status_green_when_all_fresh_and_healthy`,
`test_sprint4_ops.py::test_no_alerts_when_fully_healthy`,
`test_tick_silence_watchdog.py::test_health_snapshot_defaults_are_backward_compatible`.

**Root cause, traced precisely**: `bujji.live_shadow_operator.health.build_health_snapshot()`
checks `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` against `MEMORY_WARNING_KB` (500MB) to
decide GREEN vs AMBER. `ru_maxrss` is a process-wide, monotonically-increasing high-water mark --
it never decreases within a process. The new `test_five_minute_resolution_correctly_finds_real_captured_data`
test legitimately loaded the real production database's full 2026-08-14 options chain (2,190 real
contracts) in-process, correctly per this project's own "use real production data, not a
fabricated test" convention -- but doing so permanently pushed the whole pytest run's peak RSS
past 500MB, so every subsequent test in the same process that asserted GREEN from a fresh
`build_health_snapshot()` call failed, regardless of order, for the rest of that run. Confirmed via
isolation: the 3 failing tests pass individually and pass together with any of the other three new
tests, but fail specifically and only when run after the one real-data-loading test -- bisected
precisely to that one test's memory footprint, not a logic bug in either side.

**Fix**: the two real-data-loading tests (`test_five_minute_resolution_correctly_finds_real_captured_data`,
`test_production_completeness_fn_reports_complete_after_fix`) now run their real-data assertions in
an isolated `subprocess.run([sys.executable, "-c", ...])` call rather than in-process, printing a
small JSON result the parent test asserts on. This gets the identical real-data proof without the
subprocess's memory footprint ever touching the main pytest process's `ru_maxrss`. No change was
made to `health.py` or any of the three unrelated pre-existing test files -- the fix is entirely
contained to this phase's own new test file.

**Re-verified**: the exact 4-test combination that previously failed
(`tests/test_phase_19_14_4_completeness_resolution_fix.py` plus the 3 previously-failing health
tests) now passes clean, 7/7.

## Full Regression

| | Count |
|---|---|
| Previous baseline (Phase 19.14.1/19.14.3) | 5,956 |
| New tests this phase | 4 |
| **Final total** | **5,960** |
| Failures | **0** |
| Errors | **0** |

`5,960 passed, 0 failed` (plus the same pre-existing, unrelated `pkg_resources` deprecation
warning). Confirmed on the final, clean run after the subprocess-isolation fix above.

## Updated Status

Both Phase 19.14.2's approved remediations (filesystem ownership, stale process retirement) remain
in effect and verified (Phase 19.14.3). This phase closes the one remaining software defect that
gate discovered. No other software/runtime/scheduling/locking/integration defect is currently
known — every other check across Phases 19.14.1 and 19.14.3 (systemd verify, scheduler conflict
audit, live `ProcessLock` proofs, pipeline ordering, all six failure modes, LIVE/REPLAY equivalence
on real data, broker order-safety boundary, honest FYERS-failure behavior) already passed and is
unaffected by this change.

## Final Verdict

# **C — EXTERNAL BLOCKER ONLY**

All software and runtime checks now pass. The only remaining blocker is the already-known,
unfixable-in-this-codebase requirement: a human must complete FYERS's interactive login (browser +
TOTP/PIN) and refresh `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` before each trading day, since
FYERS's own refresh-token API is disabled per a SEBI-driven restriction
(`docs/FYERS_TOKEN_LIFECYCLE.md`, live-verified 2026-07-19). This phase did not attempt to
automate, bypass, or weaken that boundary in any way.

**Still not commissioned**: no systemd unit was installed, enabled, or started in this phase or
any prior phase. Installing and enabling `bujji-daily-intelligence.timer` remains a decision for
the operator to make explicitly.
