# Phase 19.14.0 — Autonomous Runtime Commissioning Audit

**Audit only. No code written, no code deployed, no systemd install/start/stop, no configuration
changed.** Every finding below was verified directly against the real VPS (`root@139.59.76.137`)
and the real repository at `/opt/bujji/app` — process listing, `systemctl`, file ownership,
`crontab`, and direct reads of the actual source, never assumed from filenames or prior phase
docs.

## Final Verdict

**NO — do not install and enable the systemd service and walk away for 20–30 NSE sessions yet.**

There is one blocker outside this codebase's control (Finding 1) and one inside it that would
turn "runs once a day" into "runs continuously in a restart loop all day" (Finding 2). Both are
concrete, both are scoped below, and neither requires new intelligence logic — this stays a
commissioning problem, not a redesign.

| # | Finding | Class |
|---|---|---|
| 1 | FYERS access-token refresh is not automatable (regulatory, external) — a human must log in every trading day | **C — architectural blocker (external)** |
| 2 | The systemd unit as authored will restart-loop continuously, not run once per day | **B — small commissioning fix** |
| 3 | The Phase 19.11 EOD completeness gate is never invoked by the autonomous path | **B — small commissioning fix** |
| 4 | Two of the ten operator questions have no automated answer today (EOD completeness, LIVE/REPLAY agreement) | **B — small commissioning fix** |
| 5 | Two old manual scripts can reach the broker/capture path outside the lock if run by hand | **B — operating discipline, not code** |
| 6 | Everything else audited below | **A — safe to commission, once 1–5 are addressed** |

---

## 1. Systemd Ownership

Read directly: `deploy/bujji-daily-intelligence.service` (not installed — confirmed absent from
`/etc/systemd/system/` by directory listing) and the live VPS environment.

| Property | Value | Verified how |
|---|---|---|
| Service user/group | `bujji`/`bujji` | `id bujji` → `uid=997(bujji) gid=987(bujji)`; matches `User=`/`Group=` in the unit |
| Working directory | `/opt/bujji/app` | `ls -ld` → owned by `bujji:bujji`, mode `drwxrwxr-x` — writable by the service user |
| Environment/config | `EnvironmentFile=/opt/bujji/.env` | `ls -l /opt/bujji/.env` → `-rw-------  bujji bujji` — correctly locked to owner-only, readable by the service user |
| Credentials availability | `FYERS_APP_ID`/`FYERS_ACCESS_TOKEN` must be in `.env` | Confirmed the entrypoint fails loudly (not silently) if unset — see `_build_real_broker()` — but **whether a currently-valid token is actually present was not re-verified here**, since that changes daily (see Finding 1) |
| Restart policy | `Restart=always`, `RestartSec=5` | Read directly from the unit file |
| Startup ordering | `After=network-online.target`, `Wants=network-online.target` | Read directly |
| Shutdown behavior | `KillSignal=SIGTERM`, entrypoint installs SIGTERM/SIGINT handlers that cancel the running task cleanly (Phase 19.12, confirmed still present in the deployed file) | Read directly |
| Timeout behavior | `TimeoutStopSec=30` | Read directly — enough budget for one cycle's in-flight `EventStore.append`/heartbeat write to finish |
| Log destination | `StandardOutput=journal`, `StandardError=journal`, `SyslogIdentifier=bujji-daily-intelligence` | Read directly — no file-based log, so no log-rotation code is needed on this project's side |
| Journal retention | No explicit `SystemMaxUse`/`RuntimeMaxUse` in `/etc/systemd/journald.conf` — falls back to systemd's own defaults (typically ~10% of the filesystem or 4GB, whichever is smaller) | Read `journald.conf` directly — only the `[Journal]` header is present, no overrides |
| Reboot survival | `WantedBy=multi-user.target` means it WILL start on boot, once enabled | Read directly. `ProcessLock` is OS-`flock`-based (scoped to the open file description), so a reboot always clears any stale lock — no manual lock-file cleanup would ever be needed after a reboot |

**Finding 2 (Class B): the restart policy causes a continuous restart loop, not a once-daily
run.** `run_daily_intelligence_session.py`'s `_main()` calls `DailySessionRuntime.run()` **once**
and returns — there is no internal loop, no "sleep until tomorrow." `DailySessionRuntime.run()`
itself completes to `SESSION_COMPLETE` or `FAILED` and returns; it never blocks waiting for the
next trading day. Combined with `Type=simple` + `Restart=always` + `RestartSec=5`, systemd will
relaunch the process roughly every 5 seconds — indefinitely, including outside market hours, on
weekends, and overnight — each relaunch re-acquiring the (by-then-released) `ProcessLock` and
attempting a fresh capture+intelligence cycle for the same date. During market hours this means
repeated, uncoordinated real capture cycles hammering the FYERS connection every few seconds
instead of once; outside market hours the capture subprocess exits fast (see §3) but the loop
still spins continuously, filling the journal.

This is **not** a data-safety issue — `HistoricalObservationStore.write()` and `EventStore.append`
are both genuinely idempotent (§5), so repeated cycles cannot corrupt state — but it defeats the
phase's own goal ("operate continuously ... without duplicate capture/intelligence execution" in
spirit, even though no single duplicate corrupts anything) and would generate excessive FYERS API
load and journal volume over 20–30 sessions.

**Smallest fix** (unit-file only, no Python changes): convert `bujji-daily-intelligence.service` to
`Type=oneshot` with `Restart=on-failure` and a materially longer `RestartSec` (or no restart at
all — a oneshot's job is to run once and stop), and add a companion `bujji-daily-intelligence.timer`
unit with `OnCalendar=Mon..Fri 09:00 Asia/Kolkata` (the VPS's own system timezone is already
`Asia/Kolkata` — confirmed via `timedatectl` — so no timezone-conversion risk) plus
`Persistent=true` so a missed fire (e.g. VPS was down at 09:00) still runs on next boot. This is
the standard systemd pattern for a daily batch job and requires no change to any file audited in
Phase 19.11–19.13.

---

## 2. Single-Runtime Ownership

Traced actual imports and entrypoints across the whole repository (`grep` for real call sites, not
filenames):

| Entrypoint | Reaches capture/intelligence? | Scheduled how? |
|---|---|---|
| `run_daily_intelligence_session.py` | Yes — the intended authoritative daily path | Not yet installed (this phase) |
| `scripts/capture_market_reality_session.py` / `capture_options_reality_session.py` | Yes, directly | Only invoked as subprocesses of the daily entrypoint (or by hand) — no cron/systemd trigger found |
| `scripts/run_shadow_live_observatory.py` | Yes — constructs `ShadowSessionRunner` directly with `market_perception_enabled=True` | **Manual only** — no systemd unit, no crontab entry references it |
| `shadow_sessions/SHADOW-2026-08-*-DAY0/run_session.py` | Yes — same `ShadowSessionRunner` | **Manual only**, and these are dated one-off session directories from earlier campaigns, not live infrastructure |
| `run_live_shadow.py` | Yes — the Live Shadow Operator (Sprint 114), separate system per `docs/SYSTEM_OWNERSHIP.md` | **Manual only** |
| `run_daily_observation.py` | No capture/intelligence-pipeline call — Phase III learning-package orchestration over already-recorded data, confirmed by reading its own docstring and imports | Not scheduled |
| `bujji_options_os_runner.py` | No — the separate Trading Brain/Risk Governor system (per `docs/SYSTEM_OWNERSHIP.md`, confirmed in Phase 19.9.6's own audit) | N/A to this audit |

`crontab -l` (root) and `crontab -u bujji -l` both return "no crontab" — confirmed empty, no
hidden scheduled trigger exists today. `systemctl list-units --all 'bujji*'` shows only
`bujji-orb-vwap-legacy.service`, which is **`disabled`/`inactive`** (verified via
`systemctl is-enabled`/`is-active`) — no live conflict.

**Finding 5 (Class B, operating discipline not code):** `scripts/run_shadow_live_observatory.py`
and the dated `run_session.py` scripts can each independently construct a broker connection and
call `get_spot`/`get_option_chain`/etc. if an operator launches them by hand — they do **not**
acquire `bujji.core.process_lock.ProcessLock` at `data/daily_intelligence.lock`, since that lock
was introduced in Phase 19.12 specifically for the daily runtime and these older scripts predate
it. **No automated trigger reaches them today**, so this is not a live duplicate-runtime risk —
but it is a real gap in "prove that two instances cannot capture the same session simultaneously"
if an operator manually runs one of these while the systemd service is active. Since this phase is
audit-only, the recommendation is operational: do not manually launch these scripts while the
daily service is enabled, and note this in the runbook — not a code change.

**Conclusion: exactly one automatable path can reach capture/intelligence
(`run_daily_intelligence_session.py`, via `DailySessionRuntime`), and it is currently the only one
with any scheduling mechanism at all (once installed).**

---

## 3. Daily Session Boundaries

`bujji/shadow_runtime/daily_session.py`'s `DailySessionStage` transition table (read directly,
unchanged since Phase 19.11):
`PRE_MARKET → MARKET_OPEN → CAPTURING → INTELLIGENCE_RUNNING → SESSION_COMPLETE | FAILED`, with
`FAILED` reachable from every non-terminal stage. All seven stages the task lists map onto this
table (`INTELLIGENCE` = `INTELLIGENCE_RUNNING`, `FINALIZING` is `DailySessionRuntime.run()`'s own
`finally`-block heartbeat write, not a distinct enum member — confirmed by re-reading the file).

| Scenario | What actually happens |
|---|---|
| Before NSE opens | `capture_market_reality_session.py`/`capture_options_reality_session.py` check `within_market_hours()` (weekday + `MARKET_OPEN`–`MARKET_CLOSE` window, IST) at the very top of `run()`, **before opening any broker connection**, and exit 1 immediately if outside that window. The daily entrypoint records this as a `capture_error`, session goes to `FAILED`, heartbeat reflects it honestly. |
| During market hours | Normal path: capture succeeds, `_run_live_intelligence_cycle` composes one cycle, artifacts persist, `SESSION_COMPLETE`. |
| After market close | Same as "before NSE opens" — `within_market_hours()` is a single symmetric window check, fails closed either side. |
| Weekends | `now.weekday() >= 5` inside `within_market_hours()` returns `False` unconditionally — confirmed read directly in both capture scripts. Fails fast, before any broker call, so no wasted FYERS load even under the restart-loop condition in Finding 2 (though the loop itself is still wasteful CPU/journal churn). |
| NSE holidays | **No holiday calendar exists anywhere in the audited path.** A weekday NSE holiday looks identical to a normal trading day to `within_market_hours()` — the capture step will attempt real broker calls during what it believes is market hours. The actual outcome depends on what FYERS itself returns on a holiday (stale/closed-market quotes, or an honest empty/error response) — **not independently verified in this audit** since no live holiday session was available to observe. This is a real, disclosed gap, but not classified as a blocker: `MarketDataAdapter`'s own missing-field handling and the completeness gate (`evaluate_completeness_gate`, Phase 19.13) already fail closed on missing spot/options data, so a holiday's most likely failure mode (stale/absent data) should self-report as a gated or `FAILED` cycle rather than a silently fabricated one — but this has not been proven against a real holiday and should be watched during the first commissioning window. |

---

## 4. Crash/Restart Recovery

Traced against the real, already-tested code (Phase 19.12's own 12-test suite already covers
several of these; re-confirmed here in the context of the full chain, not re-tested):

| Failure | What resumes | What's idempotent | What's deliberately not retried |
|---|---|---|---|
| Process crash (SIGKILL, OOM) | `ProcessLock`'s `flock` is scoped to the open file description — the OS releases it automatically on process death, regardless of cause. Next launch (whether systemd `Restart=always` today, or the timer after Finding 2's fix) reacquires cleanly. | The whole cycle — `HistoricalObservationStore.write()` and `EventStore.append()` are both content-keyed (§5) | Nothing needs "not retrying" here — a crash mid-cycle just means that cycle's artifacts were never written, so a rerun is a normal fresh attempt, not a retry of partial state |
| VPS reboot | `WantedBy=multi-user.target` restarts the unit; lock is OS-cleared by the reboot itself | Same as above | Same as above |
| Network failure | `_build_real_broker()`/`broker.connect()` failures are caught and returned as a structured `IntelligenceRunResult` error (`broker_connect_failed: ...`) — never raised, never crashes the process | The retry itself (next scheduled run) is idempotent | The current process does not retry within itself — by design, matching the disclosed "one cycle per daily invocation" scope from Phase 19.13 |
| FYERS authentication failure | `_build_real_broker()` raises `RuntimeError` if credentials are unset; `broker.connect()` failure is caught the same as network failure. Heartbeat/status surface it honestly via `last_error` | N/A (nothing was written) | Never silently retried with stale/invalid credentials — an operator must actually fix the credential (see Finding 1) |
| Capture failure | Recorded as `capture_error: ...` in `DailySessionReport.errors`; the intelligence stage still runs next (unrelated real failure, per `daily_session.py`'s own sequencing — capture and intelligence are independent steps, not gated on each other's success) | `HistoricalObservationStore.write()` returning `False` for a duplicate is itself the idempotency proof | The daily entrypoint does not re-invoke `scripts/capture_market_reality_session.py` a second time within one process run |
| Intelligence failure | `completeness_gate_failed` or `intelligence_pipeline_failed` reasons recorded via `record_cycle_failure`/`IntelligenceRunResult.errors` — never silently swallowed | Rerunning composition against the same reality snapshot produces the same fingerprint (deterministic, Phase 19.3 onward) | Nothing within the process retries a failed composition — a fresh daily invocation is the retry unit |
| Disk/storage failure | `HistoricalObservationStore._run()` retries on `sqlite3.OperationalError` containing "locked" (transient lock contention) with exponential backoff, but **does not specifically handle disk-full or I/O errors** — those propagate as an unhandled exception up through `_make_real_capture_fn`'s subprocess call (captured as a non-zero exit / stderr in `capture_error`) or, for the intelligence side, would surface as `market_data_fetch_failed`/an `unexpected_failure` inside `run_live_intelligence_cycle`'s own catch-all. Not a silent failure, but not a specifically diagnosed one either — acceptable for a Class A item given `DailySessionRuntime.run()`'s blanket "never raises" contract still holds even here. |

**Net finding: every real failure mode is caught, recorded, and honestly surfaced — nothing here
blocks commissioning.**

---

## 5. Duplicate Protection

| Claim | Verified |
|---|---|
| Two instances cannot capture the same session simultaneously | `ProcessLock.acquire()` (flock, `data/daily_intelligence.lock`) raises `LockAcquisitionError` on a second concurrent attempt — proven live on the VPS during Phase 19.12 (`test_duplicate_instance_is_refused`, re-confirmed by reading the code again here) |
| Two intelligence cycles cannot become competing authoritative daily results | Each daily invocation uses a deterministic `cycle_id = f"daily-{date}-1"` and `session_id = f"daily-{date}"` — a second same-day cycle produces the **same** `artifact_id`/`cycle_artifact_id` (content-fingerprinted), so `EventStore.append`'s own dedup (first `event_id` wins, confirmed by reading `store.py` directly: `"First occurrence wins for a repeated event_id"`) makes a rerun a no-op, not a competing second "authoritative" result |
| Restarting a partially completed day does not corrupt Reality | `HistoricalObservationStore.write()` is natural-key-idempotent: re-ingesting an identical observation returns `False` (no-op); a **genuinely different** fact at the same natural key raises `ConflictingHistoricalObservationError` rather than silently overwriting — read directly in `store.py` |
| `EventStore` remains consistent | Append-only, content-keyed, first-write-wins on replay — confirmed by direct code read |
| `HistoricalObservationStore` remains authoritative | Same idempotent-write guarantee — no code path anywhere in the audited chain writes to it other than the two capture scripts, and both use the same `write()`/`write_many()` idempotent path |

**No gaps found in this section.**

---

## 6. Data Continuity (Day N → Day N+1 → ...)

The **only** mechanism that will make this actually happen automatically is the scheduling trigger
— and per Finding 2, that trigger does not yet exist in a form that runs once per day. Today, with
the unit as authored, "Day N+1" would be reached only because `date.today()` naturally advances
inside the restart loop after midnight IST (the VPS's own system timezone) — which happens to
work, but only as an accidental side effect of a broken restart cadence, not a designed one.

Checked for each of the task's named risks:

- **Missing trading days**: if the VPS is down or the service fails to start across an entire
  session, that day is simply never captured — there is no backfill/catch-up mechanism in the
  audited code. `Persistent=true` on the recommended `.timer` fix (Finding 2) would cover a missed
  *fire* (VPS down exactly at trigger time) but not a missed *day* (VPS down all day). Acceptable
  for a Class A/B system as long as this gap is disclosed, since Phase 18's own historical capture
  scripts already exist for manual backfill.
- **Duplicate dates**: prevented structurally, per §5.
- **Incomplete sessions**: recorded honestly as `FAILED` with a real reason in both the heartbeat
  and (once wired, see Finding 3) the EOD completeness report.
- **Partial capture**: `HistoricalObservationStore`'s own natural-key model means a partial day's
  rows are exactly the rows that were actually captured — nothing is fabricated to fill a gap.
- **Stale files**: the heartbeat file is atomically replaced each write (`os.replace`, Phase 19.11)
  — never partially written or stale-but-readable.
- **Failed authentication**: see Finding 1 and §4's row above — surfaces honestly, never silently
  skipped.
- **Disk exhaustion**: not specifically diagnosed (see §4's last row) — acceptable but worth a
  human glance at `df -h` occasionally during the commissioning window; not a blocker.

---

## 7. Intelligence Continuity

Traced the actual code path for "every successfully captured session produces intelligence
snapshot → decision intelligence → phenomena → state transition → daily intelligence artifact":

`run_live_intelligence_cycle()` (Phase 19.13) calls `build_intelligence_heartbeat_cycle()`
unconditionally once the completeness gate passes — and that single call, read directly in
`intelligence_pipeline_adapter.py`, produces `MarketIntelligenceSnapshot`, `DecisionContext`,
`DecisionIntelligenceSnapshot`, `MarketPhenomenaAssessment`, `MarketStateNode`, and
`MarketEnvironmentAssessment` all in one composition — there is no code path where one of these is
produced without the others (they are positional return values of one function, not independently
fallible steps). `build_daily_intelligence_artifact()` then stores all of them via each object's
own `to_dict()`.

Failure visibility, checked against each of the four channels the task names:

- **Heartbeat**: `DailySessionHeartbeat.last_error` — confirmed populated on `capture_error`/
  `intelligence_error` by `daily_session.py`'s `run()`.
- **Status**: `bujji_daily_status.py` surfaces `last_failure` from that same heartbeat field —
  confirmed by re-reading `status.py`.
- **Logs**: `journalctl -u bujji-daily-intelligence` (once installed) captures stdout/stderr —
  `print(report.to_dict())` in the entrypoint's `_main()` includes the full error list.
- **Persistent artifact/event state**: `record_cycle_failure()` (Phase 19.10.2, reused in
  `live_intelligence_cycle.py`) writes an explicit `SHADOW_INTELLIGENCE_CYCLE_FAILED` event — a
  real, queryable fact, never a silent gap in the event log.

**Finding 3 (Class B): the Phase 19.11 EOD completeness gate is built but never called.**
`bujji/shadow_runtime/completeness.py`'s `validate_end_of_day_completeness()` exists, is tested,
and answers exactly "was today's reality complete" — but grepping `daily_session.py` for any
reference to it returns nothing. `DailySessionRuntime.run()` never invokes it. This means the
question "was today's reality complete" (task §10) currently has **no automated answer** anywhere
in the autonomous path — an operator would have to run `validate_end_of_day_completeness()` by
hand. Smallest fix: call it once during `run()`'s finalization (after `INTELLIGENCE_RUNNING`,
before writing the final heartbeat) and add its `is_complete`/`completeness` fields to
`DailySessionHeartbeat` and `bujji_daily_status.py`'s output — additive, no existing field
removed, matching every prior phase's own "additive, never destructive" convention.

---

## 8. LIVE/REPLAY Audit

`bujji/shadow_runtime/replay_equivalence.py` (Phase 19.13) proves fingerprint, environment
classification, and decision-posture equivalence between `LIVE` and `HISTORICAL_REPLAY` execution
modes — confirmed by re-reading the module and its own passing test
(`test_live_replay_equivalence_same_fingerprint_classification_posture`). The one field
**deliberately** excluded from the fingerprint comparison is `execution_mode` itself — confirmed
directly in `MarketIntelligenceSnapshot.fingerprint_payload()`'s own docstring: *"describes HOW
this snapshot was produced ... not WHAT Bujji understood about the market."* This is the one
intentional, disclosed difference the task asks to document — there is no other intentional
divergence anywhere in the composition path.

**Gap**: `validate_live_replay_equivalence()` exists as a callable but, like the EOD completeness
gate, **is never invoked automatically** by the daily runtime — it was built and tested as a
reusable proof function in Phase 19.13, not wired into a scheduled check. This is the same shape
of gap as Finding 3, and is folded into **Finding 4** below rather than counted separately, since
the fix is the same kind of change (call an existing, tested function from `daily_session.py`
and surface its result).

---

## 9. Safety Boundary

Re-verified structurally, not by re-reading the phase docs' own claims:

- **AST-level import check** (`tests/test_phase_19_13_live_intelligence_bridge.py`, re-run during
  this audit): zero `order`/`position`/`strategy`/`execution` imports across all four Phase 19.13
  modules — confirmed passing.
- **`run_daily_intelligence_session.py`'s own real broker construction**: `_build_real_broker()`
  wraps `FyersBroker(cfg, log)` in `disable_live_execution()` (`bujji.broker.guard`) **before** the
  broker object is returned or touched anywhere else in the file — confirmed by direct read.
  `disable_live_execution` is the same, unmodified guard `run_live_shadow.py` already uses in
  production.
- **Precise call-level check**: an AST walk over every `ast.Attribute` node in
  `run_daily_intelligence_session.py` (re-run during this audit,
  `test_run_daily_intelligence_session_never_places_orders`) confirms no
  `.place_order(`/`.modify_order(`/`.cancel_order(` call exists anywhere in the file's source.
- **Read-only market access**: `MarketDataAdapter` (`bujji.market_perception`) only calls
  `get_spot`/`get_vix`/`get_option_chain`/`get_futures_quote`/`get_recent_candles` — confirmed by
  re-reading the class; none of these can mutate broker state.

**No gaps found in this section — the safety boundary holds structurally, not just by
convention.**

---

## 10. Observability

Answering the task's own seven operator questions against what actually exists today:

| Question | Answerable without SSH-ing through source? | How |
|---|---|---|
| "Did Bujji successfully observe today's market?" | **Yes** | `bujji_daily_status.py` → `runtime_status`/`last_successful_observation` |
| "Is Bujji currently alive?" | **Yes** | `bujji_daily_status.py` → `lock_held` (live `flock` probe) + `last_heartbeat_at` |
| "Did capture complete?" | **Yes** | Heartbeat's `rows_captured_today` + absence of a `capture_error` in `last_error` |
| "Did intelligence complete?" | **Yes** | Heartbeat's `last_intelligence_cycle_timestamp` + absence of an `intelligence_error` |
| "Was today's reality complete?" | **No — Finding 3** | `validate_end_of_day_completeness()` exists but is not wired into the heartbeat/status path |
| "Did LIVE and REPLAY agree?" | **No — Finding 4 (folded from §8)** | `validate_live_replay_equivalence()` exists but is not invoked automatically or surfaced anywhere |
| "Did anything fail?" | **Yes** | `last_error` on the heartbeat, surfaced by `bujji_daily_status.py`, plus `journalctl -u bujji-daily-intelligence` |

**Finding 4 (Class B): 2 of 7 operator questions have no automated answer today.** Both missing
answers come from real, already-built, already-tested functions (`validate_end_of_day_completeness`
from Phase 19.11, `validate_live_replay_equivalence` from Phase 19.13) that were never wired into
the daily runtime's own finalization step. The fix is additive: call both once per day inside
`DailySessionRuntime.run()`'s finalization, add their results as new (never removing existing)
fields on `DailySessionHeartbeat`, and print them in `bujji_daily_status.py`'s `render()`.

---

## Summary — What Must Happen Before Commissioning

1. **Finding 1 (external, Class C)** — establish an operational routine for the daily FYERS
   interactive login (browser + TOTP/PIN) to refresh `FYERS_ACCESS_TOKEN` before each trading
   session. This is not fixable in this codebase; automatic refresh is disabled by FYERS itself
   per SEBI regulation (`docs/FYERS_TOKEN_LIFECYCLE.md`, live-verified 2026-07-19). Until a human
   (or an external, out-of-band automation the operator builds and trusts) performs this daily,
   the runtime cannot observe any session, regardless of how correct everything else is.
2. **Finding 2 (Class B)** — convert `bujji-daily-intelligence.service` to `Type=oneshot` +
   bounded/no auto-restart, and add a `bujji-daily-intelligence.timer` (`OnCalendar=Mon..Fri 09:00
   Asia/Kolkata`, `Persistent=true`). Unit-file-only change.
3. **Finding 3 (Class B)** — wire `validate_end_of_day_completeness()` into
   `DailySessionRuntime.run()`'s finalization; surface on the heartbeat.
4. **Finding 4 (Class B)** — wire `validate_live_replay_equivalence()` into the same finalization
   step; surface on the heartbeat and `bujji_daily_status.py`.
5. **Finding 5 (Class B, operational)** — document (runbook, not code) that
   `scripts/run_shadow_live_observatory.py` and any `shadow_sessions/*/run_session.py` must not be
   run manually while the daily systemd service is active, since they do not share
   `ProcessLock`.

Everything else audited in sections 1–9 — systemd shutdown/timeout behavior, single-runtime
ownership modulo Finding 5, session-boundary behavior on weekends/off-hours, every crash/restart
scenario, duplicate-protection guarantees, data-continuity idempotency, intelligence-continuity
completeness, the LIVE/REPLAY fingerprint proof itself, and the order-placement safety boundary —
is **Class A: already correct, verified directly, safe to commission as-is.**

Once 2–5 are implemented (all four are small, additive, unit-file-or-single-function changes, no
architectural rework) and 1 has an operational routine in place, this system is ready for the
20–30 session unattended commissioning window the task asks about.
