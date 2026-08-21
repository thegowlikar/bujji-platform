# Phase 19.16 — Trading Calendar Gate

Closes item 16 from the prior forensic audit: `run_daily_intelligence_session.py` had zero
trading-calendar awareness. Wires the already-existing, unmodified `bujji.market_calendar.MarketCalendar`
(Sprint 112 Deliverable 6) into the entrypoint. No systemd action was taken — nothing installed,
enabled, started, or stopped.

## The gap

Before this change, a systemd timer firing on an NSE holiday would proceed exactly like a normal
trading day: acquire the lock, attempt real capture, and only discover after the fact — via
`within_market_hours()`'s own weekday check inside the capture scripts, or via completeness
reporting `EMPTY` — that nothing useful happened. Weekends were already handled correctly this way
(capture's own weekday check), but there was no *proactive* check, and holidays specifically were
never distinguished from ordinary days at all.

## What already existed and was reused verbatim

`bujji/market_calendar.py`'s `MarketCalendar` class — found during the prior audit, previously
unwired anywhere. It already provides `is_trading_day(date) -> (bool, reason)` (weekend + listed
holiday + manual-closure checks), `next_trading_day()`, and its own honest
`verification_warning()` when `HOLIDAY_CALENDAR` (an intentionally empty template) hasn't been
populated and marked verified. **Not one line of this module was changed.**

## The fix

In `run_daily_intelligence_session.py`'s `_main()`, before the lock is acquired and before any
broker construction, for real (non-`--dry-run`) invocations only:

```python
calendar = MarketCalendar()
warning = calendar.verification_warning()
if warning:
    print(f"WARNING: {warning}", file=sys.stderr)
is_trading_day, reason = calendar.is_trading_day(date.fromisoformat(session_date))
if not is_trading_day:
    print(f"not a trading day, skipping cleanly: {reason}")
    write_daily_heartbeat(args.heartbeat_path, DailySessionHeartbeat(
        session_date=session_date, runtime_status="NOT_A_TRADING_DAY",
        last_observation_timestamp=None, last_intelligence_cycle_timestamp=None,
        rows_captured_today=0, last_error=None,
    ))
    return 0  # correctly doing nothing on a non-trading day IS success.
```

- Uses `write_daily_heartbeat()` (Phase 19.11, unmodified) — no new persistence mechanism.
- `runtime_status="NOT_A_TRADING_DAY"` — a new *string value* on an already-plain-`str` heartbeat
  field, not a new lifecycle stage; `DailySessionLifecycle`'s own transition table (frozen, tested,
  used elsewhere) was not touched, since this path never constructs `DailySessionRuntime` at all.
- Exit code 0 — a non-trading day producing no session is the correct, intended outcome, not a
  failure.
- `--dry-run` is deliberately **not** gated — its own established purpose (Phase 19.11 onward) is
  exercising the orchestration wiring on demand, any day.
- `MarketCalendar.verification_warning()` is printed on every real invocation, since
  `HOLIDAY_CALENDAR` ships empty — this keeps the disclosed limitation loud rather than silently
  relying on an unpopulated list.

## Verification

Live, against the real deployed entrypoint (subprocess, not a re-implementation), run today
(2026-08-15, a real Saturday):

```
WARNING: HOLIDAY_CALENDAR has not been marked verified against a real, published NSE trading
calendar (holiday_calendar_verified=False) -- weekends are correctly detected by real date
arithmetic, but any UNLISTED NSE holiday will be incorrectly treated as a trading day until an
operator populates and verifies the real holiday list for the relevant year(s).
not a trading day, skipping cleanly: 2026-08-15 is a weekend (Saturday)
EXIT=0
```
Heartbeat: `{"runtime_status": "NOT_A_TRADING_DAY", "last_error": null, ...}`. Lock file: never
created (confirmed absent — the gate runs before `ProcessLock` is ever constructed).

`--dry-run` on the same weekend date: proceeds exactly as before this change (stub capture/
intelligence report themselves skipped, `runtime_status="FAILED"`, never `NOT_A_TRADING_DAY`) —
confirmed the calendar gate does not intercept it.

**New tests**, `tests/test_phase_19_16_market_calendar_gate.py` (5, all against the real subprocess
entrypoint):
1. `test_market_calendar_correctly_identifies_a_real_weekend` — sanity on the unmodified class.
2. `test_real_invocation_on_a_weekend_skips_cleanly_without_lock` — the core fix, live.
3. `test_real_invocation_on_a_weekday_does_not_short_circuit_on_calendar` — a real trading day
   (2026-08-14) is never misclassified as non-trading.
4. `test_dry_run_ignores_the_calendar_on_a_weekend` — dry-run's own behavior unchanged.
5. `test_verification_warning_printed_for_every_real_invocation` — the disclosed limitation stays
   visible.

All 5 pass. Order-safety re-confirmed: `tests/test_phase_19_13_live_intelligence_bridge.py` +
`tests/test_phase_19_14_1_commissioning_hardening.py` — 40/40 clean; the only occurrence of
`place_order`/`modify_order`/`cancel_order` text anywhere in the changed file is inside an existing
docstring comment, not a call.

## Full Regression

| | Count |
|---|---|
| Previous baseline | 5,966 |
| New tests this phase | 5 |
| **Final total** | **5,971** |
| Failures | **0** |

## What this does not resolve

`HOLIDAY_CALENDAR` is still an empty template — weekends are now correctly, proactively skipped,
but a real NSE holiday falling on a weekday will still be attempted as a normal session until an
operator populates and verifies the real holiday dates in `bujji/market_calendar.py`. That step is
external (data entry against the real published NSE calendar), not a code gap, and
`verification_warning()` will keep surfacing it on every real run until it's done. This is
unchanged from the prior audit's own classification of that half of item 16 as external.

## Commissioning status

Unaffected by this phase — still gated on the FYERS token refresh and your explicit approval to
install/enable/start `bujji-daily-intelligence.timer`, as before.
