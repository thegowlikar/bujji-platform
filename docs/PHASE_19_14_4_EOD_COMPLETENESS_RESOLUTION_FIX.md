# Phase 19.14.4 — EOD Completeness Resolution Fix + Re-Verification

Fixes the single software blocker identified by Phase 19.14.3: `_make_real_completeness_fn()`
evaluated EOD completeness at `RESOLUTION_DAILY`, but the authoritative live capture path only
ever writes `RESOLUTION_FIVE_MINUTE` observations — so a correctly captured trading day was always
reported incomplete. No systemd action was taken — confirmed `not-found` for both units before and
after this phase.

## 1. Forensic Root-Cause Confirmation

Pulled and inspected the exact deployed implementations before changing anything:

- **`capture_market_reality_session.py`** / **`capture_options_reality_session.py`** — both call
  `HistoricalObservationStore.write()` with observations built at `resolution=FIVE_MINUTE` only.
  Confirmed directly against the real database: `SELECT resolution, instrument_type, count(*) ...
  WHERE timestamp LIKE '2026-08-14%' GROUP BY ...` returns exactly four rows, all
  `resolution='FIVE_MINUTE'` (`SPOT`, `INDEX` (VIX), `FUTURE`, `OPTION`) — zero `DAILY` rows exist
  for any live-captured day.
- **`bujji/shadow_runtime/completeness.py`**'s `validate_end_of_day_completeness()` called
  `build_market_reality_snapshot(date, historical_store=historical_store, now=now)` — no
  `resolution` or `as_of_time` argument, so it always used `build_market_reality_snapshot()`'s own
  default, `RESOLUTION_DAILY`.
- **`build_market_reality_snapshot()`**'s `RESOLUTION_DAILY` branch calls
  `_build_spot_snapshot()`, which queries `historical_store.range(SPOT_SYMBOL, RESOLUTION_DAILY,
  day_start, day_end)` — literally filtering for `resolution='DAILY'` rows. Since none exist, this
  branch always returns `None` for spot/futures/vix, and `_build_options_snapshot(date, day_end,
  RESOLUTION_DAILY, ...)` likewise never matches. `build_market_reality_snapshot()` itself has
  *already supported* `resolution=RESOLUTION_FIVE_MINUTE` since Phase 18.1 (confirmed:
  `_build_spot_snapshot_intraday`, `_build_vix_snapshot_intraday`,
  `_build_futures_snapshot_intraday`, and `_build_options_snapshot(..., RESOLUTION_FIVE_MINUTE,
  ...)` all already exist and are exercised by pre-existing tests in
  `tests/test_market_reality_snapshot_options_intraday.py`) — `completeness.py` simply never
  exposed that already-working path.
- **`HistoricalObservationStore`**'s query APIs (`range`, `range_by_prefix`) were not touched —
  confirmed unmodified, still natural-key idempotent, still the sole read/write surface.
- **Existing completeness tests** (`tests/test_daily_session_runtime.py`) construct
  `CompletenessCheckResult` directly via stubs — none exercised the real
  `validate_end_of_day_completeness()` end-to-end against real `FIVE_MINUTE` data, which is exactly
  why this defect went undetected until Phase 19.14.3's own production re-verification.

**Root cause confirmed from the deployed code before any edit was made.**

## 2. Minimal Fix

**`bujji/shadow_runtime/completeness.py`** — `validate_end_of_day_completeness()` gains two
additive parameters, both defaulting to the exact prior behavior so every other caller is
unaffected:
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
No new composition logic — `build_market_reality_snapshot()`'s own already-existing
`RESOLUTION_FIVE_MINUTE` path is reused verbatim, not reimplemented.

**`run_daily_intelligence_session.py`** — `_make_real_completeness_fn` now passes
`resolution=RESOLUTION_FIVE_MINUTE` (the same canonical constant capture itself writes, imported
from `bujji.market_reality_snapshot.models`, never a duplicated magic string) and a real
end-of-day `as_of_time` (`f"{date_str}T15:40:00+05:30"`, matching
`capture_market_reality_session.py`'s own `MARKET_CLOSE = datetime.time(15, 40)` constant — reused
by value, not re-derived independently).

**Nothing else was touched**: `HistoricalObservationStore`, the two capture scripts,
`MarketIntelligenceSnapshot`, `DecisionIntelligence`, `MarketPhenomena`, `MarketStateGraph`,
`MarketEnvironment`, strategy logic, and execution logic are all unmodified — confirmed by `git`-
free direct diff review of exactly the two files listed above.

## 3. Test the Actual Bug First

`tests/test_phase_19_14_4_completeness_resolution_fix.py::test_default_resolution_unchanged_still_reports_empty_for_five_minute_only_data`
demonstrates the **original defect** directly against real production data: calling
`validate_end_of_day_completeness()` with the *old* default (no `resolution`/`as_of_time`) against
the real 2026-08-14 `FIVE_MINUTE` data returns `completeness=EMPTY`, `is_complete=False` — exactly
the bug, reproduced on demand, not merely asserted from memory.

`test_five_minute_resolution_correctly_finds_real_captured_data` then proves the **fix**: the same
real data, with `resolution=RESOLUTION_FIVE_MINUTE` + a real `as_of_time`, returns `COMPLETE`/
`is_complete=True`.

`test_production_completeness_fn_reports_complete_after_fix` closes the loop against the **real,
deployed, non-stubbed** `run_daily_intelligence_session._make_real_completeness_fn` — the exact
function the daily runtime will call, not a re-implementation — confirming
`ran=True is_complete=True completeness_status=COMPLETE missing_components=()`.

## 4. Real Production-Data Proof

Run as the real `bujji` service user, against the real, `bujji`-owned
`data/historical_reality/normalized/historical_observations.db`:

| Field | Value |
|---|---|
| Session date | `2026-08-14` |
| Resolution used | `FIVE_MINUTE` (confirmed — the fix's own parameter) |
| Spot | present, close = `24395.55` |
| VIX (INDEX) | present, close = `11.32` |
| Options | present, `2,190` real contracts |
| Futures | present in the raw store (77 `FUTURE` rows for the day) — not part of the
  `is_complete` gate itself (spot/options/vix only, per the pre-existing, unmodified
  `_classify_completeness`), but confirmed available |
| `completeness` | `COMPLETE` |
| `is_complete` | `True` |

**No database contents were modified** — this was a pure read; the only write activity against
this database in this whole phase 19.14 sequence happened in Phase 19.14.3's own filesystem
remediation proof (a `BEGIN IMMEDIATE` + real INSERT + `ROLLBACK`, already verified to leave zero
trace, `integrity_check: ok`).

## 5. EOD Behavior Verification (A–F)

New tests, `tests/test_phase_19_14_4_completeness_resolution_fix.py`, against synthetic
`FIVE_MINUTE` fixtures built via `build_historical_observation()` — the exact same fixture
convention already established in `tests/test_market_reality_snapshot_readiness.py`, never a new
one:

| Case | Scenario | Result |
|---|---|---|
| A | Complete trading day (spot + futures + vix + options) | `completeness=COMPLETE`, `is_complete=True` |
| B | Missing VIX | `is_complete=False`, `vix_present=False`, `completeness=PARTIAL` |
| C | Missing options | `is_complete=False`, `options_present=False` (spot/vix still present) |
| D | Missing spot | `is_complete=False`, `spot_present=False` |
| E | Empty observation set (nothing captured) | `completeness=EMPTY`, `is_complete=False` |
| F | Weekend / non-trading day (2026-08-15, a real Saturday) | `completeness=EMPTY`, `is_complete=False` |

**No new classification was invented for F.** A weekend/non-trading day produces zero real
observations, since `within_market_hours()` in the capture scripts already refuses to run before
any broker call on a weekend (Phase 19.14.0's own confirmed finding) — this collapses to the exact
same `EMPTY` state as case E under the existing, unmodified model. A distinct
`NOT_A_TRADING_DAY` enum would duplicate information the caller can already get from
`completeness == EMPTY` combined with the day-of-week, without the existing architecture asking
for one anywhere.

All 6 cases pass; **10/10** tests in the file pass in total.

## 6. LIVE/REPLAY Path, Re-Verified With the Corrected Completeness Configuration

Re-ran the real intelligence pipeline against the real 2026-08-14 data, this time using the
**exact `as_of_time` the corrected `_make_real_completeness_fn` now uses** (`15:40:00+05:30`, not
an arbitrarily different time from an earlier gate):

```
completeness: COMPLETE  spot: 24366.0  vix: 11.27  options: 2190
fingerprint equal:  True
environment equal:  True
posture equal:      True
env: STAND_ASIDE  posture: FAVOR_PREMIUM_ENVIRONMENT
```

`LIVE` and `HISTORICAL_REPLAY` still produce identical intelligence fingerprints, environment
classification, and recommended posture — the resolution fix changes *which real data the
completeness check looks at*, not anything about how intelligence is composed from that data.
`MarketIntelligenceSnapshot`'s own `fingerprint_payload()` still deliberately excludes
`execution_mode` (unmodified, re-confirmed by this same equality holding).

## 7. Safety Boundary, Re-Verified

Re-ran `tests/test_phase_19_13_live_intelligence_bridge.py` +
`tests/test_phase_19_14_1_commissioning_hardening.py` +
`tests/test_phase_19_14_4_completeness_resolution_fix.py` together — **50/50 passed**. AST/call-
level checks confirm zero `place_order`/`modify_order`/`cancel_order` reachable from any file this
phase touched, `disable_live_execution` wrapping still present and unmodified. This phase's two
edited files (`completeness.py`, `run_daily_intelligence_session.py`) introduce zero new broker
calls, zero new writes to any store, and zero position/strategy/execution logic — market-data
*reads* only.

## 8. Full Regression

| | Count |
|---|---|
| Previous baseline (Phase 19.14.3) | 5,956 |
| New tests this phase | 10 (`test_phase_19_14_4_completeness_resolution_fix.py`) |
| **Final total** | **5,966** |
| Failures | **0** |
| Errors | **0** |

`5,966 passed, 0 failed` (plus the same pre-existing, unrelated `pkg_resources` deprecation
warning from the `fyers_apiv3` dependency).

**A second, unrelated defect was found and fixed during this phase's own verification** (not part
of the completeness resolution bug, but required to reach a clean regression): the first two
real-data-loading tests, run in-process, permanently pushed the whole pytest run's peak RSS
(`resource.getrusage().ru_maxrss`, a process-wide, monotonically-increasing high-water mark) past
the 500MB `MEMORY_WARNING_KB` threshold three unrelated health tests
(`test_sprint112_hardening.py`, `test_sprint4_ops.py`, `test_tick_silence_watchdog.py`) assert
against. Fixed by isolating those two tests' real-data assertions in a subprocess, so their memory
footprint never touches the shared pytest process. No change was made to `health.py` or any
unrelated test file — confirmed contained entirely to this phase's own new test file.

## 9. Final Commissioning Gate — Re-run, Not Cited

Every item re-executed fresh against the current, fixed, deployed code (none merely cited from
Phase 19.14.3):

| Item | Result |
|---|---|
| A. `systemd-analyze verify` | Both `deploy/bujji-daily-intelligence.service` and `.timer` → exit 0, clean |
| B. Scheduler/process conflict audit | No crontab (root/bujji), no `bujji*` systemd timers/units, `bujji-daily-intelligence.{service,timer}` not installed (`is-enabled` → `not-found` for both), zero relevant processes running |
| C. `ProcessLock` duplicate prevention | Live: second acquire → `LockAcquisitionError`; manual-entrypoint guard → `AuthoritativeRuntimeActiveError` |
| D. Restart/recovery | Live: after `release()`, a fresh `ProcessLock` on the same path acquires immediately |
| E. Pipeline ordering | Live trace: `CAPTURE -> INTELLIGENCE_LIVE -> INTELLIGENCE_REPLAY -> LIVE_REPLAY_EQUIVALENCE -> EOD_COMPLETENESS -> FINAL_ARTIFACT_BUILT -> SESSION_COMPLETE` |
| F. Six failure modes | All six live-executed, each with a distinct, prefixed reason (`capture_error`, `eod_completeness_incomplete`, `intelligence_error`, `replay_check_failed`, `replay_equivalence_mismatch`, and a clean `SESSION_COMPLETE`) |
| G. EOD completeness | Re-run against real 2026-08-14 data with the corrected wiring → `COMPLETE` (§4) |
| H. LIVE/REPLAY equivalence | Re-run with the corrected `as_of_time` → fingerprint/environment/posture all equal (§6) |
| I. Real production-data smoke test | Re-run, real spot/VIX/options counts reported (§4) |
| J. Broker safety | 50/50 tests re-run clean (§7) |
| K. FYERS failure behavior | Re-run: credentials stripped from a subprocess env (real `.env` untouched) → `_build_real_broker()` still raises `RuntimeError("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in environment")` |
| L. Filesystem/service-user access | Re-run as `bujji`: db writable, `normalized/` dir writable, `data/` writable, `.env` readable, `logs/` writable — all still correct after Phase 19.14.3's ownership fix |

## Remaining Limitation

**FYERS interactive authentication (Class C, external, unchanged)** — a human must complete
FYERS's browser + TOTP/PIN login and refresh `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` before each
trading day, since FYERS's own refresh-token API is disabled per a SEBI-driven restriction
(`docs/FYERS_TOKEN_LIFECYCLE.md`, live-verified 2026-07-19). This phase did not attempt to
automate, bypass, or weaken that boundary.

## Final Verdict

# **C — EXTERNAL BLOCKER ONLY**

All software and runtime checks re-verified clean in this phase, live and fresh, not cited from a
prior gate. The completeness-resolution defect that kept the prior verdict at **B** is fixed and
proven against real production data. The only remaining blocker is the already-known FYERS
interactive-authentication requirement, external to and unfixable in this codebase.

**Still not commissioned.** No systemd unit was installed, enabled, or started in this phase.
Installing and enabling `bujji-daily-intelligence.timer` remains an explicit operator decision.
