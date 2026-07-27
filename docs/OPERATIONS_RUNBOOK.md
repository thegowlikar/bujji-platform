# BUJJI — Operations Runbook

Practical, current-strategy procedures. See `ARCHITECTURE.md` for module
detail and `AUDIT_LOG.md` for the history of what's been fixed and what
remains open.

## Daily start

`start_bujji_day.sh` refreshes the FYERS access token and starts the
service. The systemd unit (`Restart=always`, `RestartSec=5`) means any crash
or manual `systemctl restart` self-heals within 5 seconds and re-runs the
full startup reconciliation (`Orchestrator.startup()` → `_recover()`).

## Never do this mid-day

- **`systemctl stop bujji` while a position is open.** `Restart=always`
  does not apply to a deliberate `stop` — the process stays down, and
  **nothing else in the system will flatten the position for you.** There is
  no exchange-side backstop (see AUDIT_LOG.md). If you must stop the
  service mid-day, manually square off the position first (or accept the
  straddle is unmonitored until you restart).
- **Run `python -m bujji.replay --config config/config.yaml ...` without
  checking the printed path-isolation message.** As of this session's fix,
  replay defaults to `data/replay/` and will NOT touch the live journal/
  session-state/database unless you pass `--use-live-paths` explicitly. If
  you ever see the `replay_using_live_paths` warning in logs and didn't mean
  to pass that flag, stop the run — it may have already read a stale live
  snapshot or written synthetic trades into the production journal.

## Crash / restart recovery — what to expect

On any restart, `_recover()` reconciles the saved session snapshot against
the broker's real open positions:

| Saved snapshot | Broker | Result |
|---|---|---|
| Valid position | Position confirmed open | Resumes management, re-arms Tick Engine |
| Valid position | Broker shows flat | Marks the day done — position already closed elsewhere |
| Missing/corrupted | Broker shows open leg(s) | Flattens **every** open leg found (orphan path) |
| Missing/corrupted | Broker flat | Clean slate for the day |

A snapshot with only ONE of the CE/PE legs present (partial corruption) is
now treated as corrupted (routes to the orphan-flatten row above), not
silently resumed — see AUDIT_LOG.md item 11.

## Token expiry

If `FYERS_APP_SECRET`/`FYERS_REFRESH_TOKEN`/`FYERS_PIN` are configured,
`FyersTokenManager` attempts one automatic refresh on an expired access
token before giving up. The refresh_token itself is only valid ~15 days
(FYERS community documentation — **not independently re-verified**); beyond
that window, the interactive login must be re-run manually. Watch
`status.auth_expired` on the dashboard for this condition.

## Reading the dashboard

- **State / Healthy banner:** top-line FSM state and overall health.
- **Risk / MTM:** current MTM, distance to stop-loss, profit target if
  configured.
- **System & Auth:** auth status, clock-drift detection, duplicate-candle
  count, candle-gap detection.
- **Tick / WebSocket & Candle Health:** WS connection state, reconnect
  count, last tick age, tick-driven MTM, last candle timestamp/age.
- **Premium VWAP Health** (renamed from "Market Data Health" this session):
  shows whether the strategy's actual live indicator — the volume-weighted
  combined-premium VWAP — is seeded/ready and its current value. Before
  09:20 it correctly shows "AWAITING ENTRY"; this is normal, not an alarm.
- **Trade History:** every closed trade, now including the actual traded
  `ce_symbol`/`pe_symbol`/`expiry`/`trade_id` (added this session) — not
  just a derived strike number.

## Replay usage

```
python -m bujji.replay --config config/config.yaml --candles data/history.csv
```

By default, isolated under `data/replay/` — safe to run repeatedly without
touching production data. Pass `--workdir <path>` to choose a different
isolated location, or `--use-live-paths` only if you specifically intend to
read/write the live process's own files (e.g. reproducing today's exact
session for a support investigation).

## Before allowing real capital (see AUDIT_LOG.md for full detail)

**LIVE CERTIFICATION REQUIRED:** place one real, minimum-size order in full
`fyers` mode (not `fyers_paper`) and confirm the response shape from
`place_order`/`get_order`/`orderbook()` matches what `broker/fyers.py`'s
`_map_order` assumes (`filledQty`, `tradedPrice`, numeric status codes,
`orderTag` echo). Paper trading — even `fyers_paper` with live market data —
cannot exercise this, because its order lifecycle runs entirely through
`PaperBroker`, never through `FyersBroker._map_order`.

## Known accepted limitations (documented, not fixed — see AUDIT_LOG.md)

- No exchange-side (GTT/bracket) stop-loss independent of the bot process.
- Instrument-master's 24h expiry-selection grace window (low-probability
  edge case on a stale symbol-master cache).
- `Position`'s contract-identity fields are convention-immutable, not
  type-enforced (frozen dataclass).
