# Phase 19.14.1 — Autonomous Runtime Commissioning Hardening

Implements the four fixable findings from Phase 19.14.0's audit. The fifth finding (Class C,
FYERS daily manual authentication) is external and cannot be automated around — it is preserved
exactly as found, and this phase documents the operational contract around it rather than
weakening it.

## Narrow audit performed before implementation

Before writing any code, the deployed Phase 19.14.0 state was re-read directly: the current
`deploy/bujji-daily-intelligence.service` (`Type=simple`, `Restart=always`, `RestartSec=5`, no
timer), the existing `bujji-orb-vwap-legacy.service` convention (for the timer's structural
style), `bujji/shadow_runtime/daily_session.py`, `live_intelligence_cycle.py`,
`daily_intelligence_artifact.py`, `bujji/shadow_runtime/completeness.py` and `replay_equivalence.py`
(both already built, both confirmed still unwired into the daily path), and the two legacy scripts
(`scripts/run_shadow_live_observatory.py`, `run_live_shadow.py`) named in the 19.14.0 finding —
confirming exactly where each constructs its broker, so the new guard could be inserted before any
of them touch it.

## 1. Systemd timer + oneshot service

`deploy/bujji-daily-intelligence.service` changed:
- `Type=simple` → `Type=oneshot`.
- `Restart=always` / `RestartSec=5` removed entirely — a oneshot's job is to run once and stop;
  Phase 19.14.0 found the old combination would have relaunched the process roughly every 5
  seconds, continuously, since `DailySessionRuntime.run()` completes and returns rather than
  looping.
- `[Install] WantedBy=multi-user.target` removed — a oneshot triggered by a timer is enabled via
  the timer's own `[Install]` section, never the service's, so an operator cannot accidentally
  re-create daemon-like always-on registration by enabling the service directly.

New `deploy/bujji-daily-intelligence.timer`:
- `OnCalendar=Mon..Fri 09:00 Asia/Kolkata` — weekends excluded structurally by the calendar
  expression itself (verified with `systemd-analyze calendar`, which confirms both the expression
  parses and that its next elapse correctly lands on a weekday). The VPS's own system timezone is
  already `Asia/Kolkata` (confirmed in Phase 19.14.0), so no UTC/IST conversion risk.
- `Persistent=true` — a missed fire (VPS down at 09:00 IST) still runs once the machine is back up,
  covering "survive VPS reboot appropriately" without requiring a second cron-style catch-up
  mechanism.
- No `Unit=` override — relies on systemd's own same-name convention (`foo.timer` → `foo.service`),
  confirmed absent via a direct test rather than assumed.
- Overlap prevention has two independent layers, both disclosed in the timer file's own comments:
  systemd's own single-instance-per-unit-name semantics (a second `OnCalendar` fire while the
  service unit is still active is deferred, not run in parallel), and the pre-existing,
  application-level `ProcessLock` (Phase 19.12, unchanged) — which additionally protects against a
  *manually* invoked `run_daily_intelligence_session.py` overlapping a timer-fired one, a case
  systemd's own unit semantics do not cover.

**NSE holidays are explicitly not excluded** — no holiday calendar data source exists anywhere in
this codebase (confirmed absent in Phase 19.14.0), and building one is out of this phase's scope
(no new data source, no new engine, per the phase's own architectural constraints). On a holiday
the timer still fires; `scripts/capture_market_reality_session.py`'s own pre-existing
`within_market_hours()` weekday-and-clock check does not know about holidays either, so the most
likely outcome is a real, honest capture/completeness failure recorded on the heartbeat — fails
safe, not fails silent, even though the trigger itself is not holiday-aware. This is disclosed as
a known limitation, not fixed, since fixing it would require a new data source this phase is
explicitly told not to add.

Neither unit was installed, enabled, or started — confirmed both by never running `systemctl
enable`/`start`/`cp ... /etc/systemd/system/` and by `systemd-analyze verify` against both files in
place (`deploy/`, not `/etc/systemd/system/`).

## 2. EOD completeness wired into the authoritative daily path

`bujji/shadow_runtime/completeness.py`'s `validate_end_of_day_completeness()` (Phase 19.11,
**unmodified**) is now called from `run_daily_intelligence_session.py`'s new
`_make_real_completeness_fn()`, injected into `DailySessionRuntime` via a new, additive
`completeness_fn` constructor parameter (`None` by default — every pre-existing caller/test keeps
behaving byte-for-byte identically). `daily_session.py`'s `run()` calls it after the intelligence
stage, records the result on a new `CompletenessCheckResult` (daily_session.py's own thin result
type, mirroring the existing `CaptureResult`/`IntelligenceRunResult` convention — it carries the
canonical validator's output, it does not recompute it), and surfaces three new, additive fields on
`DailySessionHeartbeat`: `completeness_status`, `missing_reality_components`, and (from the replay
check) `replay_equivalent`. An incomplete day (`is_complete=False`) is recorded as an
`eod_completeness_incomplete` error and pushes the session to `FAILED` — never silently marked
`SESSION_COMPLETE` over a day the store itself says is incomplete.

## 3. LIVE vs HISTORICAL_REPLAY equivalence wired into the authoritative daily path

`bujji/shadow_runtime/replay_equivalence.py`'s `validate_live_replay_equivalence()` (Phase 19.13,
**unmodified**) is now called from inside `live_intelligence_cycle.run_live_intelligence_cycle()`
when a new, additive `include_replay_equivalence: bool = False` parameter is `True` (which
`run_daily_intelligence_session.py`'s `_make_real_intelligence_fn` now always passes). It runs
immediately after a successful LIVE composition, against the exact same `reality_snapshot`/
`spot_candles`/`as_of_time` the LIVE cycle itself just used — never a second fetch, never a
re-derived comparison — and compares exactly the canonical fields the task named: intelligence
fingerprint, environment classification, and recommended decision posture (phenomena and state
transition are part of the same composed `IntelligenceHeartbeatCycle` the fingerprint already
covers — `validate_live_replay_equivalence` does not currently expose them as separate compared
fields, and this phase did not touch that function's own comparison logic, per "do not create a
second equivalence mechanism").

The result flows back through `LiveIntelligenceCycleResult` → `IntelligenceRunResult` (3 new,
additive fields: `replay_equivalent`, `replay_mismatches`, `replay_check_error`) →
`DailySessionHeartbeat.replay_equivalent`. A **mismatch** (`equivalent=False`) is recorded as
`replay_equivalence_mismatch` and pushes to `FAILED`, never coerced to `True`. A **check failure**
(the comparison itself couldn't run — caught separately) is recorded as `replay_check_failed`,
distinct from a mismatch, and also never reported as `equivalent=True`. The already-composed LIVE
result itself is never discarded or altered by either failure mode — only the equivalence field
reflects the problem.

`daily_intelligence_artifact.DailyIntelligenceArtifact` also gained an additive
`replay_equivalence: Optional[Dict]` field, so the full equivalence report (not just the summary
boolean) is preserved on the per-cycle artifact itself, not only the daily heartbeat.

## 4. Ordering

Actual execution order in `DailySessionRuntime.run()`: **CAPTURE → INTELLIGENCE (LIVE composition
+ HISTORICAL REPLAY composition + LIVE/REPLAY EQUIVALENCE comparison, all three fused inside
`run_live_intelligence_cycle`) → EOD COMPLETENESS → FINAL ARTIFACT → SESSION_COMPLETE/FAILED.**

This is a **deliberate, disclosed reordering** relative to the task's literal listed sequence
(`CAPTURE → INTELLIGENCE → EOD COMPLETENESS → HISTORICAL REPLAY → LIVE/REPLAY EQUIVALENCE →
FINAL ARTIFACT`). The reason: HISTORICAL REPLAY and LIVE/REPLAY EQUIVALENCE are not independently
useful steps — the replay composition only makes sense run against the exact same inputs the LIVE
cycle just used, which are only in scope inside `run_live_intelligence_cycle` itself, not back at
the `daily_session.py` orchestration layer (which has no reality/intelligence-shaped objects of
its own). Splitting them into two separate round-trips through `DailySessionRuntime`'s generic
capture/intelligence/completeness injection surface would have required either re-fetching live
data a second time (a real, new broker call this phase's own constraints forbid — "no new capture
loop") or threading heavy reality/candle objects through `daily_session.py`'s otherwise
decoupled, reliability-only contract. Running the replay check immediately inside the same function
that already holds those objects avoids both. EOD completeness and the replay check are otherwise
fully independent of each other (neither's correctness depends on which ran first — EOD
completeness reflects the historical store's full-day state; the replay check reflects
composition determinism on the live cycle's own inputs), so this reordering changes no
correctness guarantee: incomplete capture still never fabricates confidence, a replay mismatch is
still never marked `True`, and both are still recorded before the final artifact and
`SESSION_COMPLETE`/`FAILED` decision.

## 5. FYERS authentication boundary — preserved, not automated

No change to `_build_real_broker()`'s credential handling, `disable_live_execution`, or
`docs/FYERS_TOKEN_LIFECYCLE.md`'s own documented limitation. Nothing in this phase scrapes
credentials, invents a refresh path, or weakens the existing fail-loudly-if-unset behavior.

**Operational contract** (documented here, not automated):

1. A human performs the FYERS interactive login (browser + TOTP/PIN) and updates
   `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` before each trading day — this cannot be automated
   per FYERS's own SEBI-driven restriction (`docs/FYERS_TOKEN_LIFECYCLE.md`, live-verified
   2026-07-19).
2. Once authenticated, `bujji-daily-intelligence.timer` fires `run_daily_intelligence_session.py`
   automatically at 09:00 IST on weekdays — no further human action needed for a normal day.
3. The human only needs to intervene again if: the token has expired/was never refreshed
   (surfaces as an honest `FYERS_APP_ID / FYERS_ACCESS_TOKEN not set` or
   `broker_connect_failed` error on the heartbeat — see
   `test_fyers_authentication_failure_is_recorded_honestly_not_retried_within_process`), or any
   other Class B/A finding from Phase 19.14.0 that this phase did not already close (none remain —
   see verdict below).

## 6. Manual entrypoint boundary

New module `bujji/shadow_runtime/manual_entrypoint_guard.py`:
`refuse_if_authoritative_runtime_active(lock_path="data/daily_intelligence.lock")` — a
non-blocking probe (attempt-`ProcessLock.acquire()`-then-immediately-release, the same technique
`bujji.shadow_runtime.status._lock_is_live` already established in Phase 19.12) that raises
`AuthoritativeRuntimeActiveError` if the authoritative daily runtime currently holds the lock, and
returns silently otherwise. **Not a second locking mechanism** — reuses `ProcessLock` itself, never
holds the lock for its own duration (verified by `test_guard_probe_does_not_itself_hold_the_lock`:
after a successful probe, the real owner can still acquire immediately). **Not a scheduler** —
starts nothing, waits for nothing, retries nothing.

Both legacy scripts named in the 19.14.0 finding now call this guard, before any broker
construction:
- `scripts/run_shadow_live_observatory.py`'s `main()` — before `FyersBroker(config, logger)`.
- `run_live_shadow.py`'s `_run_live()` — before `_pre_market_checklist()` even begins (which is
  itself before the broker connects).

Both files also gained a short deprecation-notice paragraph in their module docstrings pointing at
the authoritative daily runtime. Neither script's own decision/session logic was touched beyond
this refusal check and the docstring note.

## Files changed / added

| File | Change |
|---|---|
| `bujji/shadow_runtime/daily_session.py` | Additive: `CompletenessCheckResult`, `completeness_fn` param, 3 new `DailySessionHeartbeat` fields, 3 new `IntelligenceRunResult` fields, `completeness_result` on `DailySessionReport`, new orchestration steps in `run()`. |
| `bujji/shadow_runtime/live_intelligence_cycle.py` | Additive: `include_replay_equivalence` param, `replay_equivalence`/`replay_check_error` on `LiveIntelligenceCycleResult`. |
| `bujji/shadow_runtime/daily_intelligence_artifact.py` | Additive: `replay_equivalence` field. |
| `bujji/shadow_runtime/manual_entrypoint_guard.py` | New. |
| `run_daily_intelligence_session.py` | New `_make_real_completeness_fn`; `_make_real_intelligence_fn` now requests replay equivalence and forwards its result. |
| `scripts/run_shadow_live_observatory.py` | Guard call added before broker construction; deprecation note. |
| `run_live_shadow.py` | Guard call added before the pre-market checklist; deprecation note. |
| `deploy/bujji-daily-intelligence.service` | `Type=simple`→`oneshot`, `Restart=always`/`RestartSec=5` removed, `[Install]` removed. |
| `deploy/bujji-daily-intelligence.timer` | New. |
| `tests/test_phase_19_14_1_commissioning_hardening.py` | New, 27 tests covering all 15 required scenarios. |

## Verification performed

All 15 required test scenarios pass (27 tests total — several scenarios have more than one test):
oneshot semantics, timer/service ownership, no-restart-loop, weekend/off-day handling,
completeness gate success/failure, replay equivalence success/mismatch/check-failure (including
one real end-to-end proof via `run_live_intelligence_cycle` against a production-shaped
`FakeBroker`, not just a stubbed `DailySessionRuntime` test), incomplete-capture-with-successful-
intelligence, crash/restart recovery, `ProcessLock` interaction (including the manual-invocation
refusal scenario), FYERS authentication failure, and the safety-boundary AST/call-level checks
(with `run_live_shadow.py`'s pre-existing, unrelated `PaperBroker.place_order` simulation call
correctly distinguished from a real order-placement risk, and independently re-confirmed that its
real `FyersBroker` is still wrapped by `disable_live_execution`).

`systemd-analyze verify` against both new/changed unit files reports no errors.
`systemd-analyze calendar "Mon..Fri 09:00 Asia/Kolkata"` confirms the expression parses and its
next elapse lands on a weekday, at the VPS's own system time.

Full regression: **5,956 passed, 0 failed** (5,929 baseline + 27 new Phase 19.14.1 tests).

## Implementation-Phase Verdict (superseded by the Final Verification Gate below)

At the time the implementation above was completed, every Class B software gap from Phase 19.14.0
appeared closed by test evidence alone. **This verdict was superseded** by the Final Verification
Gate (below), which found one additional, real, previously-undetected filesystem defect that test
suites alone could not surface — see that section for the actual, current verdict.

---

# Final Verification Gate

Static + runtime verification only. **Nothing was installed, enabled, started, stopped, or
restarted** — confirmed throughout by `systemctl is-enabled`/`is-active` returning `not-found` for
both units before and after this gate, and by never invoking `systemctl enable/start/stop/restart`
or copying either unit file into `/etc/systemd/system/`.

## 1. Full Regression

| | Count |
|---|---|
| Baseline (before Phase 19.14.1) | 5,929 |
| New (Phase 19.14.1) | 27 |
| **Final total** | **5,956** |
| Failures | **0** |
| Errors | **0** |
| Skipped (material) | 0 |

Ran twice during this gate (once before this verification pass began, once as this gate's own
required check) — both runs: `5,956 passed, 0 failed, 1 warning` (the warning is a pre-existing,
unrelated `pkg_resources` deprecation notice from the `fyers_apiv3` SDK dependency, not from any
code this project owns).

## 2. Systemd Static Verification

`systemd-analyze verify deploy/bujji-daily-intelligence.service` → exit 0, no output (clean).
`systemd-analyze verify deploy/bujji-daily-intelligence.timer` → exit 0, no output (clean).
Both run directly against the files in `deploy/`, never against `/etc/systemd/system/` (neither
file exists there).

**SERVICE**, verified by direct read of the deployed file:
- `Type=oneshot` — present, confirmed.
- `Restart=always` — absent (removed in the implementation phase).
- Restart-loop behavior — structurally impossible: no `Restart=` directive at all means systemd
  never relaunches this unit on its own after any exit; the comment block explains the
  deliberate choice not to auto-retry same-day (a same-day retry could collide with a human's
  FYERS re-authentication in progress).
- `ExecStart` — `run_daily_intelligence_session.py --date-today --heartbeat-path
  /opt/bujji/app/data/daily_session_heartbeat.json --lock-path
  /opt/bujji/app/data/daily_intelligence.lock --cycle-artifact-store-path
  /opt/bujji/app/data/shadow_intelligence_cycle_artifacts.jsonl --daily-artifact-store-path
  /opt/bujji/app/data/daily_intelligence_artifacts.jsonl` — all four paths point under
  `/opt/bujji/app/data`, matching `WorkingDirectory` and `ReadWritePaths`.
- `User=bujji` / `Group=bujji` — matches the real `bujji` system user confirmed to exist
  (`id bujji` → `uid=997(bujji) gid=987(bujji)`).
- `WorkingDirectory=/opt/bujji/app` — matches the real application directory.
- `EnvironmentFile=/opt/bujji/.env` — matches the real credentials file location (confirmed to
  exist, `-rw-------  bujji bujji`).
- Lock path (`--lock-path`), heartbeat path (`--heartbeat-path`), and both artifact-store paths —
  all under `/opt/bujji/app/data`, all covered by `ReadWritePaths=/opt/bujji/app/logs
  /opt/bujji/app/data /opt/bujji/.env`.
- Dependencies — `After=network-online.target`, `Wants=network-online.target`; correct for a unit
  that needs real network access to FYERS.

**TIMER**, verified by direct read plus `systemd-analyze calendar`:
- `OnCalendar=Mon..Fri 09:00 Asia/Kolkata` — confirmed valid: `systemd-analyze calendar` normalizes
  it to `Mon..Fri *-*-* 09:00:00 Asia/Kolkata` and computes a real next-elapse time
  (`Mon 2026-08-17 09:00:00 IST` / `Mon 2026-08-17 03:30:00 UTC`) that correctly lands on a
  weekday.
- Intended NSE/trading-day schedule — weekdays at 09:00 IST, ahead of NSE's 09:15 open, matching
  the buffer convention already used elsewhere in this project (e.g. the Phase 17F.7.1 discovery
  script's own 09:30 buffer).
- `Persistent=true` — present and intentional: a missed fire (VPS down at 09:00) still runs once
  the VPS is back, so a reboot during the trigger window does not silently skip a trading day.
- No unintended duplicate scheduling — confirmed by `systemctl list-timers --all` on the live VPS:
  no `bujji-daily-intelligence.timer` is present (not installed) and no other listed timer
  references this service; `crontab -l` for both `root` and `bujji` returns "no crontab" (no
  hidden cron-based duplicate trigger exists).
- No `Unit=` override — confirmed absent from the file, relying on systemd's own same-name
  `foo.timer` → `foo.service` convention (re-verified as part of this gate, not just assumed).
- Overlap risk beyond `ProcessLock` — systemd's own single-instance-per-unit-name semantics are the
  first layer (a second `OnCalendar` fire while the oneshot is still active is deferred, not
  parallelized); `ProcessLock` is the second, independent layer, live-proven in §4 below.

**Weekend/holiday wake-and-reject, explicitly confirmed intentional**: the timer's calendar
expression itself excludes Saturday/Sunday (`Mon..Fri`), so it does not wake on weekends at all.
It **does** wake on NSE holidays that fall on a weekday, since no holiday calendar data source
exists in this codebase (confirmed absent in Phase 19.14.0's audit, and out of this phase's scope
to build). On such a day, `scripts/capture_market_reality_session.py`'s own pre-existing
`within_market_hours()` check does not know about holidays either — the real, load-bearing safety
net is that capture will very likely observe a market that never opens as expected and the session
will report an honest `FAILED`/incomplete result (never a fabricated normal trading day). This is
the documented, intentional design from the implementation phase, re-confirmed here rather than
re-asserted without re-reading the file.

## 3. Deployment Conflict Audit

Checked directly on the live VPS, nothing stopped or killed:

| Check | Result |
|---|---|
| `bujji-daily-intelligence.service`/`.timer` installed? | **No** — absent from `/etc/systemd/system/`, `systemctl is-enabled` returns `not-found` for both |
| `bujji-orb-vwap-legacy.service` | Present, but `disabled`/`inactive` — no conflict |
| Other `bujji*` systemd units | `systemctl list-units --all 'bujji*'` → 0 loaded units |
| `crontab -l` (root), `crontab -u bujji -l` | Both: "no crontab" |
| All systemd timers (`systemctl list-timers --all`) | 18 timers listed, all standard OS/package timers (apt, logrotate, fstrim, etc.) — none reference any Bujji unit |
| `run_daily_intelligence_session.py` process | Not running |
| `bujji_options_os_runner.py` process | Not running |
| `scripts/run_shadow_live_observatory.py` process | Not running |
| **`run_live_shadow.py --live` process** | **RUNNING** — PID 787565, started **2026-07-31**, still active (`ps -ef`: `/opt/bujji/.venv/bin/python run_live_shadow.py --live --bhavcopy ... --bhavcopy-day 2026-07-30 --log-file logs/lsq1_day1.log`) |

**Real, disclosed finding**: a `run_live_shadow.py --live` process has been running continuously
for **16 days** (since 2026-07-31, before Phase 19.14.1's code existed). It holds its own,
separate lock (`data/live_shadow_operator.lock`, confirmed present on disk), **not**
`data/daily_intelligence.lock` — so it does not collide on the shared lock file the new daily
runtime uses. But because this process loaded the *old* `run_live_shadow.py` source into memory
before this phase's `refuse_if_authoritative_runtime_active()` guard existed, **it does not carry
that guard** — it was never re-executed with the new code. This is not a defect in the guard
itself (a freshly-launched `run_live_shadow.py --live` today would carry it, and the guard's own
refusal behavior was live-proven in §4 below) — it is a live-state fact about an already-running
process that predates this phase, disclosed here rather than silently ignored, and not stopped per
this gate's explicit instruction not to kill anything. An operator should be aware that this
specific long-running instance remains capable of live broker access independent of the new daily
runtime's lock until it exits or is restarted.

`data/daily_intelligence.lock` itself does not currently exist (confirmed — no daily runtime has
ever executed for real), so there is no live conflict on the actual lock the new runtime will use.

**Conclusion**: exactly one automatable path can reach capture/intelligence
(`run_daily_intelligence_session.py`), and it is not currently installed or scheduled anywhere. No
competing *scheduler* exists. One long-running *manual* process (`run_live_shadow.py`, pre-dating
this phase) remains active and is disclosed above rather than hidden.

## 4. Process Lock Proof (live, not just documentation)

Executed directly on the VPS against the real `bujji.core.process_lock.ProcessLock` and the real
`bujji.shadow_runtime.manual_entrypoint_guard.refuse_if_authoritative_runtime_active`, using the
real `data/daily_intelligence.lock` path (created and cleaned up by this proof, not left behind):

```
A1: authoritative acquired: True
A2 OK: second daily runtime refused: Another instance already holds the lock at 'data/daily_intel...
A3 OK: manual entrypoint guard refused: The authoritative Bujji daily intelligence runtime currently...
released
B1 OK: fresh daily runtime acquired after release: True
```

- **A** (duplicate prevention): a second `ProcessLock` on the same path raises
  `LockAcquisitionError` while the first is held — proven live, not mocked.
- **A, legacy entrypoint**: `refuse_if_authoritative_runtime_active()` — the exact function both
  `scripts/run_shadow_live_observatory.py` and `run_live_shadow.py` call before touching a broker
  — raises `AuthoritativeRuntimeActiveError` while the authoritative lock is held. Since both
  scripts call this identical function (confirmed by source inspection and by
  `test_both_legacy_scripts_call_the_guard_before_broker_construction`), this one live proof covers
  both named legacy entrypoints without needing to launch either script's full, heavier startup
  sequence (which would require live FYERS credentials/market hours to reach far enough to be
  meaningful).
- **B** (recovery): after `authoritative.release()`, a brand-new `ProcessLock` instance
  immediately acquires the same path successfully — proving the lock does not wedge after a clean
  release, matching the OS-`flock` design's own guarantee.

## 5. Daily Pipeline Order (live call-tracing, not just static reading)

Instrumented `DailySessionRuntime.run()` live on the VPS with injected `capture_fn`/
`intelligence_fn`/`completeness_fn` stubs that each append a marker to a shared list, run through
the real, deployed `daily_session.py`:

```
CAPTURE -> INTELLIGENCE_LIVE -> INTELLIGENCE_REPLAY -> LIVE_REPLAY_EQUIVALENCE -> EOD_COMPLETENESS -> FINAL_ARTIFACT_BUILT -> SESSION_COMPLETE
ORDER PROOF: PASS
```

- Completeness precondition gating intelligence: this refers to the **per-cycle** completeness
  gate (`completeness_gate.evaluate_completeness_gate`, evaluated *inside*
  `run_live_intelligence_cycle` before composition) — already proven not to run composition on a
  gate failure by `test_live_cycle_never_composes_intelligence_when_gate_fails` (Phase 19.13) and
  `test_completeness_gate_fails_closed_on_missing_options`, both re-run clean in §1's regression.
- EOD completeness runs after capture: proven by the marker order above (`CAPTURE` before
  `EOD_COMPLETENESS`).
- Replay runs after live intelligence, equivalence runs after replay: proven by the marker order
  (`INTELLIGENCE_LIVE` → `INTELLIGENCE_REPLAY` → `LIVE_REPLAY_EQUIVALENCE`) — all three fused
  inside one call to `run_live_intelligence_cycle`, as documented (see the implementation section's
  own disclosed, deliberate reordering rationale — HISTORICAL REPLAY and LIVE/REPLAY EQUIVALENCE
  execute logically after EOD COMPLETENESS in the task's literal listed order, but are fused with
  the live composition step here since they need the same in-scope reality/candle objects; this
  changes no correctness guarantee, as re-confirmed by this live trace still showing every check
  completing before the final artifact and terminal stage).
- Final artifact written after all required checks, `SESSION_COMPLETE` cannot be reported while
  checks are unresolved: the marker order shows `FINAL_ARTIFACT_BUILT` strictly after every check
  marker, and `SESSION_COMPLETE` only appears as the very last item, matching `daily_session.py`'s
  own `run()` logic (final_stage is computed from the accumulated `errors` list only after every
  step, including the completeness check, has executed or been skipped-with-reason).

## 6. Failure Semantics (live, distinguishable proof)

Six live-executed cases against the real, deployed `DailySessionRuntime`:

```
A capture_failure:       final_stage=FAILED errors=['capture_error: broker timeout']
B incomplete_reality:    final_stage=FAILED errors=["eod_completeness_incomplete: status=PARTIAL missing=['vix']"]
C intelligence_failure:  final_stage=FAILED errors=['intelligence_error: broker_connect_failed: timeout']
D replay_check_failure:  final_stage=FAILED errors=['replay_check_failed: ValueError: no candles']
E replay_mismatch:       final_stage=FAILED errors=["replay_equivalence_mismatch: ['intelligence_fingerprint mismatch']"]
F success:                final_stage=SESSION_COMPLETE errors=[]
```

Every failure carries a **distinct, prefixed reason** (`capture_error:`, `eod_completeness_incomplete:`,
`intelligence_error:`, `replay_check_failed:`, `replay_equivalence_mismatch:`) — never a bare
`"failed"` with no distinguishing content. In particular, **B ≠ C** (`eod_completeness_incomplete`
vs `intelligence_error` — an incomplete store is not conflated with a broker/composition failure)
and **D ≠ E** (`replay_check_failed`, the comparison itself couldn't run, vs
`replay_equivalence_mismatch`, the comparison ran and disagreed) — exactly the two distinctions the
gate asked to verify.

## 7. Real Production Data Smoke Test

Used the real `HistoricalObservationStore` at `data/historical_reality/normalized/historical_observations.db`
(1,691,496,448 bytes on disk, 670,163 total rows). Queried directly for a date with real,
non-trivial FIVE_MINUTE-resolution data across all four instrument types and found
**2026-08-14**: 75 SPOT rows, 75 INDEX (VIX) rows, 77 FUTURE rows, 162,150 OPTION rows
(09:15–15:25/15:35 IST).

Built a real `MarketRealitySnapshot` via `build_market_reality_snapshot('2026-08-14', ...,
resolution=RESOLUTION_FIVE_MINUTE, as_of_time='2026-08-14T14:00:00+05:30')` — result:
**completeness=COMPLETE**, real spot close **24395.55**, real VIX close **11.32**, real options
chain with **2,190 contracts**.

Ran `build_intelligence_heartbeat_cycle()` twice against this identical real snapshot — once
`execution_mode=LIVE`, once `execution_mode=HISTORICAL_REPLAY`:

| Field | LIVE | HISTORICAL_REPLAY | Equal? |
|---|---|---|---|
| Intelligence fingerprint | `95d14ac5049f7464afb10b0391b676c37344bd17c629a4ac9b7a9d6ef14cb19e` | `95d14ac5049f7464afb10b0391b676c37344bd17c629a4ac9b7a9d6ef14cb19e` | **True** |
| Environment classification | `STAND_ASIDE` | `STAND_ASIDE` | **True** |
| Recommended posture | `FAVOR_PREMIUM_ENVIRONMENT` | `FAVOR_PREMIUM_ENVIRONMENT` | **True** |
| Phenomena | `[]` (none detected this cycle — no prior snapshot supplied for comparison) | `[]` | **True** |
| State transition | `None` (no prior state node supplied) | `None` | **True** |

`session date=2026-08-14`, `spot=24395.55`, `options=2190 contracts available`, `VIX=11.32
available`. **LIVE == HISTORICAL_REPLAY confirmed on real production data**, not a synthetic
fixture — the same proof technique Phase 19.10.1 first established, re-run here against a
different, larger real dataset than any prior phase used.

No broker was constructed or connected for this test — pure composition over already-stored real
data, zero live market/network calls, zero order risk.

## 8. Broker Safety Proof

Re-ran the relevant AST/static/live checks (all clean, `40 passed` combining
`test_phase_19_13_live_intelligence_bridge.py` + `test_phase_19_14_1_commissioning_hardening.py`):

- `run_daily_intelligence_session.py`'s `_build_real_broker()` wraps `FyersBroker(cfg, log)` in
  `disable_live_execution(...)` (`bujji.broker.guard`) before returning it — confirmed by direct
  read and by `test_run_daily_intelligence_session_still_wraps_broker_with_disable_live_execution`.
- AST walk over every `ast.Attribute` node in `run_daily_intelligence_session.py`,
  `bujji/shadow_runtime/daily_session.py`, `live_intelligence_cycle.py`,
  `daily_intelligence_artifact.py`, and `scripts/run_shadow_live_observatory.py`: zero
  `.place_order(`/`.modify_order(`/`.cancel_order(` calls
  (`test_changed_files_never_call_order_placement_attrs`).
- `run_live_shadow.py`'s one legitimate `.place_order(` call targets `paper_broker` (a pure
  simulation object, pre-existing and unrelated to the real, `disable_live_execution`-wrapped
  `FyersBroker`) — confirmed line-by-line
  (`test_run_live_shadow_real_broker_wrapped_before_paper_broker_simulation`).
- No import of `order`/`position`/`strategy`/`execution`-named modules anywhere in the new Phase
  19.14.1 module (`manual_entrypoint_guard.py`).
- `MarketDataAdapter` (the only broker-facing class the daily intelligence path actually calls)
  exposes only `get_spot`/`get_vix`/`get_option_chain`/`get_futures_quote`/`get_recent_candles` —
  none capable of mutating broker state — re-confirmed by direct class read.

No real broker order call was made or attempted anywhere in this gate.

## 9. FYERS Authentication Contract

Live-verified, not merely re-asserted: with `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` stripped from a
subprocess environment (the real `/opt/bujji/.env` file was never touched), calling
`_build_real_broker()` raises `RuntimeError("FYERS_APP_ID / FYERS_ACCESS_TOKEN not set in
environment")` — a clear, real, actionable failure, not a silent pretend-success. When invoked
through the full `DailySessionRuntime`, this surfaces as `intelligence_error: FYERS_APP_ID /
FYERS_ACCESS_TOKEN not set in environment` on both the returned report and the persisted heartbeat
(`last_error`) — live-demonstrated in §6, case C's underlying mechanism.

**Exact human action required**: before each NSE trading day, a human must complete FYERS's
interactive login flow (browser + TOTP/PIN) and place a currently-valid access token into
`FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` (`EnvironmentFile` for the service). This cannot be
automated — FYERS's own refresh-token API is disabled per a SEBI-driven restriction
(`docs/FYERS_TOKEN_LIFECYCLE.md`, live-verified 2026-07-19) — and this gate did not attempt to
bypass, automate, or weaken that boundary in any way.

## 10. Filesystem / Service User Check

Checked directly as the real `bujji` user (`sudo -u bujji ...`), without exposing credential
contents:

| Path | Readable by `bujji`? | Writable by `bujji`? |
|---|---|---|
| `/opt/bujji/app` (application dir) | Yes | Yes |
| `/opt/bujji/.env` (credentials) | Yes | — (not required to write) |
| `/opt/bujji/app/data` (heartbeat/lock/artifact directory) | Yes | Yes |
| `/opt/bujji/app/logs` | Yes | Yes |
| `/opt/bujji/.venv/bin/python` | Yes (executable) | — |
| **`/opt/bujji/app/data/historical_reality/normalized/historical_observations.db`** | Yes | **NO** |
| **`/opt/bujji/app/data/historical_reality/normalized/` (containing directory)** | Yes | **NO** |

**Real, live-verified defect found by this gate**: `ls -la` shows
`/opt/bujji/app/data/historical_reality/normalized/` and the `historical_observations.db` file
inside it are owned `root:root`, mode `755`/`644` — group/other have no write permission. A direct
write-transaction attempt as the `bujji` user against the real file confirms this is not a
theoretical gap:

```
$ sudo -u bujji python -c "import sqlite3; c=sqlite3.connect('.../historical_observations.db'); c.execute('BEGIN IMMEDIATE')"
WRITE TEST FAILED (as expected): OperationalError attempt to write a readonly database
```

**Consequence**: `scripts/capture_market_reality_session.py`/`capture_options_reality_session.py`
both call `HistoricalObservationStore.write()` against this exact file. Running the daily service
as `User=bujji` (as the unit file specifies), capture would fail with a permission error on
**every single invocation**, independent of FYERS authentication being valid. This is a real
software/infrastructure defect this gate's own filesystem check was specifically designed to
catch — and did.

**This is not a code defect** (no Python logic is wrong) and **not fixed by this gate** — it is a
one-line VPS ownership correction outside this phase's "minimum required code change" scope, and
changing ownership of a 1.6GB production data file is exactly the kind of system-modifying action
this gate's own instructions say to surface, not silently execute. The fix, for the operator to
run before commissioning:

```bash
chown -R bujji:bujji /opt/bujji/app/data/historical_reality
```

## 11. 20–30 Session Commissioning Readiness

| # | Question | Verdict | Evidence |
|---|---|---|---|
| 1 | Will every intended trading day trigger exactly one authoritative run? | **PARTIAL** | The timer will fire exactly once per weekday (§2) — but every fire's capture step will currently fail on the §10 permission defect, independent of triggering correctly. Triggering = PASS; a *successful* run today = FAIL until §10 is fixed. |
| 2 | Can two runs overlap? | **PASS** | §4: `LockAcquisitionError` proven live on a second concurrent attempt; systemd's own single-instance semantics as a second layer. |
| 3 | Can a VPS reboot cause duplicate capture? | **PASS** | `ProcessLock` is OS-`flock`-scoped to the open file description — auto-released on any process death including a reboot; `Persistent=true` covers a missed fire without double-firing. |
| 4 | Can a crashed run be restarted safely? | **PASS** | §4's release→reacquire proof; `test_crash_then_fresh_instance_recovers` (re-run clean in §1); `HistoricalObservationStore`/`EventStore` both idempotent by content key. |
| 5 | Will incomplete market data be detected? | **PASS** | §6 case B: `eod_completeness_incomplete` recorded and surfaced, session marked `FAILED`, never silently `SESSION_COMPLETE`. |
| 6 | Will intelligence failure be visible? | **PASS** | §6 case C: `intelligence_error` recorded distinctly, surfaced on heartbeat's `last_error`. |
| 7 | Will replay failure be visible? | **PASS** | §6 case D: `replay_check_failed` recorded distinctly from a mismatch. |
| 8 | Will LIVE/REPLAY disagreement be visible? | **PASS** | §6 case E: `replay_equivalence_mismatch` recorded, never coerced to `equivalent=True`; §7's real-data run additionally proves the *agreement* path works correctly when inputs are identical. |
| 9 | Will the complete artifact survive restart? | **PASS** | `EventStore.append`'s first-write-wins idempotency; `hydrate_daily_intelligence_artifacts`/`hydrate_cycle_artifacts` cross-session hydration, re-verified passing in §1. |
| 10 | Can a legacy/manual runtime silently compete? | **PARTIAL** | For any *future* invocation of the two named legacy scripts: **No** — live-proven refusal in §4. For the *currently-running* `run_live_shadow.py` process (§3, PID 787565, since 2026-07-31): it predates the guard and is not silently blocked by the new lock (though it also does not touch the new lock file, so it cannot corrupt the new runtime's own state) — disclosed, not hidden. |
| 11 | Can the intelligence path place an order? | **PASS** | §8: `disable_live_execution` wrapping confirmed; AST/call-level checks clean; no real order call attempted anywhere in this gate. |
| 12 | What human action is still required because of FYERS authentication? | **Documented, not a PASS/FAIL** | §9: daily interactive login (browser + TOTP/PIN), updating `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env`, before each trading day. External, unfixable in this codebase. |

## 12. Final Verdict

# **B — SOFTWARE BLOCKER REMAINS**

Every runtime/scheduling/locking/integration behavior this gate could exercise passed cleanly —
including two live, non-mocked proofs (§4 lock proof, §5 call-order trace) and one real-production-
data run (§7) that this project has not previously run at this scale for Phase 19.x. If the only
open item were the already-known FYERS interactive-authentication requirement, this would correctly
be **C — external blocker only**, per this gate's own instruction.

But it is not the only open item: **§10 found a real, live-verified filesystem ownership defect**
— `data/historical_reality/normalized/` and `historical_observations.db` are owned `root:root` and
not writable by the `bujji` service user the unit file specifies. This would cause **every single
capture attempt to fail**, independent of and in addition to the FYERS authentication requirement.
This is a software/infrastructure defect, not merely an external dependency, and per this gate's
own explicit instruction ("do not call it A simply because tests pass... if any software/runtime/
scheduling/locking/integration defect remains, classify B"), the correct classification is **B**.

**The fix is a single, well-understood, one-line operator action** (`chown -R bujji:bujji
/opt/bujji/app/data/historical_reality`), not a code change or an architectural rework — it was
intentionally not applied automatically by this gate, since modifying ownership of a 1.6GB
production data file is a system-level action this gate's own instructions direct should be
surfaced, not silently executed.

**Recommendation**: once an operator runs the one-line `chown` above, re-run §10's write-test
check (or simply re-run this gate) to confirm; at that point, with the FYERS interactive
authentication routine as the only remaining item, the verdict becomes **C — external blocker
only**, and the timer/service pair may be considered for installation.
