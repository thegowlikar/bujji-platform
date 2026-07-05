# Bujji ORB-VWAP ATM Seller — v1.0

A production-grade, fully automated intraday options-selling system for weekly
NIFTY ATM options. The strategy does not predict — it **follows**, and on every
completed 5-minute candle it re-asks: *"Would I still enter this trade if I had
no position?"* If not, it exits.

## Architecture

Three completely independent modules, coupled only through immutable dataclass
contracts in `bujji/core/models.py`:

| Module | Package | Responsibility | Never does |
|--------|---------|----------------|------------|
| 1. Signal Engine | `bujji/signal` | ORB, VWAP, breakout detection, emits `Signal` | Places orders |
| 2. Trade Manager | `bujji/trade` | Owns the position, per-candle thesis reassessment, emits `TradeDecision` | Talks to broker |
| 3. Execution Engine | `bujji/execution` + `bujji/broker` | Order placement, retry, verify, reconcile, recovery | Trading logic |

The `Orchestrator` (`bujji/core/orchestrator.py`) wires them together through a
**finite state machine** (`bujji/core/state_machine.py`):

```
WAITING -> READY -> CONFIRMED -> IN_POSITION -> EXITING -> DONE_FOR_DAY
```

Every transition is logged. There is no giant `main.py` and no nested-if
spaghetti — all branching is FSM-driven.

## Strategy rules

- **Opening Range**: 09:15–09:20. No trades before it completes.
- **Entry (bullish)**: spot closes above VWAP *and* above ORB high *and*
  breakout candle body ≥ 60% of range → **Sell ATM Put**.
- **Entry (bearish)**: mirror → **Sell ATM Call**.
- **One trade per day.** No averaging, pyramiding, martingale, or re-entry.
- **Exit** if spot loses VWAP, momentum collapses, control flips, max MTM loss
  is hit, or at 15:15 (no overnight positions). Capital preservation first.

## Broker-agnostic execution

All execution runs against the abstract `Broker` (`bujji/broker/base.py`).
Included adapters:

- `paper` — in-memory simulator (default; used for tests and dry runs).
- `fyers` — FYERS MCP/SDK adapter skeleton; wire the transport in
  `FyersBroker._call`.

Select via `broker.name` in config. Secrets come from env vars
`FYERS_APP_ID` / `FYERS_ACCESS_TOKEN` — never from YAML.

### VWAP & volume (verified against the live FYERS API)

- The FYERS **live quote** for `NSE:NIFTY50-INDEX` returns `volume: 0` and
  `atp: 0` (atp is FYERS' VWAP field) — so there is **no broker-provided index
  VWAP** to consume, and we do not use the quote's volume.
- The FYERS **historical/candle** endpoint **does** return genuine per-candle
  volume for the index. VWAP is therefore computed as a true volume-weighted
  VWAP `Σ(typical·volume)/Σ(volume)` from those candles.
- The old equal-weight approximation is now a **fallback only, disabled by
  default** (`market.vwap_equal_weight_fallback: false`). If a feed ever reports
  no volume, VWAP is flagged not-ready and the Signal Engine **refuses to
  trade** rather than risk capital on an approximated reference. The dashboard
  exposes `vwap_real` so you can see at a glance whether VWAP is genuine.

## Configuration

Everything is config-driven (`config/config.yaml`) — ORB times, trading window,
lots, max loss, strike interval, broker, paths, dashboard. No magic numbers in
code.

## Logging & journal

- Structured JSONL logs per day in `logs/` — **every** evaluation cycle and
  state transition, not just trades.
- Trade journal written to CSV **and** SQLite (`data/`) with full MFE/MAE,
  excursions, candles held, and daily result.

## Dashboard

Read-only, dependency-free (stdlib HTTP). Shows state, VWAP, ORB, open position,
live MTM, decision/reason, today's logs, trade history, and health. Auto-refresh.
Default: http://127.0.0.1:8787

## Crash recovery & capital protection (Tier 1: C1–C4)

Atomic session snapshot (`data/session_state.json`) restores FSM state, trade
count, and the **full open position** after a restart, then reconciles against
live broker positions:

- **C1** — a recovered open position is **resumed** (not abandoned); an
  unrecognized broker position is **flattened**; an already-closed one is
  finalized.
- **C2** — a wall-clock **end-of-day square-off** guarantees no overnight
  position even if the candle feed stalls or an exit fails (it retries).
- **C3** — orders are placed **at most once** per client id (query-before-place
  and verify-on-error), so a timeout can't create a duplicate order.
- **C4** — **partial fills** are handled: positions are sized off the actual
  filled quantity and exits flatten fully before reporting flat.

See [docs/TIER1_CAPITAL_PROTECTION.md](docs/TIER1_CAPITAL_PROTECTION.md) for the
full design and the deferred Tier 2 items. The candle loop never dies on an
exception and shuts down gracefully on SIGINT/SIGTERM.

### Tier 2 (frozen): capital-protection hardening complete for the paper-trading campaign

The following items from [docs/CHAOS_TESTING_PLAN.md](docs/CHAOS_TESTING_PLAN.md)
are implemented and tested (93 tests total):

- **Single-instance lock** (`core/process_lock.py`) — an OS-level `flock`
  acquired before the broker is ever touched. A second instance refuses to
  start; a crashed prior instance never blocks a real restart (the OS releases
  the lock the moment that process dies, for any reason).
- **Snapshot schema safety** (`core/position_codec.py`'s `PositionSchemaError`)
  — a corrupted or schema-mismatched `position` entry in the session snapshot
  can no longer crash recovery. It's treated as "no saved position"; broker
  reconciliation remains the ground truth and still discovers and flattens any
  real open position.
- **Auth/session-expiry detection** (`broker/errors.py`'s `AuthenticationError`)
  — an expired token or invalidated session is detected as a distinct failure
  class (not a generic retryable blip), never blindly retried, never causes a
  position to be lost, and self-clears once the broker recovers. Resolving it
  (refreshing the token, restarting) remains a human action by design — this
  is detection and fail-fast alerting only, not automated remediation.
- **Process supervision** ([deploy/bujji.service](deploy/bujji.service)) — a
  systemd unit with `Restart=always` so the process (and its C1 crash-recovery
  guarantees) actually comes back after a VPS reboot or unexpected crash. See
  [deploy/README.md](deploy/README.md).
- **Explicit Asia/Kolkata time management + in-session clock-drift detection**
  (`core/clock.py`) — every market-time comparison (`ORB`/trading window/hard
  exit, broker candle-timestamp conversion, the session's trading-day stamp) is
  now pinned to IST regardless of host timezone. A `ClockGuard` detects an
  in-session wall-clock jump and blocks *new* entries only (never an existing
  position's management, never the EOD square-off) until the next normal tick
  restores trust.
- **Duplicate/missing candle detection** (`Orchestrator.on_candle`) — a
  duplicate or stale-out-of-order candle is ignored outright before any signal
  or trade logic runs (can no longer double-count momentum/excursion); a gap
  larger than expected is logged and surfaced on `RuntimeStatus` for
  visibility. Detection only — no backfill, no trading halt.

**The platform is now frozen** for an extended paper-trading campaign. Remaining
items (log rotation, config cross-field validation, margin pre-check, and a
handful of narrower edge cases) are deferred until real paper-trading evidence
indicates which of them actually matter — not implemented speculatively.

### Paper Trading mode — live FYERS data, paper-only execution

One capability was added as the final pre-campaign step: `broker.name:
fyers_paper` runs against **real FYERS market data** (candles, quotes, option
prices, contract resolution) while every order is placed, tracked, and
exited **exclusively** through the existing `PaperBroker` ledger — structurally
impossible to place a real order in this mode (the live broker's execution
methods are neutered at construction time, not just avoided by convention).
See [docs/PAPER_TRADING_LIVE_DATA.md](docs/PAPER_TRADING_LIVE_DATA.md) for the
composite-broker architecture and safety proof, and
[docs/PAPER_TRADING_CAMPAIGN.md](docs/PAPER_TRADING_CAMPAIGN.md) for how to run
the campaign with it. This is the recommended mode for the campaign — plain
`paper` is a fully synthetic simulator and doesn't exercise real market
conditions.

## Run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                       # 12 tests
python -m bujji.app --config config/config.yaml
```

## Extending (v2-ready)

The core modules do not need to change to add: multiple indices
(BankNifty/Finnifty/Sensex) via `MarketConfig`, more brokers via
`broker/factory.py`, paper trading (already present), a backtest feed (new
`Broker` subclass replaying candles), or Telegram/voice/AI hooks off the
structured log stream.
