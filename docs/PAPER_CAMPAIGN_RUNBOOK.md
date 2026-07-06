# BUJJI — Paper Campaign Operator Runbook

Operate BUJJI in `fyers_paper` mode (live FYERS market data, **paper-only
execution — no real orders can be placed**). Written to be usable with no prior
project knowledge. Assumes the platform is already installed per
`docs/VPS_DEPLOYMENT.md` (systemd service `bujji`, code at `/opt/bujji/app`,
credentials in `/opt/bujji/.env`).

**Strategy in one paragraph (context only — do not change it):** NIFTY
options-selling. Builds an Opening Range 09:15–09:20 IST, then on each completed
5-minute candle looks for a VWAP+ORB breakout with a strong candle body. On a
bullish breakout it **sells the ATM Put**; on bearish, **sells the ATM Call**.
**One trade per day maximum.** It holds while the thesis stays valid (price on
the right side of VWAP, momentum intact) and exits when the thesis breaks, a
tick-driven stop-loss triggers, or at the 15:15 hard exit. No overnight
positions.

Market clock: NSE cash/derivatives trade **09:15–15:30 IST, Mon–Fri**, excluding
NSE holidays. BUJJI's trading window is 09:15–15:15 with a hard flat by 15:15.

---

## 1. Exact startup procedure

Normally BUJJI runs continuously under systemd and you never start it by hand.
To start/verify:

```bash
sudo systemctl start bujji         # start (no-op if already running)
systemctl is-active bujji          # -> active
journalctl -u bujji -n 40 --no-pager
```

There is nothing to "start each morning" if the service is enabled — it stays
up across days and the strategy simply arms itself at 09:15. (The one recurring
manual task is the ~fortnightly token bootstrap — see §12.)

## 2. Expected startup logs

A clean start prints the banner and these lines (order as shown):

```
paper_mode_live_data_active: using LIVE FYERS market data with PAPER-ONLY execution ...
==============================================================
  Bujji ORB-VWAP ATM Seller  v0.9-paper
  Mode:               PAPER
  Broker:             fyers_paper
  Market data source: FYERS LIVE
  Execution dest.:    PAPER LEDGER
  Live Orders: DISABLED ✓
==============================================================
... Dashboard at http://127.0.0.1:8787
... reconcile                          (checks broker for any existing position)
... Bujji started | broker=fyers_paper
```

**Must be true:** `Mode: PAPER`, `Live Orders: DISABLED ✓`,
`broker=fyers_paper`, and **no** `auth_expired` / `startup_blocked_auth_failure`
line. If you see an auth failure at startup, the token needs refreshing (§12).

## 3. Expected market-open behavior (≈09:15–09:20 IST)

- The candle loop wakes on each 5-minute boundary and fetches the just-closed
  candle. Before 09:15 and after 15:15 it idles (see §4).
- During 09:15–09:20 the Opening Range is built. You will see `state_transition`
  to `READY` with reason `orb_complete` once the ORB window closes (~09:20).
- Every cycle emits a `vwap_audit` line; while flat these show
  `trade_state: FLAT`.

## 4. Expected idle behavior

- **Before market open / after 15:15 / on holidays:** the loop ticks every 5
  minutes, sees it is outside the trading window, and does nothing. Started
  outside market hours it will transition straight to `DONE_FOR_DAY` at the
  first boundary past 15:15 (reason `eod_no_position`) and shut down for the day
  — this is correct, not a fault.
- **Intraday, flat, no signal yet:** state stays `READY`, one `reassessment`/
  `vwap_audit` per candle, no orders. This is the normal majority state.

## 5. Expected entry workflow (happens at most once/day)

When a candle completes a valid breakout, in one cycle you'll see roughly:

```
signal_generated            (direction + thesis narrative)
state_transition  READY -> CONFIRMED   reason signal_confirmed
order_submit                (SELL, the resolved ATM option symbol, qty)
order_confirmed             (filled at the LIVE option premium)
position_opened             (symbol, qty, entry_premium)
state_transition  CONFIRMED -> IN_POSITION   reason position_filled
tick_engine_started         (WebSocket now streaming that contract's ticks)
```

Dashboard now shows `Direction`, `Position` (the real `NSE:NIFTY...CE/PE`
symbol), `Entry`, live `MTM`, and under **Tick / WebSocket Health**:
`WS Connected: yes` with a small `Last tick age`.

## 6. Expected exit workflow

Exit fires on the **first** of: thesis break on a candle, a tick-driven
stop-loss, or the 15:15 hard exit. You'll see one of:

```
# candle-driven:
reassessment  decision=EXIT reason=trend:lost_vwap; ...
# OR tick-driven (faster, between candles):
tick_exit_triggered  reason=tick_stop_loss(...)
# then, common to both:
order_submit / order_confirmed   (BUY to close, at live premium)
trade_journaled  pnl=... reason=...
state_transition -> DONE_FOR_DAY  reason trade_closed
```

After exit: position flat, one new row in the journal (§10), dashboard `MTM`
clears, and — because it's one-trade-per-day — the bot stays flat until the next
session (a further qualifying candle is ignored; you may see
`max_trades_reached` if one occurs).

## 7. Expected end-of-day shutdown

At/after 15:15 IST, wall-clock driven (independent of candle arrival):

- **If flat:** `state_transition -> DONE_FOR_DAY` reason `eod_no_position`, then
  `Shutting down gracefully`, and the process exits. systemd's `Restart=always`
  brings it back up; it idles until the next session.
- **If still holding:** a forced square-off (`eod_hard_exit` → `squared_off`)
  flattens the position first, journals it, then shuts down. **No overnight
  position is ever left open.**

## 8. Restart procedure

```bash
sudo systemctl restart bujji
journalctl -u bujji -n 40 --no-pager    # verify clean startup (§2)
```

Do restarts outside market hours when possible. The single-instance lock (F4)
guarantees the old and new processes never overlap.

## 9. Recovery procedure (after a crash / reboot)

Automatic — no operator action needed. On restart, BUJJI reconciles against the
broker and logs exactly one of:

- `recovery_resumed` — an open position was found and re-adopted; tick
  monitoring re-armed. Normal.
- `position_already_closed` — the snapshot had a position but the broker is
  flat; finalized safely.
- `recovery_orphan_position` → `orphan_flattened` — the broker had a position
  BUJJI didn't recognize; it was flattened for safety (also sets `healthy=false`
  so you notice — investigate why an orphan existed).

To confirm after any unexpected restart:
```bash
journalctl -u bujji | grep -E "recovery_|orphan"
```

## 10. Log & journal locations

- **systemd journal (everything):** `journalctl -u bujji [-f]`
- **Structured JSONL app log:** `/opt/bujji/app/logs/bujji_<YYYY-MM-DD>.jsonl`
  (one per day; every event referenced in this runbook is a `msg` field there).
- **Trade journal (the record of trades):**
  - `/opt/bujji/app/data/trade_journal.csv` (human/Excel)
  - `/opt/bujji/app/data/bujji.db` (SQLite, same data)
- **Recovery snapshot:** `/opt/bujji/app/data/session_state.json`
- **Instance lock:** `/opt/bujji/app/data/bujji.lock`

## 11. Dashboard & health checks

Dashboard binds to localhost only; reach it via SSH tunnel from your laptop:
```bash
ssh -L 8787:127.0.0.1:8787 <user>@<vps>     # then open http://127.0.0.1:8787
```
Check on the dashboard:
- **Market Data Health:** VWAP shows `REAL VOLUME` (not fallback) once candles flow.
- **Tick / WebSocket Health:** while in a position, `WS Connected: yes`,
  `Last tick age` small (seconds); `Reconnect count` may rise on transient drops
  — that's the SDK auto-reconnecting, not a fault.
- Top badge shows `HEALTHY`. If `UNHEALTHY`, read `health_detail`.

CLI health check (no tunnel needed):
```bash
journalctl -u bujji --since "09:00" | grep -E "ws_health|auth|error|EXIT|recovery|stale"
```

## 12. Token refresh verification

- **Automatic (daily):** if the refresh credentials are set (§ VPS guide),
  BUJJI renews the access token itself. Confirm with:
  ```bash
  journalctl -u bujji | grep fyers_token_refreshed_automatically
  ```
- **Manual bootstrap (~every 15 days, or if you see an auth failure):** run the
  interactive login (`fyers_token_refresh.py` on a machine with a browser),
  copy the new `FYERS_ACCESS_TOKEN` and `FYERS_REFRESH_TOKEN` into
  `/opt/bujji/.env`, then `sudo systemctl restart bujji`. Full detail:
  `docs/FYERS_TOKEN_LIFECYCLE.md`.

## 13. Daily operator checklist (run before 09:15 IST)

- [ ] `systemctl is-active bujji` → `active`
- [ ] Startup banner in logs shows `Mode: PAPER`, `Live Orders: DISABLED ✓`
- [ ] No `auth_expired` / `startup_blocked_auth_failure` since last (re)start
- [ ] Server clock synced: `timedatectl` → "System clock synchronized: yes"
- [ ] Is today an NSE trading day? (skip holidays)
- [ ] Dashboard badge `HEALTHY`; `clock_trusted` true
- [ ] Disk has room: `df -h /opt` (JSONL logs are not auto-rotated)

## 14. End-of-day review checklist (after 15:15 IST)

- [ ] Position is flat (no `IN_POSITION` in the latest logs; `netPositions` empty)
- [ ] If a trade occurred: exactly **one** new row in `trade_journal.csv`, and it
      matches the `position_opened`/`trade_journaled` log events (symbol, entry,
      exit, reason, pnl)
- [ ] No `orphan` / `exit_incomplete` / unexplained `error` lines:
      `journalctl -u bujji --since 09:00 | grep -iE "orphan|incomplete|traceback|exception|error"`
- [ ] `healthy` ended the day true (or the reason it didn't is understood)
- [ ] WebSocket: any `ws_health_disconnected` was followed by a reconnect
- [ ] Record the day's outcome for the acceptance tally
      (`docs/PRODUCTION_ACCEPTANCE_CRITERIA.md`)
