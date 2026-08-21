# Phase 19.14.3 — Production Remediation + Re-Verification

Executes the two operational remediations approved from Phase 19.14.2, then re-runs the
commissioning gate against the corrected state. **Nothing was installed, enabled, started, or
restarted** — confirmed throughout by `systemctl is-enabled` returning `not-found` for both units,
before and after every step in this phase.

## 1. Historical Observation Store Ownership Fix

**Command executed exactly as approved:**
```bash
chown -R bujji:bujji /opt/bujji/app/data/historical_reality/normalized
```
`raw_artifacts/` was **not** touched — confirmed still `root:root` after the change. No `chmod` was
run.

**Before → After:**

| Path | Before | After |
|---|---|---|
| `normalized/` (dir) | `root:root` | **`bujji:bujji`** |
| `historical_observations.db` | `root:root` | **`bujji:bujji`** |
| `market_reality_snapshots.db` | `root:root` | **`bujji:bujji`** |
| `raw_artifacts/` (+ subtree) | `root:root` | `root:root` (unchanged) |
| Directory mode | `755` (unchanged) | `755` (unchanged — no chmod run) |
| File mode | `644` (unchanged) | `644` (unchanged — no chmod run) |

**Real write proof, not fabricated** — used the actual `HistoricalObservationStore` connection
(`self._conn`, the same object every capture call uses) as the `bujji` user:
```
journal_mode after bujji open: wal
row count before:              670163
row count mid-transaction:     670164   (BEGIN IMMEDIATE + real INSERT)
row count after rollback:      670163   (ROLLBACK — no permanent change)
WRITE TEST: PASS
WAL sidecar files created by bujji: historical_observations.db-wal, historical_observations.db-shm
```
Followed by, still as `bujji`:
```
integrity_check: ok
count: 670163                                    (unchanged from before the test)
probe row absent: 0                              (confirms the rollback left no trace)
PRAGMA wal_checkpoint(TRUNCATE)                  → sidecar files cleaned up, db back to steady state
```
**Historical data contents were not altered** — verified by identical row count before/after and
`integrity_check: ok`.

## 2. Stale PID 787565 Retirement

**Pre-stop re-confirmation** (same PID, same command line, still zero legs/P&L, still not on the
canonical capture path):
```
PID 787565, started Fri Jul 31 07:01:44 2026, cmd unchanged:
  python run_live_shadow.py --live --bhavcopy ... --bhavcopy-day 2026-07-30 --log-file logs/lsq1_day1.log
Last log line: 2026-07-31 15:30:01 WARNING tick_feed_closed; legs=0 total_pnl=0.0 throughout.
No HistoricalObservationStore file handle; no reference to data/historical_reality anywhere in its open files.
```

**Action**: `kill -TERM 787565`. `run_live_shadow.py`'s own script was **not** modified.

**Post-stop verification:**
| Check | Result |
|---|---|
| Process still exists? | **No** — `ps -p 787565` returns nothing, 3s after SIGTERM |
| `live_shadow_operator.lock` released? | **Yes** — a fresh `ProcessLock` on that same path acquires cleanly immediately after |
| Replacement `run_live_shadow.py` process appeared? | **No** — `ps aux` clean |
| Canonical capture/intelligence path affected? | **No** — none of `run_daily_intelligence_session.py`/`capture_market_reality_session.py`/`capture_options_reality_session.py` were running before or after (expected, not yet commissioned) |

## 3. Filesystem Verification, Re-run as `bujji`

| Path | Readable | Writable |
|---|---|---|
| `historical_observations.db` | Yes | **Yes (fixed)** |
| `normalized/` (dir) | Yes | **Yes (fixed)** |
| `/opt/bujji/app/data` | Yes | Yes (unchanged, already correct) |
| `/opt/bujji/.env` | Yes | — |
| `/opt/bujji/app/logs` | Yes | Yes (unchanged) |

The real production `HistoricalObservationStore` connection, opened as `bujji`, correctly enters
WAL mode, performs a real write transaction, checkpoints, and leaves the database in a clean,
integrity-checked state — see §1's proof above (not repeated).

## 4. Authoritative Runtime Safety Audit, Re-run

| Check | Result |
|---|---|
| `crontab -l` (root), `crontab -u bujji -l` | Both: "no crontab" |
| `systemctl list-timers --all` | No `bujji*` timer present |
| `systemctl list-units --all 'bujji*'` | 0 loaded units |
| `bujji-daily-intelligence.service`/`.timer` installed? | No — absent from `/etc/systemd/system/`, `is-enabled` → `not-found` for both |
| `run_daily_intelligence_session.py` process | Not running |
| `run_live_shadow.py` process | **Not running** (retired in §2) |
| `scripts/run_shadow_live_observatory.py` process | Not running |
| `bujji_options_os_runner.py` process | Not running |
| `ShadowSessionRunner`-based process | Not running |

**Confirmed: one canonical capture path, one authoritative daily runtime (not yet running), one
`HistoricalObservationStore`, one intelligence pipeline — zero competing schedulers, zero competing
processes.**

## 5. Final Commissioning Gate, Re-run

**A. `systemd-analyze verify`**: both `deploy/bujji-daily-intelligence.service` and `.timer` → exit
0, clean.

**B. Scheduler conflict audit**: see §4 above — clean.

**C. `ProcessLock` duplicate prevention**, live, against `data/daily_intelligence.lock`:
```
authoritative acquired: True
second daily runtime refused: LockAcquisitionError
manual entrypoint guard refused: AuthoritativeRuntimeActiveError
```

**D. Reboot/restart recovery**: after `release()`, a fresh `ProcessLock` on the same path acquires
immediately — `True`.

**E. Pipeline ordering**, live call-trace against the real `daily_session.py`:
```
CAPTURE -> INTELLIGENCE_LIVE -> INTELLIGENCE_REPLAY -> LIVE_REPLAY_EQUIVALENCE -> EOD_COMPLETENESS -> FINAL_ARTIFACT_BUILT -> SESSION_COMPLETE
```

**F. All six failure modes**, live-executed:
```
capture_failure:      FAILED  capture_error: broker timeout
incomplete_reality:   FAILED  eod_completeness_incomplete: status=PARTIAL missing=['vix']
intelligence_failure: FAILED  intelligence_error: broker_connect_failed: timeout
replay_check_failure: FAILED  replay_check_failed: ValueError: no candles
replay_mismatch:      FAILED  replay_equivalence_mismatch: ['fingerprint mismatch']
success:              SESSION_COMPLETE  (no errors)
```
Every failure carries a distinct, prefixed reason — no generic "failed" anywhere.

**G. EOD completeness — re-run against the now-`bujji`-writable store, and a real, previously
undetected defect found here:**

Calling the *actual, deployed* `run_daily_intelligence_session._make_real_completeness_fn('2026-08-14')`
as the `bujji` user — the exact function the daily runtime will call in production — returns:
```
ran=True is_complete=False status=EMPTY missing=('spot', 'options', 'vix')
```
This is **wrong** for 2026-08-14: independently proven in §H below (and in Phase 19.14.1's own
prior audit) that this date has real, COMPLETE data — spot 24395.55, VIX 11.32, 2,190 real option
contracts.

**Root cause traced**: `validate_end_of_day_completeness()` calls `build_market_reality_snapshot()`
without a `resolution` argument, defaulting to `RESOLUTION_DAILY`. `_build_spot_snapshot()` (the
`RESOLUTION_DAILY` code path) queries `historical_store.range(SPOT_SYMBOL, RESOLUTION_DAILY,
day_start, day_end)` — literally filtering for rows whose stored `resolution` column equals
`'DAILY'`. But `scripts/capture_market_reality_session.py`/`capture_options_reality_session.py`
(the only writers) **always** write rows with `resolution='FIVE_MINUTE'` — confirmed directly
against the real database (`SELECT resolution, instrument_type, count(*) ... GROUP BY`: every real
row for 2026-08-14 is `FIVE_MINUTE`, zero rows are `DAILY`). `_make_real_completeness_fn` in
`run_daily_intelligence_session.py` never passes `resolution=RESOLUTION_FIVE_MINUTE` or an
`as_of_time`, so it can never find the data that is actually there.

**Consequence**: as currently wired, the EOD completeness check will report `EMPTY`/incomplete on
**every single trading day**, including days where capture and intelligence composition both
succeed perfectly — because it is querying for a resolution the live capture pipeline never
writes. This would cause every daily session to end `FAILED` on the `eod_completeness_incomplete`
check alone, independent of and in addition to the two remediations approved for this phase.

**This defect is real, live-verified against the actual deployed code and real production data —
and, per this phase's explicit scope, it is disclosed here and NOT fixed.** Fixing it (passing
`resolution=RESOLUTION_FIVE_MINUTE` and a real `as_of_time` into
`_make_real_completeness_fn`'s call to `validate_end_of_day_completeness()`) is a small, additive
change to `run_daily_intelligence_session.py` — but this phase's instructions are explicit: "Do not
make any additional architectural changes. Stop after the verification report," and the only two
approved remediations were the `chown` and the process retirement. This is flagged as the required
follow-up for the next phase.

**H+I. LIVE vs HISTORICAL_REPLAY on real production data, re-run as `bujji` against the
now-writable store** — same real date (2026-08-14, `resolution=RESOLUTION_FIVE_MINUTE`,
`as_of_time=2026-08-14T14:00:00+05:30`, the correct call shape §G's defect is missing):
```
spot=24395.55  vix=11.32  options=2190 contracts
fingerprint equal:  True
environment equal:  True
posture equal:      True
```
Identical to the result already obtained in Phase 19.14.1's gate — re-confirmed here specifically
running as the `bujji` user against the now-correctly-owned database, proving the ownership fix
did not disturb this path.

**J. Broker order-safety boundary**: `tests/test_phase_19_13_live_intelligence_bridge.py` +
`tests/test_phase_19_14_1_commissioning_hardening.py` re-run clean, **40/40 passed** — AST/call-level
checks confirming zero `place_order`/`modify_order`/`cancel_order` reachable from the daily
intelligence path, `disable_live_execution` wrapping confirmed present.

**K. FYERS authentication failure behavior**, re-run: with credentials stripped from a subprocess
environment (the real `.env` untouched), `_build_real_broker()` still raises
`RuntimeError("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in environment")` — clear, honest failure,
unchanged by this phase's remediations.

**L. Filesystem/service-user access**: see §3 — all paths now correctly accessible, the one
previously-failing path (`historical_observations.db`/`normalized/`) now fixed and re-verified.

## 6. Full Regression

| | Count |
|---|---|
| Previous baseline (Phase 19.14.1) | 5,956 |
| New tests this phase | 0 (remediation-only phase, no code changes, no new tests required) |
| **Final total** | **5,956** |
| Failures | **0** |
| Errors | **0** |

`5,956 passed, 0 failed, 1 warning` (the same pre-existing, unrelated `pkg_resources` deprecation
notice from the `fyers_apiv3` dependency).

## Remaining Blockers

1. **FYERS interactive authentication (Class C, external, unchanged)** — a human must complete the
   FYERS browser+TOTP/PIN login and refresh `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` before each
   trading day. Not addressed by this phase, not addressable in this codebase.
2. **EOD completeness resolution mismatch (Class B, software, newly discovered by this gate)** —
   `_make_real_completeness_fn` in `run_daily_intelligence_session.py` queries
   `RESOLUTION_DAILY` data that the live capture pipeline never writes (`FIVE_MINUTE` only),
   causing every real trading day to report `eod_completeness_incomplete`/`FAILED` even on a
   perfect capture. **Not fixed in this phase, per its own explicit scope** — flagged as the
   required next step before commissioning.

## Final Verdict

# **B — SOFTWARE BLOCKER REMAINS**

Both approved remediations succeeded and are fully verified: filesystem ownership is fixed (real
write-transaction proof, integrity-checked, data unaltered), and the stale process is cleanly
retired (lock released, no replacement, canonical path unaffected). Every other check in this
gate — `systemd-analyze verify`, scheduler conflict audit, live `ProcessLock` duplicate-prevention
and recovery proofs, live pipeline-ordering trace, all six failure modes, LIVE/REPLAY equivalence
on real production data, the broker order-safety boundary, and honest FYERS-failure behavior — all
passed cleanly, and full regression is green at 5,956/5,956.

**However, this gate's own re-verification of EOD completeness (§G) — run against the real
deployed function and real production data rather than relying on prior stubbed test results, as
this phase's own instructions required — found a genuine, previously undetected software defect**:
the completeness check queries a data resolution the live capture pipeline never produces, and
would report every real trading day as incomplete. This is a software/integration defect, not the
already-known external FYERS dependency, so per this phase's own explicit rule ("If anything
software/runtime-related fails: B — SOFTWARE BLOCKER REMAINS. Do not call it A yet" — and by
direct extension, not C either, since C requires "all software/runtime checks above pass"), the
correct classification is **B**, not C.

**Recommendation**: a small, additive fix to `_make_real_completeness_fn` (pass
`resolution=RESOLUTION_FIVE_MINUTE` and a real `as_of_time` into
`validate_end_of_day_completeness()`'s underlying `build_market_reality_snapshot()` call) should be
implemented and re-verified in a follow-up phase before this becomes **C — external blocker only**.
No other software blocker is currently known.
