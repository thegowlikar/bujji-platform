# Phase 19.12 — Production Runtime Deployment & Session Ownership

## Objective

Convert the proven `DailySessionRuntime` (Phase 19.11) into a reliable, unattended NSE market
observation service. Infrastructure only — no changes to Reality models, Intelligence models,
Decision Intelligence, Strategy logic, or Execution logic.

## Scope boundary (verified, not assumed)

This service may **observe, capture, analyze, store**. It must never **place orders, modify
positions, execute strategies**. Verified structurally, not by convention: every new Phase 19.12
file is AST-inspected for imports containing `broker`/`order`/`position`/`strategy`/`execution`
— see `test_no_trading_decision_imports` in
`tests/test_phase_19_12_production_runtime.py`. All four new/changed files pass.

## What was built

### 1. Systemd service definition — `deploy/bujji-daily-intelligence.service`

Deployed to the VPS as a **plain file only**, never installed or enabled. Per this phase's own
safety boundary, installing/enabling a systemd unit is a system-configuration change I do not
perform myself — see "Installing the service" below for the exact commands to run yourself.

Modeled on the existing `deploy/bujji-orb-vwap-legacy.service` convention:
- `Restart=always` / `RestartSec=5` — restart on unexpected failure.
- `TimeoutStopSec=30`, `KillSignal=SIGTERM` — graceful-shutdown budget before SIGKILL.
- `WantedBy=multi-user.target` — starts automatically after reboot, once enabled.
- Security hardening: `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome`, `PrivateTmp`,
  narrowly scoped `ReadWritePaths`.
- `Type=simple`, single `ExecStart` line — combined with the lock (below), this guarantees at
  most one live instance.

### 2. Runtime lock ownership — reused, not reinvented

`run_daily_intelligence_session.py` now acquires `bujji.core.process_lock.ProcessLock` (a
pre-existing, already-used-by-`live_shadow_operator` crash-safe `flock`-based lock — Phase F4,
confirmed by reading the module directly) before constructing `DailySessionRuntime`, and releases
it in a `finally` block. Because the lock is OS-level (`flock`, scoped to the open file
description, not a PID file), a crashed or killed process's lock is released by the kernel itself
— a fresh instance never wedges waiting on a dead process's stale lock file.

This single lock covers all three required cases (duplicate daily sessions, duplicate capture,
duplicate intelligence cycles) because the daily session, capture, and intelligence cycle are all
owned by the one process this lock gates — there is no second entrypoint that could start any of
them independently.

### 3. Operational status command — `bujji_daily_status.py` / `bujji.shadow_runtime.status`

```
python bujji_daily_status.py [--heartbeat-path PATH] [--lock-path PATH] [--json]
```

Read-only. Reads the same `DailySessionHeartbeat` file `DailySessionRuntime` already writes
(Phase 19.11's own `write_daily_heartbeat()` — no second, competing store) and probes the lock
file's live/dead state by attempting a non-blocking acquire (the PID written inside the lock file
is informational only; the real liveness signal is the OS-level `flock`, per `ProcessLock`'s own
docstring). Reports exactly the five required fields:

- current lifecycle state
- last heartbeat
- current session date
- last successful observation
- last failure

### 4. Graceful shutdown handling

`run_daily_intelligence_session.py` installs `SIGTERM`/`SIGINT` handlers via
`asyncio.get_running_loop().add_signal_handler()` that cancel the running `DailySessionRuntime.run()`
task. `daily_session.py`'s `run()` method gained a new `except asyncio.CancelledError:` branch,
placed *before* the pre-existing generic `except Exception:` branch, so a deliberate stop is
distinguished from a genuine crash (recorded as `"graceful_shutdown: cancellation received"`
rather than `"unexpected_failure: ..."`). `run()` still never raises and still always finalizes a
real `DailySessionReport` and heartbeat — the same contract established in Phase 19.11.

VPS shutdown and `systemctl stop` both deliver `SIGTERM` (the unit's own `KillSignal=SIGTERM`),
so both are covered by the same handler; "manual stop" is either that or `Ctrl-C` (`SIGINT`),
also covered.

## Files changed / added

| File | Change |
|---|---|
| `bujji/shadow_runtime/daily_session.py` | Additive: `import asyncio`, new `except asyncio.CancelledError` branch in `run()`. No other logic touched. |
| `bujji/shadow_runtime/status.py` | New. `OperationalStatus`, `get_operational_status()`. |
| `bujji_daily_status.py` | New. CLI wrapper (repo root). |
| `run_daily_intelligence_session.py` | Rewritten from the Phase 19.11 version: added `--date-today`, `--lock-path`, `ProcessLock` acquire/release, SIGTERM/SIGINT handling. Capture/intelligence wiring (`_make_real_capture_fn`/`_make_real_intelligence_fn`) unchanged from 19.11, including the disclosed `intelligence_fn` real-broker limitation. |
| `deploy/bujji-daily-intelligence.service` | New. Deployed as a file only — not installed. |
| `tests/test_phase_19_12_production_runtime.py` | New. 12 tests (below). |

## Verification performed

All against the real VPS environment and the real `ProcessLock`/`DailySessionRuntime` classes —
none mocked at the interface level.

| Requirement | Test / evidence |
|---|---|
| Duplicate prevention | `test_duplicate_instance_is_refused` — second `ProcessLock.acquire()` raises `LockAcquisitionError` while the first is held. Also manually verified live on the VPS. |
| Reboot recovery | `test_reboot_recovery_lock_reacquirable_after_release` — a lock released (simulating a rebooted/crashed holder) is reacquirable by a fresh instance. Manually verified live on the VPS. |
| Crash restart | `test_crash_in_capture_is_recorded_not_raised` + `test_fresh_instance_after_crash_completes_normally` — a crashing `capture_fn` is caught, recorded, heartbeat written as `FAILED`; a second, fresh runtime against the same heartbeat path then completes normally. |
| Two consecutive trading sessions | `test_two_consecutive_sessions_do_not_leak_state` — day 1 and day 2 runtimes against the same heartbeat path each report their own `session_date`/`rows_captured_today`, no carryover. |
| Graceful shutdown | `test_graceful_shutdown_on_cancellation` — cancelling an in-flight `run()` task lands in the `CancelledError` branch, reports `"graceful_shutdown: ..."`, still returns a terminal report. |
| Operational status | `test_status_reports_all_five_required_fields`, `test_status_reflects_a_live_lock` — all five required fields present and correct; lock liveness correctly reflected. |
| No trading-decision imports | `test_no_trading_decision_imports` (parametrized over all 4 new/changed files) — AST-level import inspection, zero `broker`/`order`/`position`/`strategy`/`execution` imports found. |
| End-to-end dry-run wiring | Manually run live on the VPS: `python run_daily_intelligence_session.py --date 2026-08-15 --dry-run` — heartbeat written correctly, `bujji_daily_status.py` against that heartbeat renders correctly. |

**Test results**: 12/12 new tests pass. Full regression: **5,916 passed, 0 failed** (5,904
baseline + 12 new), no regressions introduced.

## Known, disclosed limitation (carried over from Phase 19.11, unchanged)

`_make_real_intelligence_fn()` in `run_daily_intelligence_session.py` is **not wired to a real
broker** — it returns an honest error (`"intelligence_fn not wired to a real broker -- see
run_daily_intelligence_session.py's own docstring"`) rather than silently pretending an
intelligence cycle ran. This means the service, once installed, will run **capture** successfully
every cycle but will report an intelligence-pipeline failure every cycle until real broker
credentials are wired in a future phase. This is deliberate — building fake success into an
infrastructure phase would corrupt every downstream heartbeat/status reading. `--dry-run` exists
for wiring verification independent of this limitation.

## Installing the service (you must run these yourself)

Per your own confirmed decision on this phase's scope, I do not install or enable systemd units.
The unit file is already deployed to `/opt/bujji/app/deploy/bujji-daily-intelligence.service`.
Run the following on the VPS as root:

```bash
cp /opt/bujji/app/deploy/bujji-daily-intelligence.service /etc/systemd/system/bujji-daily-intelligence.service
systemctl daemon-reload
systemctl enable bujji-daily-intelligence.service
systemctl start bujji-daily-intelligence.service
```

To check it afterward:

```bash
systemctl status bujji-daily-intelligence.service
journalctl -u bujji-daily-intelligence -f
cd /opt/bujji/app && /opt/bujji/.venv/bin/python bujji_daily_status.py --heartbeat-path /opt/bujji/app/data/daily_session_heartbeat.json --lock-path /opt/bujji/app/data/daily_intelligence.lock
```

To stop / disable:

```bash
systemctl stop bujji-daily-intelligence.service
systemctl disable bujji-daily-intelligence.service
```

Before enabling, note the disclosed limitation above: capture will run, intelligence will report
an honest failure every cycle until real broker wiring is completed.
