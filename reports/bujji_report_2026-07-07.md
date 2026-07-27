# BUJJI Daily Campaign Report — 2026-07-07

## 1. Session Overview
- Report generated at: 2026-07-07 22:27 IST
- Service state: **inactive (dead)** since 2026-07-07 13:33:00 IST (~9h before report generation). Not currently running and has not self-recovered.
- Restarts today (NRestarts): systemd restart counter climbed to **10** in a rapid burst between 13:32:01–13:33:00 IST, then hit `StartLimitBurst` ("Start request repeated too quickly") and gave up. `systemctl show` reports `NRestarts=0` for the final (non-restarting) attempt, but the journal clearly shows 10 restart cycles in ~60 seconds.
- Mode confirmed: `fyers_paper` / **PAPER** / **Live Orders: DISABLED ✓** (confirmed in every startup banner in the log)

## 2. System Health
| Item | Status | Evidence |
|---|---|---|
| auth_expired at any point | **FAIL** | `auth_expired_candle_fetch` CRITICAL logged 57 times, every 5 min from 09:15:01 to 13:30:01 IST. No successful refresh occurred at any point in that window. |
| fyers_token_refreshed_automatically | **NOT_YET / FAIL** | Zero successful automatic refreshes logged all day. At 13:32:01 the access token fully expired (code=-8) and every one of the 10 subsequent refresh attempts failed with code=-16: "Refresh token API is currently disabled to comply with SEBI regulations... refresh_token has likely expired (~15 days) or was already consumed." |
| clock_trusted | **NOT_OBSERVED** | No `clock_trusted` log lines found anywhere in today's jsonl log. |
| clock_drift_detected events | **NONE_FOUND** | No occurrences. |
| ws_connected / reconnects | **NOT_OBSERVED** | No `ws_health`/`ws_connected` events logged at all today — websocket layer appears to never have been reached, consistent with the candle/data pipeline being broken from market open onward. |
| ws_health_disconnected | **NONE_FOUND** | — |
| ws_health_stale_tick | **NONE_FOUND** | — |
| duplicate_candles_ignored | **0** | No occurrences. |
| candle_gap_detected | **NONE_FOUND** | — |
| Dashboard responding | **NO** | `curl http://127.0.0.1:8787/api/status` and `/api/trades` both returned empty — service is dead. |

## 3. Trades
**No trades taken.**

- Status API: unreachable (service dead at check time) — `last_decision`/`last_reason` could not be retrieved.
- Trade journal CSV (`data/trade_journal.csv`): does not exist (`NO_CSV`).
- ORB formation: cannot be confirmed either way — candle fetch began failing with auth errors at 09:15:01 IST, i.e. essentially at NSE market open, and never recovered for the rest of the session.
- Direction set: no evidence found.
- **Reason no entry was triggered:** the FYERS live-data candle fetch failed continuously with authentication errors from market open (09:15 IST) through 13:30 IST, and the service then crashed into a dead state at 13:32–13:33 IST when the access token itself expired and the refresh_token exchange failed outright — the strategy never had usable market data to evaluate an ORB/VWAP setup at any point today.

## 4. Warnings & Exceptions
| Timestamp(s) | Event | Detail |
|---|---|---|
| 09:15:01 – 13:30:01 (57 occurrences, every 5 min) | `auth_expired_candle_fetch` (CRITICAL) | `FYERS auth failure: code=-16 message=Could not authenticate the user` |
| 10:40:01, 11:20:01 | `candle_fetch_error` (ERROR, traceback) | `RuntimeError: FYERS historical error: code=-99 message=Bad request` — raised from `bujji/broker/fyers.py:297` via `hybrid.py:80` / `app.py:218`. Distinct from the auth errors; unexplained "Bad request" from the historical-data endpoint. |
| 13:32:01 | `fyers_access_token_invalid_attempting_refresh` (WARNING) | `code=-8 message=Your token has expired. Please generate a token` |
| 13:32:01 | `auth_error_detected` (INFO) | refresh_token exchange failed |
| 13:32:01 | `startup_blocked_auth_failure` (CRITICAL) | `code=-16 message=Refresh token API is currently disabled to comply with SEBI regulations.. The refresh_token has likely expired (~15 days) or was already consumed — run the interactive login flow again to obtain a new one.` |
| 13:32:01 – 13:32:55 (repeats at 13:32:07, :13, :19, :25, :31, :37, :43, :49, :55 — 10 total) | Same 4-line auth failure sequence on every restart attempt | Each restart hit the identical `startup_blocked_auth_failure`, i.e. the crash was deterministic and unrecoverable by simply restarting. |
| 13:33:00 | `systemd: Start request repeated too quickly` | Service gave up restarting; left **inactive/dead**. No further attempts since (confirmed still inactive at 22:27 IST report time). |
| `recovery_orphan_position`, `exit_incomplete`, `eod_square_off_error`, `EXITING` | **NONE** | No occurrences (consistent with zero positions ever opened). |

No other tracebacks/exceptions found in today's log.

## 5. Production Acceptance Criteria — Daily Check

| ID | Criterion (abbreviated) | Today | Evidence |
|----|--------------------------|-------|----------|
| A1 | ≥15 sessions run end-to-end | UNPROVEN (accumulates) | Today's session did not run end-to-end — crashed into dead state mid-day. |
| A2 | ≥5 completed paper trades | UNPROVEN (accumulates) | 0 trades today. |
| B1 | Zero duplicate orders | PASS | No `order_submit`/`order_confirmed` events at all (no orders placed). |
| B2 | Zero orphan positions at EOD | PASS | No positions ever opened. |
| B3 | No overnight positions | PASS | N/A — no positions. |
| B4 | Exit completeness | PASS | N/A — no positions to exit; no `exit_incomplete`/stuck `EXITING`. |
| B5 | One trade per day | PASS | 0 trades ≤ 1. |
| C1 | Zero unrecovered crashes | **FAIL** | Service crashed 10x in a burst and is still inactive/dead ~9h later — unrecovered. |
| C2 | Restart-in-position drill | UNPROVEN until drill | Not run. |
| C3 | Orphan handling proven/never-triggered | UNPROVEN | No `recovery_orphan_position` events (never triggered — no positions existed to orphan). |
| C4 | No wedged states | **FAIL** | Service is currently wedged in a dead state, not `DONE_FOR_DAY`/idle. |
| D1 | Auto token renewal succeeded | **FAIL** | 10/10 refresh attempts failed; access token never renewed. |
| D2 | No silent auth failures | PASS | Every failure was logged loudly (CRITICAL), not silent — though the underlying auth problem itself was not resolved (see D1). |
| D3 | Stable WS reconnects | N/A | No `ws_health_*` events observed — websocket layer never reached today. |
| D4 | No stale-tick mistakes | N/A | No tick/reassessment activity observed. |
| E1 | Journal consistency | PASS | No CSV and no trades API data — consistent (both empty), though CSV file not existing at all is worth confirming is expected pre-first-trade. |
| E2 | Dashboard consistency | **FAIL** | Dashboard/API not responding at check time (service dead). |
| E3 | Market data integrity | **FAIL** | Candle fetch broken via auth errors essentially all session, plus 2x unexplained `code=-99 Bad request` errors. No `vwap_real`/"REAL VOLUME" confirmations found. |
| E4 | Clock integrity | NOT_OBSERVED | No `clock_trusted` log lines found today. |
| F1 | No unexplained exceptions | **FAIL** | `RuntimeError: FYERS historical error: code=-99 message=Bad request` (x2) is not explained by the auth-expiry root cause and warrants investigation. |
| F2 | Test suite green | PASS | `157 passed, 1 warning in 1.97s` (warning is an unrelated `pkg_resources` deprecation notice from the fyers_apiv3 dependency). |
| F3 | Code frozen | UNPROVEN | First report generated — no prior baseline commit recorded to compare against. Current HEAD: `d03914a Dashboard operator-safety improvements (P1-P4)`. |

## 6. Overall Day Verdict

**FAIL** — Root cause: the FYERS `refresh_token` is expired/consumed, and FYERS has disabled the refresh_token exchange API "to comply with SEBI regulations." This blocked all live candle data from market open (09:15 IST) onward, guaranteed zero trading opportunity, and ultimately crashed the service into a dead state that has not self-recovered for ~9 hours.

FAILs: **C1** (unrecovered crash), **C4** (wedged/dead service), **D1** (token auto-renewal failed), **E2** (dashboard down), **E3** (market data integrity broken), **F1** (unexplained `code=-99` errors).

WARNINGS:
- The candle-fetch loop retried the same failing auth call every 5 minutes for 4+ hours (57 times) without escalating or backing off — worth adding a circuit breaker/alert after N consecutive auth failures.
- No clock/websocket-health telemetry observed at all today — either expected (feature not yet reached) or a gap in what's being logged; worth confirming which.

## 7. Cumulative Campaign Counters
(No prior reports exist in `/opt/bujji/app/reports/` — this is report #1.)
- Sessions run to date: 1 (today — did not complete end-to-end)
- Total trades to date: 0
- FAILs encountered to date: C1, C4, D1, E2, E3, F1 (2026-07-07)
- Drills completed: C2 restart-in-position — no; VPS reboot — no; D1 token renewal — no (in fact D1 failed today)

## 8. Recommended Actions
1. **URGENT / manual action required:** Run the interactive FYERS login flow on the VPS to obtain a fresh access_token/refresh_token, then manually restart `bujji.service` — it will not self-heal; systemd exhausted its restart budget and is sitting dead.
2. Investigate the SEBI-regulation change disabling the refresh_token exchange API (per the logged message). If this is a broader/permanent FYERS policy change, the current auto-refresh design may need rework (e.g., daily interactive re-auth) — this is a design question for the operator, not something applied automatically here.
3. Investigate the two `FYERS historical error: code=-99 message=Bad request` occurrences (10:40:01, 11:20:01) — distinct from the auth failures and currently unexplained.
4. Consider adding a circuit breaker/alert on the candle-fetch loop so 4+ hours of identical auth failures doesn't go unescalated until a human checks the report.
5. Confirm whether clock/websocket-health logging is expected to be silent at this stage of the campaign, or whether it indicates missing instrumentation.
