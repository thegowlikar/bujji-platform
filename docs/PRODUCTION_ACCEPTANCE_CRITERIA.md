# BUJJI — Production Acceptance Criteria

Objective, pass/fail gate that must be **fully met** before any live capital is
deployed. The paper campaign is the evidence-gathering phase; this document is
how you decide it succeeded. Nothing here is subjective — every criterion is
checkable from logs, the journal, and the dashboard, using the checks in
`docs/PAPER_CAMPAIGN_RUNBOOK.md`.

**Rule:** any single **FAIL** blocks live deployment. A criterion with
insufficient evidence (e.g. an event that never occurred during the window) is
**not** a pass — it is "unproven", and unproven items must be resolved (by
running longer, or by a deliberate, logged drill) before the gate opens.

---

## A. Campaign duration & trade volume

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| A1 | Minimum successful sessions | **≥ 15 NSE trading days** run end-to-end with no unresolved FAIL below | daily/EOD checklists |
| A2 | Actual trades observed | **≥ 5 completed paper trades** (entry + exit + journal), so the trade path is exercised, not just the idle path | `trade_journal.csv` |

*Rationale: a handful of quiet days proves the idle path, not the trading path.
Both a duration floor and a trade-count floor are required.*

## B. Order correctness (capital-safety core)

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| B1 | Zero duplicate orders | No trade produced two entry or two exit fills for one intended action | `journalctl \| grep order_submit/order_confirmed`; one entry + one exit per journal row |
| B2 | Zero orphan positions at EOD | Every session ended flat; broker `netPositions` empty after 15:15 | EOD checklist; `recovery_*` logs |
| B3 | No overnight positions | No position ever carried across a session boundary | journal `exit_time` same day as `entry_time`, always |
| B4 | Exit completeness | No `exit_incomplete` / stuck `EXITING` state left unresolved | `grep -iE "exit_incomplete\|EXITING"` |
| B5 | One-trade-per-day honored | No session recorded > 1 trade | `trade_journal.csv` grouped by date |

## C. Resilience & recovery

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| C1 | Zero unrecovered crashes | Every process exit was either a clean EOD shutdown or was auto-restarted by systemd and resumed correctly | `journalctl -u bujji`; `systemctl status` |
| C2 | Restart recovery proven | At least **one** real restart-while-in-position drill performed, producing `recovery_resumed` with tick monitoring re-armed | deliberate drill (see §Drills) |
| C3 | Orphan handling proven OR cleanly never-triggered | Either an orphan was correctly flattened in a drill, or it provably never occurred (no `recovery_orphan_position` outside a drill) | `grep recovery_orphan_position` |
| C4 | No wedged states | No session ended stuck in `CONFIRMED`/`EXITING`; every day reached `DONE_FOR_DAY` or was cleanly flat/idle | `state_transition` logs |

## D. Connectivity & tokens

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| D1 | Automatic token renewal succeeded | At least **one** `fyers_token_refreshed_automatically` observed, followed by continued normal operation (this is the one link not verifiable pre-campaign) | `grep fyers_token_refreshed_automatically` |
| D2 | No silent auth failures | Every `auth_expired` / auth error was followed by either a successful auto-refresh or a clear operator action — never a silent stall | auth-related logs |
| D3 | Stable WebSocket reconnects | Every `ws_health_disconnected` was followed by a reconnect (`ws_health_connected`, `Reconnect count` incremented) with no permanent loss of tick monitoring while in a position | `ws_health_*` logs; dashboard |
| D4 | No stale-tick-driven mistakes | Any `ws_health_stale_tick` did not cause a wrong exit; the candle-driven backstop remained correct | tick + reassessment logs |

## E. Data & observability consistency

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| E1 | Journal consistency | CSV and SQLite journals agree; every trade in the logs has exactly one matching journal row with sane fields (entry/exit/pnl/reason) | `trade_journal.csv` vs `bujji.db` vs logs |
| E2 | Dashboard consistency | Dashboard state (position, MTM, health, WS status) matched the logs whenever spot-checked | manual spot-checks |
| E3 | Market-data integrity | VWAP shown as `REAL VOLUME` on trading days (not fallback); no unexplained `duplicate_or_stale_candle_ignored` storms or unhandled `candle_gap_detected` | Market Data Health; C_D1 logs |
| E4 | Clock integrity | `clock_trusted` stayed true (or any `clock_drift_detected` was understood and did not cause a mis-timed action) | D1/D2 logs |

## F. Software stability

| # | Criterion | Pass condition | Evidence source |
|---|---|---|---|
| F1 | No unexplained runtime exceptions | Zero unhandled tracebacks / `candle_processing_error` / `eod_square_off_error` that aren't explained and benign | `grep -iE "traceback\|exception\|_error"` |
| F2 | Test suite still green | `pytest -q` passes on the deployed commit (157/157 at time of writing) | run on the VPS |
| F3 | Code frozen during campaign | No strategy or code change was made mid-campaign that would invalidate the accumulated evidence | git log vs campaign window |

---

## Required drills (evidence that can't come from passive running)

Some criteria need a deliberate action because they may never occur naturally in
a short window. Perform each **once**, outside market hours or in a way that
cannot affect a real session, and keep the log excerpt as evidence:

1. **Restart-in-position (C2):** while a paper position is open, `systemctl
   restart bujji`; confirm `recovery_resumed` + `tick_engine_started` and that a
   subsequent stop-loss still fires.
2. **Reboot (C1):** `sudo reboot` the VPS; confirm the service auto-starts and
   reconciles.
3. **Token renewal (D1):** the first real ~24h token rollover under configured
   refresh credentials — confirm the automatic-refresh log line. (Naturally
   occurs within the first day or two of the campaign; no artificial trigger
   needed.)

---

## The gate

Live capital is authorized **only when**:

> All A–F criteria are **PASS**, all three drills are completed with evidence,
> and there are **zero** open "unproven" items.

Even then, the first live deployment should be **limited capital** (minimum lot
size), because paper P&L excludes fees and slippage (`docs/UNATTENDED_READINESS.md`)
— so paper profitability does **not** predict live profitability, only that the
*machinery* is sound. This gate certifies operational reliability, not that the
strategy makes money.

Sign-off (operator records): campaign start date, sessions counted, trades
observed, drills completed (with dates), and any criterion that required a
second look and why it ultimately passed.
