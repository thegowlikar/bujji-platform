# Tick Engine / Health Engine — Architecture Separation

Signal generation (candle-driven, 5-minute, unchanged) is now separated from
position monitoring (tick-driven, continuous, only while `IN_POSITION`).

## What changed and what didn't

**Unchanged**: `SignalEngine`, `MarketBrain`, `TradeManager` (entry rules,
exit thesis checks, `_check_risk`'s existing candle-driven stop-loss),
`ExecutionEngine`, `Orchestrator`'s FSM/entry path, journaling, capital
protection (C1–C4). Nothing in this pass imports or calls into the Signal
Engine. The existing candle-driven risk check keeps running every candle
regardless of tick monitoring — if the WebSocket is down, that's still the
backstop, exactly as before.

**New**:
- `bujji/broker/fyers_ws.py` — `FyersTickFeed`: thin wrapper around the real
  `fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket` (verified live — see
  below). Runs on a background thread (the SDK's `connect()` is blocking);
  exposes a plain thread-safe last-price store polled from the asyncio loop.
- `bujji/tick/engine.py` — `TickEngine`: subscribes to `POSITION_OPENED`/
  `POSITION_CLOSED` on the existing `EventBus`. While a position is open,
  polls the option contract's tick price every second, computes MTM via the
  existing `Position.mtm()`, and calls the existing `Orchestrator.square_off()`
  the moment a threshold is breached — the same idempotent, capital-protected
  exit path already used for the EOD square-off, not a new exit mechanism.
- `bujji/tick/health.py` — `HealthEngine`: independent, purely observational.
  Updates `RuntimeStatus` with connection state, reconnect count, and tick
  staleness. Never calls any orchestrator mutation method (tested directly —
  `test_health_engine_never_calls_orchestrator_mutation_methods`).
- `Broker.live_tick_credentials()` — new optional capability (default `None`
  on the base class), overridden only by `FyersBroker` and delegated by
  `HybridPaperBroker`. `PaperBroker` never gets a tick feed — no fake data is
  fabricated for a broker with no real one.
- `RiskConfig.max_mtm_profit` — new, **opt-in**, defaults to `None` (disabled,
  zero behavior change). This is a genuinely new capability, not an inferred
  existing one — no profit-target concept existed in the strategy before this.
  The existing `max_mtm_loss` is reused unchanged as the tick-driven stop-loss
  threshold (same number, faster detection — not a new threshold).

"Emergency exit" is this same tick-driven stop-loss, described as urgent
because it fires on the next tick instead of the next candle — no separate
mechanism was invented for it.

## Live verification (real FYERS session, not mocked)

1. **WebSocket connects and streams real ticks**: subscribed to
   `NSE:NIFTY50-INDEX`, received a real tick (`{'ltp': 24430.35, 'symbol':
   'NSE:NIFTY50-INDEX', 'type': 'if'}`) within seconds.
2. **Full pipeline, real option contract**: resolved the real ATM contract
   (`NSE:NIFTY2670724450PE`) from a live spot, subscribed the tick feed to it,
   and received a real tick (`74.55`, matching the REST-fetched LTP exactly)
   with correct tick-age tracking.
3. **Access token format confirmed**: `f"{app_id}:{access_token}"` (verified
   against the sibling `bujji-api` project's already-working usage before
   testing, then confirmed directly).

## Tests

`tests/test_tick_engine.py` (6) and `tests/test_health_engine.py` (4), all
using a duck-typed `FakeTickFeed` — no live network dependency in the suite.
Cover: no-feed graceful no-op, stop-loss breach triggers exit via the real
`square_off()`, profit-target disabled by default / triggers only when
explicitly configured, no-tick-yet is a safe no-op, monitor task cancels
cleanly on position close, and Health Engine never calls an orchestrator
mutation method. 138 tests passing total, no regressions.

## What was intentionally left out

- **Underlying-symbol tick subscription**: only the option contract is
  subscribed. MTM/stop-loss/profit-target only ever need the option premium;
  subscribing to the underlying's tick too would be unused data with no
  consumer — avoided per the same discipline as "don't build speculative
  infrastructure" applied earlier in this project.
- **A new reconnect implementation**: the FYERS SDK's own `reconnect=True`
  already handles this (verified as a real constructor parameter); the
  Health Engine surfaces whether it's happening (via the connect counter)
  rather than re-implementing it.
