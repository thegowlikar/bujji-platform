# Paper Trading Mode — Live Data, Paper Execution (Composite Broker)

**Status: implemented, and the default operating mode** (`broker.name:
fyers_paper` in `config/config.yaml`). This is an essential validation
capability, not a strategy enhancement — it lets the bot run against real
market conditions every trading day without any possibility of a real order
being placed. Full live trading (`broker.name: fyers`) remains a separate,
explicit opt-in.

## Architecture

```
                    ┌─────────────────────────────┐
                    │      HybridPaperBroker       │   broker.name: fyers_paper
                    │  (implements Broker ABC)     │
                    └──────────────┬───────────────┘
             live data methods     │      execution methods
        (get_spot, get_recent_     │  (place_order, get_order,
         candles, resolve_atm_     │   cancel_order,
         contract, get_ltp,        │   get_open_positions)
         connect)                  │
                    ▼                              ▼
         ┌─────────────────────┐        ┌───────────────────────┐
         │     FyersBroker      │        │      PaperBroker       │
         │  (execution methods  │        │   (unmodified ledger,  │
         │   NEUTERED — raise   │        │    same as broker.name │
         │   LiveExecutionDis-  │        │    = paper)             │
         │   abledError)        │        │                         │
         └──────────┬───────────┘        └───────────────────────┘
                    │
                    ▼
              Real FYERS API
        (read-only: quotes, candles,
         option LTP, instruments)
```

Nothing else changes. `HybridPaperBroker` implements the same `Broker` ABC as
every other adapter, so it plugs into the exact same
`ExecutionEngine` → `Orchestrator` → (`SignalEngine`, `MarketBrain`,
`TradeManager`, `Journal`, `EventBus`, `Dashboard`) stack, completely
unmodified. Decision Trace, VWAP Audit, thesis capture, and crash recovery
(C1–C4) all work identically to plain Paper mode — they operate on the
`Position`/`Signal`/`TradeDecision` objects, which don't know or care which
broker produced the underlying data.

## What's live vs. what's paper

| Capability | Source |
|---|---|
| Spot price (`get_spot`) | **Live FYERS** |
| Recent candles (`get_recent_candles`) | **Live FYERS** — real volume, used for genuine VWAP (see the VWAP-audit work) |
| Option premium / LTP (`get_ltp`) | **Live FYERS** |
| Contract/strike/expiry resolution (`resolve_atm_contract`) | **Live FYERS** — the symbol traded is the real, currently-listed contract |
| Order placement, fills | **Paper ledger only** — filled at the live-observed premium (see below), not a synthetic random walk |
| Order status / cancel | **Paper ledger only** |
| Open positions | **Paper ledger only** |

`HybridPaperBroker.place_order` fills a market order (no explicit
`limit_price`) at the **live** LTP fetched at order time, rather than
`PaperBroker`'s own synthetic random-walk price — so paper P&L reflects real
market prices, not a fictitious series. `PaperBroker` itself is completely
unmodified; this is done by constructing an adjusted `OrderRequest` before
delegating to it, using the `limit_price` field exactly as its existing
docstring defines it ("None => market order").

## Safety guarantee: structurally impossible to place a real order

Two independent layers, both required to fail simultaneously for a real order
to ever be placed — and even then, the second layer alone stops it:

1. **`HybridPaperBroker`'s own code never calls a live execution method.**
   Every one of `place_order`/`get_order`/`cancel_order`/`get_open_positions`
   in `bujji/broker/hybrid.py` delegates to `self._ledger` (the `PaperBroker`)
   — `self._live_data` is never referenced in any of those four method
   bodies. This is verifiable by reading the ~90-line file directly.

2. **The live broker instance's execution methods are neutered at
   construction time**, before `HybridPaperBroker` ever holds a reference to
   it (`broker/guard.py`'s `disable_live_execution`). This replaces
   `place_order`/`cancel_order`/`get_order`/`get_open_positions` — and
   `modify_order`, forward-compatibly, if it's ever added — on that *specific
   instance* with stubs that raise `LiveExecutionDisabledError` immediately,
   before any network call. This is instance-level patching, not class-level:
   a genuine full-live `FyersBroker` instance (`broker.name: fyers`) is
   completely unaffected — verified by
   `test_factory_live_fyers_mode_is_not_neutered`.

   `HybridPaperBroker.__init__` applies this guard a *second* time
   (belt-and-braces) even if a caller already did — so even a future
   refactor that forgets to pre-disable the broker before constructing the
   hybrid still can't produce an unguarded live leg.

If, hypothetically, a bug in `HybridPaperBroker` called
`self._live_data.place_order(...)` directly, it would raise
`LiveExecutionDisabledError` in-process — no HTTP request, no MCP tool
invocation, no real order, ever. This is proven by
`test_live_execution_methods_raise_immediately_if_invoked` and by the
behavioural test that runs a full entry→hold→exit cycle and asserts none of
FYERS's execution-related actions (`place_order`, `cancel_order`,
`order_history`, `positions`) ever appear in the live broker's call log.

## Trading calendar / session status — honest scope note

The requirements list asked for live trading-calendar/session-status data "if
applicable." **It is not currently applicable, and nothing was fabricated to
satisfy it:** neither the FYERS MCP tool surface available in this environment
nor the existing codebase exposes a market-holiday/session-status endpoint.
The bot's session-window logic (ORB/trading start/end/hard-exit) is driven
entirely by `config.timing` compared against `now_ist()` (see
`core/clock.py`), independent of whether the exchange is actually open that
day. On a market holiday, `fyers_paper` mode will still run its wall-clock
window logic and attempt to fetch live candles — `get_recent_candles` would
receive whatever FYERS returns for a non-trading day (likely stale/empty),
which is not a new failure mode: it's the same as any live/paper-live feed
gap already covered by the candle-dedup/gap detection (C_D1). Adding a real
calendar check remains a legitimate future improvement, not implemented here.

## Tests

`tests/test_hybrid_paper_broker.py` — proves, in order:

1. **Live data path**: spot/candles/LTP/contract-resolution genuinely flow
   through `FyersBroker`'s real method bodies (via a test double that
   overrides only the transport stub `_call`, not the methods themselves),
   including the D2 tz-aware-IST candle-timestamp fix.
2. **No live execution ever invoked**: both structurally (direct calls to the
   neutered live leg raise immediately, zero simulated network activity) and
   behaviourally (a full trading cycle's live-broker call log never contains
   an execution action).
3. **Identical behaviour to plain Paper mode**: position opens at the real
   symbol and live-observed premium, MTM is tracked, the exit flattens
   cleanly, and the journal/thesis narrative are recorded exactly as they
   would be in any other mode — because the Orchestrator genuinely cannot
   tell the difference.

Plus factory-wiring tests confirming `fyers_paper` builds a
`HybridPaperBroker`, plain `paper` is unaffected, and full-live `fyers` is
never neutered.

## What was NOT touched

Per the requirement: no strategy logic or capital-protection code was
modified. `SignalEngine`, `MarketBrain`, `TradeManager`, `ExecutionEngine`,
`Orchestrator`, `PaperBroker`, `DashboardServer`, `TradeJournal`,
`ReplayEngine`/`ReplayBroker` are all byte-for-byte unchanged. Only new files
were added (`broker/hybrid.py`, `broker/guard.py`, one new error type, factory
wiring, and this documentation).
