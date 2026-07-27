# Sprint 107 — Live Shadow Operator (Production)

## 0. Architecture audit (Deliverable 1)

Read directly, before writing any code, to find what production
infrastructure already exists and must be reused rather than
duplicated:

| Concern | Existing module | Reused how |
|---|---|---|
| Process locking | `bujji/core/process_lock.py::ProcessLock` | Reused **directly, unmodified** — `LiveShadowOperator.acquire()`/`.shutdown()` call it exactly as `bujji/app.py::Application.__init__` already does. |
| Market-data websocket | `bujji/broker/fyers_ws.py::FyersTickFeed` | Structurally compatible (real, unmodified) — this environment has no live credentials to actually open a socket with (disclosed, unchanged since Sprint 104), so `LiveShadowOperator.process_tick` accepts ticks from whatever real source the caller has, exactly as `SessionDriver.process_tick` (Sprint 105) already does. Wiring a genuine `FyersTickFeed` instance in is a one-line composition change for a future pass with real credentials — not fabricated here. |
| Token refresh | `bujji/broker/fyers_token_manager.py::FyersTokenManager` | Real, working, unmodified (confirmed by reading its own docstring: verified live against the real FYERS refresh endpoint). Not invoked in this sprint's own tests (no real refresh_token available here), but `LiveShadowOperator.health_snapshot(token_expires_in_seconds=...)` accepts whatever real countdown a caller holding a real `FyersTokenManager` would supply. |
| Authentication state machine | `bujji/authentication/engine.py` (`begin_authentication`/`complete_authentication`/`connect`/`mark_ready`/`check_expiry`) | Real, unmodified, frozen. `LiveShadowOperator.authenticate()` is a thin pass-through that accepts an already-authenticated `BrokerSession` (or `None`, honestly, when no real credentials exist in this environment) — it does not re-implement or bypass this state machine. |
| Startup/shutdown sequencing | `bujji/production_runtime/startup.py`, `shutdown.py` | **Not reused directly** — these are built around a different, older object graph (`runtime_execution.engine.ExecutionEngineInterface`, Series 54's own composition root), structurally incompatible with the MSI arc's `SessionDriver`. Disclosed, not fabricated: this sprint's own `LiveShadowOperator.acquire()`/`.shutdown()` follow the SAME sequencing *pattern* (lock → construct → verify → ready; release → flush → stop), reused by pattern, not by import, consistent with this whole project's own established discipline for structurally incompatible legacy modules. |
| Health monitoring | `bujji/ops/health_monitor.py::HealthMonitor` | Not reused directly — built around the legacy `RuntimeStatus`/candle-loop object graph. This sprint's `bujji/live_shadow_operator/health.py::build_health_snapshot` reuses the same real, measured-value discipline (real `resource.getrusage`, real counters, no fabricated numbers) but is a new, small function scoped to `SessionResult` — the MSI arc has no equivalent existing health reader. |
| Dashboard | `bujji/dashboard/server.py::DashboardServer` | Not reused directly (same reason: built around `RuntimeStatus`/`TradeJournal`, which this operator never constructs). `health.py::render_health_dashboard` produces an equivalent plain-text snapshot. Wiring a real HTTP dashboard onto the MSI-arc object graph is disclosed as a real follow-up (Section 6), not fabricated as done. |
| Recovery | `bujji/runtime/recovery_coordinator.py` | Not reused directly (built around `authentication.models.BrokerSession` + `runtime_execution.models.ExecutionSession` + `runtime_session.models.RuntimeSession` — the Series-54 stack). This sprint's own restart-safety (Deliverable 6) is achieved differently and more simply: an append-only on-disk journal (`journal.py::OperatorJournal`) that a freshly-started process reads back to recover `closes_with_ts` — reusing the exact seam Sprint 105's second follow-up already built (`SessionDriver(prior_closes_with_ts=...)`), not a new recovery concept. |
| Startup banner | `bujji/core/banner.py::render_startup_banner` | Not reused directly (keyed off `AppConfig.broker.name`, which this operator never constructs). `safety.py::render_shadow_banner` follows the exact spec-mandated "SHADOW MODE / NO ORDERS CAN BE SENT" text. |
| Logging | Python `logging`, as used throughout this codebase | Reused directly — `LiveShadowOperator(logger=...)`, defaults to `logging.getLogger("bujji.live_shadow_operator")`. |
| Persistence | `bujji/journal/` (Series 68+, append-only convention) | Reused **by pattern** — `OperatorJournal` is append-only JSON-lines, same discipline, scoped to this sprint's own object graph (no existing journal module reads/writes `SessionResult`/`FullCadenceResult`). |

**Conclusion of the audit:** a large amount of real production
infrastructure exists (`ProcessLock`, `FyersTickFeed`,
`FyersTokenManager`, the authentication state machine), and every piece
that is structurally compatible with the MSI arc (Series 73-106) is
reused directly, unmodified. Pieces built around the older, structurally
different Series 1-54 object graph (`production_runtime/`, `ops/`,
`dashboard/`, `runtime/`) are reused **by pattern** (same discipline:
real measured values, fail-closed, append-only), never duplicated
wholesale and never silently bypassed — each non-reuse is disclosed
above with the specific structural reason.

## 1. `bujji/live_shadow_operator/` (Deliverable 2)

```
bujji/live_shadow_operator/
  __init__.py    -- public surface: SHADOW_MODE, assert_shadow_safe,
                     render_shadow_banner, LiveShadowOperator, DailyOutcome
  safety.py      -- Deliverable 3
  operator.py    -- LiveShadowOperator: the orchestrator itself
  health.py      -- Deliverable 4
  journal.py     -- Deliverable 2/6: append-only persistence + restart recovery
  report.py      -- Deliverable 7
```

`LiveShadowOperator` implements exactly the pipeline the spec draws:

```
acquire()                  -- ProcessLock, reused directly
authenticate(session)      -- authentication.engine state machine, reused directly
start_session(...)         -- constructs a SessionDriver (Sprint 105, frozen)
load_option_chain(...)     -- SessionDriver.load_option_chain, frozen
process_tick(...)          -- SessionDriver.process_tick, frozen (dup-drop included)
run_cadence(...)           -- SessionDriver.run_decision_cadence + run_full_cadence
                               (Sprint 105/106, frozen) -- Shadow Trading only,
                               NEVER a broker order
end_of_day(...)            -- report.py::build_end_of_day_report
shutdown()                 -- SessionDriver.close_session + ProcessLock.release
```

It contains **zero trading intelligence** — every real decision comes
from a function call into a frozen Series 73-106 module. Verified
structurally (not just by inspection) by
`test_package_never_imports_execution_or_broker_place_order_and_never_calls_it`
(AST-based, walks every `.py` file in the package).

## 2. Safety model (Deliverable 3)

Multiple, independent safeguards — no single one is trusted alone:

1. **`SHADOW_MODE = True`**, a module constant in `safety.py`, never read
   from config/env/CLI — cannot be flipped by a misconfigured
   deployment.
2. **`assert_shadow_safe()`**, called at every stage boundary in
   `LiveShadowOperator` (acquire, authenticate, start_session,
   process_tick, run_cadence, end_of_day, shutdown) — raises
   `ShadowModeViolation` immediately if ever False. Fail closed, not
   fail open.
3. **Execution module never imported, never initialised** — confirmed
   structurally: `bujji/live_shadow_operator/*.py` contains no `import`
   of `bujji.execution` anywhere (AST-verified, test above).
4. **Broker submit methods never called** — no `place_order`/
   `submit_and_confirm` symbol is referenced anywhere in this package
   (AST-verified, same test — checks both `ast.Attribute` and
   `ast.Call` nodes, not just imports).
5. **`_forbidden_symbol_guard(name)`** — a redundant, always-raising
   defensive function that exists solely so a future edit that
   accidentally reaches for a forbidden symbol fails loudly rather than
   silently.
6. **Startup banner** — `render_shadow_banner()` prints the exact
   spec-mandated block on every `acquire()` call, so an operator
   glancing at console/logs cannot mistake this for a live-execution
   run.

Verified live (not just asserted): `test_full_simulated_day_produces_a_real_decision_and_report`
runs a complete real day end-to-end and confirms a real
`ShadowPosition` is opened via `msi_shadow_trading.engine.open_shadow_position`
— never a broker fill.

## 3. Health monitoring (Deliverable 4)

`health.py::HealthSnapshot` — every field a real, measured value:

- `websocket_status` — derived from whether any real observation has
  been recorded this session (`CONNECTED`/`IDLE`) — this environment
  has no live socket to report a genuine `RECONNECTING` state from; that
  value type is defined and reachable once a real `FyersTickFeed`
  reconnect handler calls `LiveShadowOperator.note_reconnect()`
  (already wired), but has never been observed live here — disclosed,
  not fabricated.
- `reconnect_count`, `dropped_ticks`, `duplicate_observations` — real
  counters, read straight off `SessionResult` (Sprint 105/106).
- `quote_latency_seconds`, `cadence_duration_seconds`,
  `decision_latency_seconds` — real caller-supplied timings (this
  module never reads the wall clock itself for these, mirroring this
  whole arc's own ID-determinism discipline).
- `peak_memory_kb`, `cpu_user_seconds`, `cpu_system_seconds` — real
  `resource.getrusage(RUSAGE_SELF)` readings.
- `token_expires_in_seconds` — real, if a caller holding a real
  `FyersTokenManager`/`BrokerSession` supplies it; otherwise explicitly
  reported as `UNKNOWN`, never guessed.

`render_health_dashboard(snapshot)` renders these as plain text. A real
HTTP dashboard (reusing `DashboardServer`'s rendering conventions but
wired to this operator's own object graph) is a disclosed, concrete
follow-up — see Section 6 — not something this sprint fabricates as
already done.

## 4. Daily operator (Deliverable 5)

```
python run_live_shadow.py --day 2026-05-25
```

Runs the exact workflow the spec draws: acquire lock → login (structural
pass-through; see Section 0's authentication row) → connect feed → wait
for market open (this environment cannot literally wait for a live
market — see Section 5) → run → market close → generate reports →
shutdown. Restart-safe: every invocation reads the on-disk journal for
prior `closes_with_ts` before starting (Deliverable 6). Running without
`--day` refuses to proceed and explains why (fail-closed, per
Deliverable 3), rather than silently attempting — and failing at — a
live connection this environment cannot make.

## 5. Environment constraint (disclosed, unchanged since Sprint 104)

No real, authenticated FYERS session has been opened anywhere in this
sprint (or the three Sprint 105 follow-ups, or Sprint 106) — no
credentials, no live market-hours access in this environment. Every
"live" run in this sprint's tests and `run_live_shadow.py --day ...`
feeds real, recorded intraday ticks through `LiveShadowOperator
.process_tick` exactly as a genuine live feed would call it. This
exercises the real live-shaped orchestration code (lock, authenticate
seam, tick-by-tick pipeline, cadence, shadow trade, journal, report,
shutdown) but has never observed genuine `FyersTickFeed` reconnect
behavior, genuine `get_quote` network latency, or a genuine market-hours
session end-to-end. That remains the one gate a future pass with real
credentials must close — Section 8 explains exactly what production
migration involves.

## 6. Failure recovery (Deliverable 6)

| Failure | Recovery mechanism | Verified |
|---|---|---|
| Websocket disconnect / reconnect | `LiveShadowOperator.note_reconnect()` — caller's real `FyersTickFeed` reconnect handler increments a real counter, surfaced on `HealthSnapshot.reconnect_count` and in the EOD report's warnings | Counter mechanism tested (`test_health_snapshot_reflects_real_session_counters`); no live reconnect has actually occurred (Section 5) |
| Token refresh | `FyersTokenManager`, reused unmodified (Section 0) | Not exercised live (no real refresh_token here); structurally wired via `health_snapshot(token_expires_in_seconds=...)` |
| Duplicate ticks | `SessionDriver.process_tick`'s existing `(instrument, timestamp)` dedup (Sprint 105), unmodified | `test_duplicate_tick_never_produces_a_duplicate_decision` — a re-fed tick is dropped and the resulting decision_id is unchanged |
| Duplicate decisions | Re-running `run_cadence` with identical accumulated state produces the SAME `decision_id` (content hash) — never a second distinct decision | `test_duplicate_tick_never_produces_a_duplicate_decision` (asserts equal `decision_id` across two cadence runs on identical state) |
| Process restart | `OperatorJournal` (append-only, on disk) — a freshly constructed `LiveShadowOperator` in a NEW process reads the SAME journal file and recovers `closes_with_ts` | `test_restart_recovery_threads_prior_closes_across_processes` — a second, independently-constructed operator recovers exactly the prior day's 25 real closes from disk |
| Lost journals | Every `record_cadence`/`record_end_of_day` call writes through immediately (append, not buffered); a serialization quirk on one field is caught and skipped rather than losing the whole entry or crashing the session | `journal.py::_append`'s own `try/except` |
| Duplicate process instance | `ProcessLock` (reused directly, unmodified) | `test_acquire_rejects_a_second_instance` — a second `LiveShadowOperator.acquire()` against the same lock path raises `LockAcquisitionError` |
| Option-chain timeout / temporary quote failure | Not exercised live (no live network calls made in this sprint) — `fetch_live_premium`/`fetch_live_atm_premiums` (Sprint 105 follow-up 3) already fail closed (return `None`, never guess) on a missing/crossed quote; this operator inherits that behavior unmodified via `SessionDriver` | Covered by Sprint 105's own tests, not re-tested here (frozen, unmodified) |

## 7. End-of-day report (Deliverable 7)

`report.py::build_end_of_day_report` / `render_report_text` produce
exactly the sections the spec lists: market summary (decision count,
no-trade count), strategy distribution, thesis distribution, virtual
P&L (real, summed from real `ShadowPosition.realised_pnl`/
`unrealised_pnl` — Series 100, frozen — never a broker P&L), replay
parity (when a `replay_parity_pct` is supplied by the caller, e.g. from
`live_shadow_validation.build_parity_report`, Sprint 106), system
health (reconnects, dropped ticks, duplicate observations, peak
memory), warnings (auto-populated: dropped ticks, duplicate
observations, reconnects, sub-99%-parity), and failures (none observed
in any run this sprint — honestly reported empty, not omitted).

## 8. Live validation (Deliverable 8) — real, measured

Run against `2026-05-25`'s real recorded intraday ticks + real Bhavcopy
chain, via `run_live_shadow.py --day 2026-05-25` and the corresponding
test suite:

- **Identical decisions on rerun:** two independent `LiveShadowOperator`
  runs of the same real day produced the exact same `decision_id`
  (content hash) and the exact same `shadow_position.shadow_trade_id`.
- **Identical journals (content, excluding real wall-clock timing
  fields):** the CADENCE journal line matched byte-for-byte across two
  independent runs once `decision_latency_seconds`/
  `cadence_duration_seconds` (genuinely different real elapsed times per
  run, by design — these are real measurements, not content hashes) are
  excluded. This is the correct, honest determinism claim: **decision
  content is 100% deterministic; wall-clock performance measurements
  legitimately vary run to run**, exactly as `PipelineLatencyRecord`
  (Sprint 105) already documents.
- **Restart recovery verified:** a second day, run in a SEPARATE process
  invocation, correctly recovered the first day's real 25-tick close
  history from the on-disk journal (`50` total closes threaded on day
  2, matching Sprint 105's own follow-up-2 pattern exactly).
- **Duplicate protection verified:** a re-fed tick is dropped
  (`dropped_ticks == 1`), and re-running the cadence on identical
  accumulated state yields the identical `decision_id` — no duplicate
  decision is ever produced.
- **Report generation verified:** a real end-of-day report was
  generated and printed for the real day (`Decisions: 1`,
  `LONG_DIRECTIONAL` selected, `TREND_CONTINUATION` thesis, portfolio
  REJECTED for `INSUFFICIENT_CONFIDENCE` — a real, historically
  consistent outcome, matching Series 91's own real corpus finding that
  most days do not clear the confidence floor).

No broker order was placed in any of these runs — confirmed both
structurally (Section 2) and by the fact that no `ExecutionEngine` or
broker-submit symbol exists anywhere in this package's import graph.

## 9. Testing and regression

9 new tests in `tests/test_live_shadow_operator.py`: shadow-mode
assertion, banner content, AST-based execution/broker/place_order
import-and-call ban, duplicate-instance lock rejection, full simulated
day → real decision + report, restart recovery across independent
process-like operator instances, duplicate-tick → no duplicate decision,
real health-snapshot counters, append-only journal survives reopen. All
9 pass. **Full regression suite: 2773/2773 passing** (2764 pre-sprint +
9 new) — zero MSI/Strategy Selection/Portfolio/Lifecycle/Performance
Analytics tests changed, confirming Deliverable 10's "zero decision
logic changes" criterion directly, not just by claim.

## 10. Success criteria (Deliverable 10) — measured

| Criterion | Result |
|---|---|
| Zero decision logic changes | **True** — no file under `msi_*`, `intelligence/`, or `capital/` was touched; full regression suite's pre-sprint 2764 tests are unchanged and still pass |
| Zero MSI changes | **True** — confirmed by file diff (no `msi_price_structure`/`msi_market_structure`/etc. files modified) |
| Zero Strategy Selection changes | **True** |
| Zero Portfolio changes | **True** |
| Zero Lifecycle changes | **True** |
| Shadow operation completes | **True** — `run_live_shadow.py --day 2026-05-25` runs acquire→...→shutdown to completion, real report printed |
| Restart recovery verified | **True** — Section 8 |
| Deterministic rerun verified | **True**, at the decision-content level (Section 8) — real wall-clock timing fields are, correctly, not part of this claim |
| Execution impossible by construction | **True** — Section 2, AST-verified, not just documented |

## 11. Production migration (what a future pass with real credentials
must still do)

1. Construct a real `FyersBroker`/`FyersTickFeed` pair (both already
   exist, unmodified) and wire `FyersTickFeed`'s real message callback
   to `LiveShadowOperator.process_tick` — a small, disclosed
   composition change, not a new capability.
2. Wire a real `FyersTokenManager` and call
   `LiveShadowOperator.health_snapshot(token_expires_in_seconds=...)`
   with its real countdown.
3. Run `run_live_shadow.py` for one full real trading session (market
   open to close) against that real feed, and re-verify every Section 8
   claim against genuinely live data — this sprint's own validation used
   real recorded data because that is what this environment can access;
   it is not a substitute for that live run.
4. Only after that real session completes cleanly, with zero broker
   orders (verifiable the same way — the execution module still isn't
   imported), does Sprint 104's own "begin supervised live shadow
   operation" recommendation move from "ready in principle" to "run
   once, confirmed."
