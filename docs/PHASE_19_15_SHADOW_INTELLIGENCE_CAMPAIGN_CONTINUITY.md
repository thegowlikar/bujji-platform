# Phase 19.15 — Shadow Intelligence Campaign Operational Continuity Hardening

Closes the three remaining operational gaps from the prior forensic audit: stale-heartbeat
detection, trading-calendar wiring, and missed/silent-gap continuity detection. No systemd action
was taken — nothing installed, enabled, started, or stopped. FYERS authentication remains the one
accepted human action.

## Implementation Boundary (audited before coding)

Read in full before any change: `bujji/market_calendar.py`, `bujji/shadow_runtime/daily_session.py`,
`bujji/shadow_runtime/status.py`, `DailySessionHeartbeat`, `hydrate_daily_intelligence_artifacts()`,
`DailyIntelligenceArtifact`, `deploy/bujji-daily-intelligence.{service,timer}`, and the full
Phase 19.13/19.14.x test suites. Traced actual production callers: `run_daily_intelligence_session.py`
is the only writer of `DailySessionHeartbeat`; `run_live_intelligence_cycle` is the only writer of
`DailyIntelligenceArtifact`/`ShadowIntelligenceCycleArtifact`; `HistoricalObservationStore` is the
only writer of raw captured observations. Nothing else touches any of these.

**Decision, stated up front**: no new scheduler, capture engine, intelligence engine, database,
event store, shadow package, broker layer, or execution layer was created. Every addition is either
a thin, read-only wrapper over an existing component, or a small, additive field on an
already-existing dataclass.

## 1. Stale Heartbeat Detection

**File changed**: `bujji/shadow_runtime/status.py`.

`OperationalStatus` gains two additive fields (`stale: bool`, `heartbeat_file_age_seconds: Optional[float]`),
both defaulted so every existing caller keeps compiling unchanged. `get_operational_status()` gains
two additive parameters: `now` (injectable clock, defaults to real wall-clock only when omitted —
matching this project's established clock-injection convention) and `stale_threshold_seconds`
(defaults to 24 hours, explicitly configurable per call).

**Mechanism**: `os.path.getmtime()` on the heartbeat file itself — not a new persistence
mechanism, the filesystem already tracks this. Deliberately **not** calendar-aware: it answers
"has anything written to this file recently," nothing more. A file that has never existed is
reported `stale=False` (there is nothing to be stale — this is honestly distinct from "ran and
went quiet"), never conflated with a genuine staleness finding.

## 2. Trading-Calendar Wiring

**File changed**: `run_daily_intelligence_session.py` (continuing Phase 19.16's own work, this
session renamed the heartbeat value from `NOT_A_TRADING_DAY` to `NON_TRADING_DAY` to match this
phase's exact required vocabulary — `MarketCalendar` itself remains untouched).

`MarketCalendar().is_trading_day()` (Sprint 112 Deliverable 6, unmodified) is checked before the
lock is acquired and before any broker construction, for real (non-`--dry-run`) invocations only.
On a non-trading day, `_main()` writes one honest heartbeat with `runtime_status="NON_TRADING_DAY"`
and returns 0 — success, because correctly doing nothing on a non-trading day IS success.
`MarketCalendar.verification_warning()` is printed to stderr on every real invocation, since
`HOLIDAY_CALENDAR` ships as an empty, honestly-disclosed template.

**"Fail safely if the holiday calendar is not verified"**: `MarketCalendar.is_trading_day()`'s own
existing design already fails in the safe direction — an unverified, unlisted date defaults to
"trading day," never to "holiday." The failure mode of an unverified calendar is therefore "may
attempt to observe on an actual holiday and honestly report an empty/incomplete session," never
"silently skip a real trading day." This is the correct direction for a market-observation
campaign, and this phase did not change it.

**External, not fixed here**: `HOLIDAY_CALENDAR` remains empty. Per this phase's own explicit
instruction, no NSE holiday dates were populated from memory or guesswork. **The dates that need
authoritative verification are: the full published NSE 2026 trading holiday calendar** (and 2027,
before the campaign would run into it) — this is a data-entry task against FYERS/NSE's own
published calendar, entirely separate from any code change, and `verification_warning()` will keep
surfacing it every real run until done.

## 3 & 4. Missed-Session / Continuity Detection + Silent-Gap Distinction

**New file**: `bujji/shadow_runtime/campaign_continuity.py`. Read-only. Reuses, verbatim:
`MarketCalendar.is_trading_day()`, `validate_end_of_day_completeness()` (Phase 19.11/19.14.4),
`hydrate_cycle_artifacts()` + `EVENT_SHADOW_INTELLIGENCE_CYCLE_FAILED` (Phase 19.10.2),
`hydrate_daily_intelligence_artifacts()` (Phase 19.13). No new database, no new event store, no
new capture/intelligence logic.

**`classify_session(date, ...)`** — the required five-way distinction, never collapsed:

| Status | Meaning | Evidence used |
|---|---|---|
| `NON_TRADING_DAY` | Calendar says this date was never expected to run | `MarketCalendar.is_trading_day()` |
| `SESSION_COMPLETE` | A real `DailyIntelligenceArtifact` exists, gate passed, EOD complete | `hydrate_daily_intelligence_artifacts()` + `validate_end_of_day_completeness()` |
| `INCOMPLETE` | A `DailyIntelligenceArtifact` exists but the gate or EOD check did not fully pass | same, `completeness_gate_passed`/`is_complete` False |
| `SESSION_FAILED` | A real, recorded failure exists (a `SHADOW_INTELLIGENCE_CYCLE_FAILED` event), OR capture data is present with no successful/failed cycle record at all (the FYERS-pre-flight-failure shape, where the intelligence side never even reached a recorded cycle) | `EventStore.read_events()` + `HistoricalObservationStore` presence |
| `MISSING` | An expected trading day with absolutely no record anywhere | absence of all of the above — **never** silently reported as success |

**`build_campaign_continuity_report(start_date, end_date, ...)`** — classifies every day in the
range, then computes `expected_sessions`, `completed`, `incomplete`, `failed`, `missing`,
`non_trading_days`, `current_streak` (walking backwards from the most recent date, counting
consecutive `SESSION_COMPLETE` trading days, skipping non-trading days since they don't break
continuity, stopping at anything else), and `gaps` (the exact dates classified `MISSING`).

## 5. Operator-Friendly Campaign Status

**New file**: `bujji_campaign_status.py`. Combines `get_operational_status()` (today's live/latest
snapshot — the heartbeat is real-time but overwritten daily, so it's the right source for "right
now") with `build_campaign_continuity_report()` (the campaign-to-date aggregate). Renders the
requested summary box: today's session status, FYERS/capture/intelligence/EOD-completeness/
LIVE-REPLAY/artifact fields for today, and the campaign-wide expected/completed/incomplete/failed/
missing/non-trading/streak/overall counts. `--json` for machine consumption. No email/SMS/Telegram
— CLI output only, per instruction.

When no heartbeat file exists yet at all, the per-field sub-status shows `NO_DATA_YET`, not
`FAILED` — a real correctness fix made during this phase's own real-data verification (see below),
since presenting "never run" as "ran and failed" would be a fabrication.

## 6. Campaign Safety Invariants — verified unchanged

Re-confirmed by re-running the existing safety suites after every change in this phase (not merely
asserted): `tests/test_phase_19_13_live_intelligence_bridge.py` +
`tests/test_phase_19_14_1_commissioning_hardening.py` — 40/40 clean. `disable_live_execution`
wrapping, `ProcessLock` duplicate prevention, idempotent `HistoricalObservationStore`/`EventStore`
writes, and the LIVE/HISTORICAL_REPLAY equivalence mechanism were not touched by this phase at all
— every new module here is read-only over what those already produce.

## 7. Failure Classification — never hidden

Directly satisfied by `classify_session`'s own decision tree (§3/4 above): a FYERS authentication
failure that never reaches a recorded cycle classifies as `SESSION_FAILED` (via the
capture-without-cycle-record branch); a recorded intelligence pipeline failure classifies as
`SESSION_FAILED` with the real recorded reason; a day the timer never ran classifies as `MISSING`;
a verified holiday/weekend classifies as `NON_TRADING_DAY`. None of these paths can produce
`SESSION_COMPLETE` — that status requires an actual persisted, gate-passed `DailyIntelligenceArtifact`.

## Real Production-Data Verification

Run against the real, `bujji`-owned VPS state (`data/historical_reality/normalized/historical_observations.db`,
the real, currently-empty `data/daily_intelligence_artifacts.jsonl`/`shadow_intelligence_cycle_artifacts.jsonl`
— since the daily runtime has never actually been commissioned/run in production yet):

```
$ bujji_campaign_status.py --campaign-start-date 2026-08-10 --as-of-date 2026-08-14
Expected sessions: 5   Completed: 0   Failed: 5   Missing: 0   Non-trading days: 0   Streak: 0
```

**This is the honest, correct answer** — these 5 real weekdays (2026-08-10 through 2026-08-14)
were never run through the actual authoritative daily runtime (only ad hoc manual testing occurred
during development), so no real `DailyIntelligenceArtifact` exists for any of them, and the tool
correctly reports `SESSION_FAILED` rather than fabricating completions. This is a **real historical
check against real infrastructure**, clearly distinct from **synthetic test sessions** (the
in-memory fixtures in `tests/test_phase_19_17_campaign_continuity.py`, which use explicitly
synthetic dates/holidays/artifacts, never real NSE dates or guessed holidays) and from the
**current live session** (today's status, sourced from the heartbeat file, which does not yet
exist — correctly reported `NO_DATA_YET`, not fabricated as either success or failure).

## Testing

**New**: `tests/test_phase_19_17_campaign_continuity.py`, 15 tests — fresh/stale heartbeat
(including a fix mid-phase: heartbeat file mtime must be explicitly pinned via `os.utime` in tests,
since `os.path.getmtime` reflects the real filesystem clock, not an injected `now`), verified
trading day, weekend, a synthetic listed holiday (explicitly not a real NSE date), unverified
calendar behavior, all five `classify_session` outcomes, streak calculation across a real-shaped
week with a deliberate gap, and the "never silently MISSING-as-success" invariant.

**Updated**: `tests/test_phase_19_16_market_calendar_gate.py` — heartbeat status string updated
from `NOT_A_TRADING_DAY` to `NON_TRADING_DAY` to match this phase's required vocabulary; re-run
clean, 5/5.

**Re-confirmed unchanged**: `tests/test_phase_19_13_live_intelligence_bridge.py` +
`tests/test_phase_19_14_1_commissioning_hardening.py` (order-safety, duplicate-runtime prevention,
FYERS-failure propagation) — 40/40.

## Full Regression

| | Count |
|---|---|
| Previous baseline | 5,971 |
| New tests this phase | 15 |
| **Final total** | **5,986** |
| Failures | **0** |

## Files Changed/Created

- `bujji/shadow_runtime/status.py` — additive (`stale`, `heartbeat_file_age_seconds`, `now`, `stale_threshold_seconds`)
- `run_daily_intelligence_session.py` — `NOT_A_TRADING_DAY` → `NON_TRADING_DAY` rename (from Phase 19.16's own work)
- `bujji/shadow_runtime/campaign_continuity.py` — new
- `bujji_campaign_status.py` — new
- `tests/test_phase_19_17_campaign_continuity.py` — new, 15 tests
- `tests/test_phase_19_16_market_calendar_gate.py` — updated for the rename

## What Remains External

**FYERS interactive authentication.** A human must complete the browser + TOTP/PIN login and
refresh `FYERS_ACCESS_TOKEN` in `/opt/bujji/.env` each trading morning — FYERS's own refresh-token
API is disabled per a SEBI-driven restriction, unfixable in this codebase. **The real, published
NSE holiday calendar** — a data-entry task, not a code gap, deliberately left unpopulated per this
phase's own instruction.

## Operational Model

```
HUMAN (once each trading morning):
    Refresh FYERS authentication.

BUJJI (automatically, afterward):
    Capture -> HistoricalObservationStore -> Market Reality -> Intelligence ->
    Decision Intelligence -> Phenomena -> State -> Environment -> Artifact ->
    EOD completeness -> LIVE/REPLAY equivalence -> heartbeat/status ->
    campaign continuity classification.

    On a weekend/verified holiday: skips cleanly, NON_TRADING_DAY.
    On any real failure: SESSION_FAILED (or INCOMPLETE), with the real reason recorded.
    On a day nothing ran at all: MISSING, once queried via bujji_campaign_status.py.
```

This is not autonomy from FYERS authentication — the objective, correctly stated, is that FYERS
authentication is the *only* recurring human action once installed. Commissioning itself (installing/
enabling the systemd timer) remains a separate, explicit operator action, not performed by this
phase.
