# Bujji Options OS Phase-1 Shadow Runner

## Purpose

This is the **first outer entrypoint** Bujji Options OS has ever had. Before
this runner existed, the entire Trading Session Governor stack
(`production_runtime/`, `trading_session_governor/`, `trading_brain/`,
`msi_trade_construction/`, `shadow_observatory/`) was fully built and tested
but had **zero automated driver** — nothing could start a session without a
human writing a one-off script.

`bujji_options_os_runner.py` orchestrates `TradingSessionGovernor` through
one shadow session and exits. It contains **no trading logic of its own** —
no strategy selection, no risk calculation, no strike selection, no exit
rules, no position sizing. Every decision-shaped action it takes is a direct
call into `TradingSessionGovernor`, which remains exactly where it already
lived.

This runner is **shadow mode only**. There is no live-broker mode, no code
path to a real broker, and no automatic startup capability anywhere in it.

## Architecture

```
                              CLI
                               │
                               ▼
                  bujji_options_os_runner.py
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
        MarketDataProvider          RegimeProvider
                 │                           │
                 └─────────────┬─────────────┘
                                ▼
                    TradingSessionGovernor
                                │
                    TradingBrainRuntime
                                │
                    PositionLifecycleRuntime
                                │
                    TradeLifecycleExecutor
                                │
                    PaperBroker + EventBus
                                │
                        Shadow Observatory
```

**`TradingSessionGovernor` is the only entry/exit/session authority.** The
runner calls exactly five of its methods, in order:
`begin_market_analysis()` → `select_and_lock_strategy()` → `attempt_entry()`
→ `evaluate_and_enforce_exit()` → `end_session()`. Nothing else in the
codebase makes an entry, exit, or session-completion decision on this
runner's behalf.

**`ShadowSessionController` (Gate F.5) is intentionally NOT used.** Its own
`run_entry_cycle()` / `run_management_cycle()` / `run_eod_reconciliation()`
each independently call `TradingBrainRuntime.process_entry_cycle()` /
`PositionLifecycleRuntime.evaluate_group()` / `TradeLifecycleExecutor.execute()`
directly — bypassing the Governor's one-strategy-per-day lock entirely, and
(if combined with the Governor's own calls) risking duplicate lifecycle
evaluations and duplicate order submissions for the same position group.
This was verified by reading `ShadowSessionController`'s actual method
bodies during the Phase-1 design audit, not assumed from its docstring.

`bujji.live_shadow_operator` / `run_live_shadow.py` are a separate,
unrelated system (using the legacy `exit_engine`, not D.4) and are not
imported anywhere in this runner.

## Running

```bash
python bujji_options_os_runner.py \
  --config config/options_os_shadow.yaml \
  --as-of-date 2026-05-25 \
  --bhavcopy-path /path/to/BhavCopy_NSE_FO_....csv \
  --trend-regime SIDEWAYS \
  --volatility-regime LOW_VOL
```

**Required inputs:**

| Flag | Required | Notes |
|---|---|---|
| `--config` | No | Defaults to `config/options_os_shadow.yaml` |
| `--as-of-date` | **Yes** | `YYYY-MM-DD` — the historical session date to replay |
| `--bhavcopy-path` | Effectively yes | Overrides `providers.market_data.bhavcopy_path`; must be set here or in config, or the runner fails closed at `PRE_MARKET_CHECK` |
| `--trend-regime` | Effectively yes | Overrides `providers.regime.trend_regime`; must be set here or in config, or the runner fails closed at `PRE_MARKET_CHECK` |
| `--volatility-regime` | Effectively yes | Overrides `providers.regime.volatility_regime`; same fail-closed rule |
| `--session-id` | No | Defaults to `OPTIONS_OS_<as-of-date>_<random-suffix>` |

**Regime requirement**: `HumanSuppliedRegimeProvider` (the only Phase-1
`RegimeProvider`) never guesses. If either `trend_regime` or
`volatility_regime` is missing at the point `PRE_MARKET_CHECK` calls
`get_regime()`, the runner raises `MissingRegimeInputError` and exits with
code `1` — the session never reaches `MARKET_SESSION`, no strategy is ever
locked, no entry is ever attempted.

**Market data requirement**: `ReplayChainProvider` (the only Phase-1
`MarketDataProvider`) needs a readable, real historical NSE F&O bhavcopy
file. A missing or unreadable path raises `MarketDataUnavailableError` at
`PRE_MARKET_CHECK` and exits with code `1`, before any session state is
touched.

## Configuration

`config/options_os_shadow.yaml` is the runner's only config file.

- **`shadow_mode: true`** — always true in Phase-1; there is no other mode.
- **`providers.market_data` / `providers.regime`** — provider selection.
  Phase-1 ships exactly one concrete implementation of each
  (`replay_chain` / `human_supplied`); this is the seam a future live feed
  or MIC classifier would plug into, without touching any strategy or risk
  code.
- **`logging.namespace: bujji-options-os-shadow`** — every log line this
  runner emits is tagged under this namespace.

This file is **NOT `config/config.yaml`** (the legacy ORB-VWAP bot's own
configuration) and has zero overlap with it. `bujji/app.py` never reads
`options_os_shadow.yaml`, and this runner never reads `config/config.yaml`.
See [docs/SYSTEM_OWNERSHIP.md](SYSTEM_OWNERSHIP.md) for the full separation.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Normal shadow session completion (`SESSION_COMPLETE` reached, regardless of whether a trade was actually taken that day) |
| `1` | Configuration/input failure — missing regime, missing/unreadable market data, missing/invalid config file |
| `2` | Unexpected runtime failure — any other exception, caught at the process boundary and logged with a full traceback |

`SHUTDOWN` always runs before the process exits, in every one of the three
cases above (via a `try`/`finally` around the lifecycle stages) — logs are
always flushed, even on failure.

## Phase-1 Limitations

1. **No live broker.**
2. **`PaperBroker` only** — no other broker implementation is constructed anywhere in this runner.
3. **No MIC/regime automation** — `HumanSuppliedRegimeProvider` holds whatever an operator explicitly typed; nothing classifies the market itself.
4. **No restart recovery** — `position_group_recovery.py`/`recovery_coordinator.py` are not imported or resurrected.
5. **No automatic resume** — every invocation begins a brand-new session from `STARTUP`; nothing reads back a prior run's state. Two separate runs against the same journal/broker each start `INITIALIZING` fresh.
6. **`ReplayChainProvider` uses a single EOD snapshot, not intraday ticks** — a bhavcopy is one point-in-time record, so `POSITION_MANAGEMENT`/`EOD_CLOSE` revalue using the same entry-time reference price. Only the `mandatory_exit_time` hard limit can meaningfully fire in Phase-1; profit-target/max-loss hard limits cannot, since no real intraday price movement is ever observed.
7. **First fresh-journal sessions may hit D.2's empty-book gap** — `classify_portfolio_risk()` returns `RISK_INVALID` for a genuinely empty book (a real, pre-existing gap documented during Gate D.6's own audit, not introduced by this runner). A session's first-ever entry attempt against a brand-new journal may be blocked at the `PORTFOLIO` stage. The runner does not seed a fabricated prior position group to work around this — doing so would fake trading history.

## Safety Boundaries

Intentionally **not** included in Phase-1:

- No systemd service.
- No auto-start capability.
- No live execution path.
- No FYERS connection.
- No hedging engine (`ADD_HEDGE` recommendations, if D.4 ever produces one, still require a caller-supplied `hedge_instruction` this runner never constructs).
- No recovery engine.

## Ownership

This runner belongs to **Bujji Options OS**, specifically the **Phase-1
shadow runtime** layer. See
[docs/SYSTEM_OWNERSHIP.md](SYSTEM_OWNERSHIP.md) for the complete system
ownership map, including the legacy ORB-VWAP system, the abandoned
"Options OS v3 Series 31–54" generation, and the shared infrastructure
(`PaperBroker`, `EventBus`, `Broker` ABC, `Side`/`OptionType`/`OrderStatus`,
`OptionContract`/`OrderRequest`/`OrderResult`) that must never be deleted or
modified as part of any cleanup to either system.
