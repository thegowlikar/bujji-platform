# Phase 15Q -- Market Timeseries Store (5-min candles + technical analysis)

## 1. Forensic audit

| Question | Finding |
|---|---|
| Is real tick data available? | **YES.** `bujji/broker/fyers_ws.py` implements `FyersTickFeed`, a real websocket feed subscribing with `data_type="SymbolUpdate"`, already consumed by `live_observation/runner.py` and `live_shadow_operator/operator.py`. |
| Does tick aggregation already exist? | **YES, and it is correct.** `bujji/live_observation/` provides `AggregationWindow`, `Tick`, `LateTick`, `new_window`, `add_tick`, `close_window` -- including `INTERVAL_FIVE_MINUTE` already in its taxonomy, out-of-order tick preservation (late ticks are recorded, never dropped), and closed-window immutability. |
| Does it fabricate candles from empty windows? | **NO** -- `close_window` returns `(closed, None)` for a zero-tick window. This already matched the project's UNKNOWN discipline exactly and was reused unchanged. |
| Is there durable candle storage? | **NO.** `live_observation/journal.py` is an append-only JSONL *audit trail*, not a queryable timeseries. No OHLC table, no time-range index. **This was the real gap.** |
| Is SQLite an acceptable choice here? | **YES, with precedent.** `bujji/journal/journal.py`, `bujji/journal/position_group_journal.py` and `bujji/mil_next/snapshot_journal.py` already use SQLite in WAL mode. No new dependency was introduced. |
| Is there a live/forming candle accessor? | **NO.** `live_observation/query.py` exposes events/state, never the in-progress window. |
| Is there any technical analysis? | **NO.** None anywhere in the codebase. |
| Is the tick path connected to `ShadowSessionRunner`? | **NO.** The runner uses 30s REST polling and never touches the websocket. |

**Conclusion: the recurring Bujji pattern again -- ~60% of the hard part (tick handling, late-tick correctness, window bounds, replay safety) already existed and was correct.** The gap was storage, window→OHLC folding, the live bar, and analysis.

## 2. Design decisions (both explicitly chosen, not defaulted)

**Data source: websocket ticks, giving true OHLC.** The alternative -- folding the existing 30s REST snapshots into 5-min bars -- yields 10 samples per candle, so `high`/`low` would be the max/min of 10 point observations rather than real extremes. Wicks would be systematically understated. That is acceptable for close-based indicators (SMA/EMA/RSI) and actively misleading for range-based ones (ATR, Bollinger, breakouts, pivots). Since the stated purpose is technical analysis, approximate extremes were rejected.

**Scope: spot + India VIX + ATM ±3 strikes in CE and PE = 16 live series.** Bujji *observes* a ±500 chain but *constructs* near the money; streaming the full chain would multiply websocket load and storage for series no strategy trades. The band is a parameter (`strikes_each_side`), not a constant.

## 3. What was built -- `bujji/market_timeseries/`

- **`models.py`** -- `Candle` (immutable settled bar: OHLC, `volume`, `tick_count`, `open_interest`, `kind`) and `FormingCandle` (the live bar).
- **`store.py`** -- SQLite (WAL, `synchronous=FULL`), primary key `(instrument, interval, window_start)`, two indices for range queries. Idempotent on identical rewrite; raises `ConflictingCandleError` on contradicting content.
- **`aggregator.py`** -- multi-instrument tick router with clock-aligned window rollover, folding closed windows into `Candle`s and exposing the forming bar. Reuses `live_observation` primitives directly.
- **`subscription.py`** -- deterministic symbol-set builder (spot/VIX/strike band). Builds lists only; never opens a socket.
- **`indicators.py`** -- SMA, EMA, RSI (Wilder), ATR (Wilder), Bollinger, realised volatility, plus `series_is_contiguous`.

## 4. The three epistemic rules enforced

**A zero-tick window produces no candle.** Not a flat bar carried from the previous close. A gap in the series is real information -- the market genuinely produced nothing for that instrument in that window -- and stays visible. Nothing forward-fills, anywhere.

**Insufficient history returns `None`, never a best-effort number.** A 14-period RSI computed from 6 bars is not a weak RSI; it is not an RSI. Every indicator returns `None` rather than seeding from a partial window.

**`tick_count` is stored on every candle.** A 2-tick candle and a 400-tick candle are not the same evidence, and the store refuses to hide the difference from whatever reasons over it later.

Two further deliberate choices:
- **`volume` stays `None` when the feed reported none** -- "no volume reported" and "zero volume traded" are different facts.
- **`FormingCandle` is a separate type whose latest price is named `last`, not `close`.** This is a structural guard against the single most common live-vs-replay divergence bug: an indicator computed on a partial bar live but a completed bar in replay. A type error is better than a silent wrong number.

**Window boundaries are clock-aligned** (09:15:00, 09:20:00, …), not anchored to the first tick seen -- so a mid-session restart reproduces identical windows, and two instruments that begin streaming at different moments still share a common time axis. Proven by `test_instruments_share_a_common_time_axis_despite_different_start_times`.

## 5. Verification

**Live smoke test:** 360 synthetic ticks over 3 hours → **36 correctly-bounded 5-min candles** (180 min ÷ 5 = 36 ✓), 10 ticks each, first bar `09:15:00 O=24495 H=24500 L=24492 C=24498`, series contiguous, all six indicators computing, `RSI/ATR` on 3 bars returning `None`, forming candle showing `O=24500 H=24530 L=24490 last=24490 ticks=3 closed=False`, and a 16-symbol subscription set.

**42 new tests, all passing:**
- **33 functional** (`test_market_timeseries.py`) -- clock-aligned bounds, exact OHLC, rollover emission, single-tick candles, volume summing vs `None`, **empty window → no candle**, **gap not forward-filled and detectable**, multi-instrument isolation, shared time axis, forming-candle correctness and type distinctness, store idempotency/conflict-rejection/restart, half-open range queries, `recent()` never padding, subscription determinism, and indicator epistemics (insufficient history, exact SMA, RSI bounds, ATR positivity, Bollinger ordering, invalid-period rejection).
- **9 safety** (`test_market_timeseries_safety.py`) -- no forbidden imports (execution/risk/broker/strategy-selection/lifecycle/outcome-memory), no order calls, **never opens a market-data socket itself**, indicators never fabricate, zero-tick windows never become candles, history never silently overwritten, forming candle cannot masquerade as settled, no execution/risk packages touched, and a guard that `live_observation` is **reused rather than forked** (preventing a second divergent aggregation implementation).

**Full regression: 5094 passed, 0 failed** (from 5052).

### Bugs found
Three, all in my own test/safety code, none in the production modules:
1. A test helper discarded candles emitted on rollover, keeping only the final flush — masked as five indicator failures.
2. A contiguity test passed for the wrong reason (a 1-bar series is trivially contiguous); a length guard was added.
3. Two safety-test false positives: `sqlite3.connect()` (a local file handle, not a socket) and `FyersTickFeed` appearing only in a *docstring*. Both were narrowed to AST-based identifier checks that verify the real property precisely rather than by substring.

## 6. Storage cost

~16 series × 75 five-minute bars/day ≈ **1,200 rows/day**, ~300K rows/year. Trivial for SQLite, and roughly **four orders of magnitude smaller** than the existing `intelligence_cycle.jsonl` (45 MB per 478 cycles).

## 7. Verdict

**`MARKET_TIMESERIES_TRUST: TRUSTED_WITH_LIMITATIONS`**

Trusted: OHLC arithmetic is exact and derived from real ticks; window boundaries are deterministic and restart-stable; gaps and thin candles are preserved as information rather than smoothed away; indicators refuse to fabricate on short history; history is immutable; the forming bar cannot be mistaken for settled history; and the package cannot reach a broker, an order path, or a socket.

Limitations, stated plainly:
- **Not yet fed by real ticks.** Every test uses synthetic tick sequences through the real aggregation code. `FyersTickFeed` is not wired to `CandleAggregator` — that wiring is the next step and needs a live market session to validate.
- **No historical backfill.** The store starts empty; it only accumulates forward from the first live session. Indicators needing 20+ bars will correctly return `None` for roughly the first 100 minutes of the first session.
- **Expiry code is caller-supplied.** `subscription.py` has no calendar and deliberately refuses to guess one; a wrong code would silently stream the wrong contract.
- **Strike band is static per subscription call.** If spot moves far intraday, the band does not re-centre automatically — re-subscription is the caller's decision.

## 8. Next step

**Wire `FyersTickFeed` → `CandleAggregator` → `CandleStore` inside the shadow runtime**, and run one live session to populate real history. That is the single remaining step before technical analysis operates on genuine market data rather than proven-correct plumbing.

This also directly addresses the cadence finding from the earlier analysis: with candles in place, structural re-analysis can run on the 5-minute bar close (matching the data's real granularity) while position monitoring stays on the faster loop — instead of both being welded to a single 30-second poll.

Working tree remains uncommitted.
