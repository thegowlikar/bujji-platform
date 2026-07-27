# BUJJI Daily Campaign Report — 2026-07-20

## 1. Session Overview
- Report generated at: 2026-07-20 16:26:07 IST
- Service state: **active (running)** at check time — current process started 16:20:07 IST, ~6 min uptime at report time
- Restarts today: systemd `NRestarts` shows **1** (counter was reset by the most recent manual `systemctl stop`/`start` at 16:16:25). The full-day journal shows this understates activity: **20 total service starts today** (4 manual `Stopping bujji.service` events + 16 systemd auto-restarts), driven by a repeating restart cycle described in §4.
- Mode confirmed: **PAPER** / Broker: **fyers_paper** / Market data source: **FYERS LIVE** / Execution dest.: **PAPER LEDGER** / **Live Orders: DISABLED ✓** — confirmed in every startup banner all day, no exceptions.

## 2. System Health
- **auth_expired — FAIL.** `auth_expired_candle_fetch` (CRITICAL, code=-16 "Could not authenticate the user") logged **63 times**, every 5 minutes from **09:15:01 to 14:25:01** (5h10m — essentially the entire pre-lunch session). Only stopped when the service was manually restarted at 14:26:51; the very next cycle succeeded normally. No automatic recovery occurred during the outage.
- **fyers_token_refreshed_automatically: NOT_YET.** No such event was logged today. Recovery coincided exactly with a manual service restart, not a background auto-refresh.
- **clock_trusted: true** throughout — `clock_trusted: true` in the live status API, and no `clock_drift_detected` events anywhere in today's journal. PASS.
- **clock_drift_detected events:** none.
- **ws_connected: false** at EOD (status API), `ws_connect_count: 0`. No `ws_health_disconnected` / `ws_health_stale_tick` events logged at all — the websocket tick layer was never engaged today (consistent with no position ever being opened).
- **duplicate_candles_ignored: 0**.
- **candle_gap_detected events:** none found.
- **Dashboard responding: yes** — `/api/status` returned a well-formed HTTP 200 JSON payload at check time.

## 3. Trades
No trades taken.
- `last_decision` / `last_reason` (status API, post-restart at 16:20): both `null` — this reflects the freshly-restarted process, not the day's activity.
- From the JSONL log, the day's one and only signal: at **14:30:01** the ORB completed (`orb_complete`, spot close 24254.75) and `straddle_signal_generated` fired, transitioning WAITING → READY → CONFIRMED.
- ORB formed: **yes** (very late in the day — 14:25 candle — because the 09:15–14:25 auth outage prevented any earlier candle fetch).
- Direction: `SIGNAL_GENERATED` logged `direction: null` — expected for this straddle strategy (non-directional).
- **Why no entry:** `entry_blocked_capital` — capital health check found `usable_margin=₹162,000.00 < margin_required_per_lot=₹215,451.35` (broker-verified via `fyers_span_margin_certified`), so `maximum_safe_lots=0` and the trade was refused. State rolled back CONFIRMED → READY, then the day ended via `eod_no_position` → `DONE_FOR_DAY` at 15:05:01.

## 4. Warnings & Exceptions
Grep across the full day's journal + JSONL for `traceback`, `exception`, `_error`, `candle_processing_error`, `eod_square_off_error`, `exit_incomplete`, `EXITING`, `recovery_orphan_position`: **NONE found.** No Python exceptions or stuck-exit states today.

Two non-exception operational issues worth flagging:
1. **5h10m auth outage** (09:15:01–14:25:01, 63 consecutive CRITICAL logs) consumed almost the entire session before recovering, and recovery required a manual restart rather than self-healing.
2. **Repeating ~5-minute restart cycle after EOD.** From 15:05 through 16:20, the service repeatedly reached `WAITING → DONE_FOR_DAY (eod_no_position)`, then logged `Shutting down gracefully` and was restarted by systemd roughly 5 minutes later, re-entered `WAITING`, immediately re-detected `eod_no_position`, and repeated. This produced 16 systemd auto-restarts (restart counter cycling 1→8, reset, 1→4, reset, 1→3, reset, 1) plus 4 manual `systemctl stop`/`start` interventions, for 20 total starts today. This looks like a design gap (the app appears to exit on reaching `DONE_FOR_DAY` instead of idling, and `Restart=always` brings it back up into the same terminal state) rather than a crash — no error was logged at any of these transitions — but it generates a lot of restart churn for something that should be quiescent post-EOD.

## 5. Production Acceptance Criteria — Daily Check

| ID | Criterion (abbreviated) | Today | Evidence |
|----|--------------------------|-------|----------|
| A1 | ≥15 sessions run end-to-end | UNPROVEN (accumulates) | — |
| A2 | ≥5 completed paper trades | UNPROVEN (accumulates) | 0 trades today |
| B1 | Zero duplicate orders | PASS | No `order_submit`/`order_confirmed` events at all — zero orders placed |
| B2 | Zero orphan positions at EOD | PASS | No position ever opened; nothing to orphan |
| B3 | No overnight positions | PASS | N/A — no positions |
| B4 | Exit completeness | PASS | No `exit_incomplete` / stuck `EXITING`; no positions to exit |
| B5 | One trade per day | PASS | 0 trades ≤ 1 |
| C1 | Zero unrecovered crashes | PASS | No Python crash/traceback; service is currently active. (See restart-churn warning above — not a crash, but worth monitoring.) |
| C2 | Restart-in-position drill | UNPROVEN until drill | Not run |
| C3 | Orphan handling proven/never-triggered | UNPROVEN | No `recovery_orphan_position` events — never triggered (no positions existed) |
| C4 | No wedged states | PASS | Reached `DONE_FOR_DAY` repeatedly (not stuck); flagged the restart-cycling as a separate warning, not a wedge |
| D1 | Auto token renewal succeeded | **FAIL** | 63 CRITICAL auth failures over 5h10m with no `fyers_token_refreshed_automatically` event; recovery only after manual restart |
| D2 | No silent auth failures | PASS | Every failure was logged loudly (CRITICAL), not silent — underlying issue itself is captured under D1 |
| D3 | Stable WS reconnects | N/A | No `ws_health_*` events — WS layer never engaged (no position opened) |
| D4 | No stale-tick mistakes | N/A | No tick data observed |
| E1 | Journal consistency | PASS | CSV absent (`NO_CSV`) and trades API empty (`[]`) — consistent, both show zero trades |
| E2 | Dashboard consistency | PASS | `/api/status` returned HTTP 200 with sane fields |
| E3 | Market data integrity | PASS | Real `CANDLE_CLOSED` events with real close prices (e.g. 24254.75, 24242.9, 24230.15...) ingested correctly once auth recovered; `premium_vwap` audit staying at `candles_used: 0` is expected since no straddle position was ever opened to generate premium candles |
| E4 | Clock integrity | PASS | `clock_trusted: true` all day; zero `clock_drift_detected` events |
| F1 | No unexplained exceptions | PASS | No traceback/exception hits; the day's only CRITICAL condition (auth failure) is fully explained (code=-16) |
| F2 | Test suite green | PASS | `396 passed, 1 warning in 3.40s` (warning is an unrelated `pkg_resources` deprecation from the `fyers_apiv3` dependency) |
| F3 | Code frozen | PASS | HEAD is `d03914a Dashboard operator-safety improvements (P1-P4)` — same commit reported as HEAD in the 2026-07-07 report; no code changes since |

## 6. Overall Day Verdict
**FAIL** — one criterion failed: **D1** (automatic FYERS token renewal did not succeed; the service ran with an expired/invalid token for over 5 hours and only recovered via a manual restart).

WARNINGS (not FAILs, but worth monitoring):
- The ~5-minute post-EOD restart cycle (20 total service starts today) — see §4.
- The one trading signal of the day was blocked entirely by insufficient margin (₹162,000 usable vs ₹215,451.35 required per lot) — a capital/config matter, not a code bug, but it meant even a fully healthy session today would not have traded.
- A 13-day gap exists between this report and the only prior report on file (2026-07-07) — see §7.

## 7. Cumulative Campaign Counters
(Only one prior report exists in `/opt/bujji/app/reports/`: `bujji_report_2026-07-07.md`.)
- Sessions run to date: **2** (2026-07-07, 2026-07-20) — both ended in FAIL verdicts. Note: there is a 13-day gap between these two report dates with no report file in between; this run cannot determine from the VPS alone whether the scheduled task simply didn't fire on those days, or the market was closed for all of them — worth checking the scheduler's own run history.
- Total trades to date: **0**
- FAILs encountered to date:
  - 2026-07-07: C1, C4, D1, E2, E3, F1 (service crashed dead after 10 consecutive auth-refresh failures, never self-recovered)
  - 2026-07-20: D1 (auth failed for 5h10m, recovered only via manual restart; service itself stayed up throughout, unlike 07-07)
- Drills completed: C2 restart-in-position drill — **no**; VPS reboot drill — **no**; D1 automatic token renewal — **no** (failed on both reported sessions to date)

## 8. Recommended Actions
1. **D1 is now 0-for-2.** The auto-refresh path for the FYERS token has not been observed to work on either reported session. Prioritize either fixing automatic renewal or, if FYERS' refresh-token API is policy-restricted (per the 2026-07-07 finding), design a supported daily re-auth flow (e.g., a lightweight interactive/semi-automated re-auth step run before market open) so sessions don't lose hours of trading window waiting on a human to notice.
2. Investigate the repeating ~5-minute restart cycle after `DONE_FOR_DAY` (§4) — confirm whether the app is intentionally exiting on reaching EOD and relying on systemd `Restart=always` to bring it back (which then re-triggers the same EOD exit), and consider having it idle quietly post-EOD instead.
3. Add alerting after N consecutive `auth_expired_candle_fetch` CRITICAL logs (e.g. after 3-4, ~15-20 min) rather than relying solely on this once-daily report — today's outage ran unactioned for over 5 hours.
4. Review margin/capital sizing assumptions: confirm whether the account is expected to be funded above the current ₹180,000 equity level, or whether lot sizing / the 90% safety buffer should be tuned, given the one signal generated today was blocked entirely on capital grounds.
5. Confirm with the scheduler/cron history why no report exists between 2026-07-07 and 2026-07-20 (13 days) — determine if this indicates missed runs versus market holidays/weekends, to keep the cumulative counters in §7 trustworthy going forward.
