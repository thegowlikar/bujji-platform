# Pre-Unattended-Trading Verification

Verification performed before letting the platform trade without a human
watching it. One real gap was found and fixed (restart recovery didn't
restart tick monitoring); one real minor gap was found and is documented,
not fixed, pending a decision (rate-limited candle fetches degrade silently).
Everything else is either confirmed working (with test/live evidence) or
explicitly flagged as requiring elapsed real time, not something a test can
substitute for.

---

## 1. Order execution fidelity — what paper results actually mean

**Fill model (verified by reading `PaperBroker.place_order` and
`HybridPaperBroker.place_order` directly, `bujji/broker/paper.py:123-151`,
`bujji/broker/hybrid.py:104-110`):**

- **Fill price**: in `fyers_paper` mode, every market order (the only order
  type this strategy places — see `Orchestrator._enter`/`_do_exit`, no
  limit orders anywhere) fills at the **exact live LTP** fetched from FYERS
  at the instant `place_order` is called. In plain `paper` mode, it fills at
  a synthetic random-walk price instead.
- **Fill price is not the price the strategy "saw"**: the LTP is fetched
  fresh inside `place_order`, a moment *after* the decision was made from
  the completed candle's close. In real trading this gap (decision → order
  → fill) always exists too, but real fills also cross the bid/ask spread —
  this simulation does not.
- **Slippage: none, by construction.** There is no bid/ask spread model, no
  market-impact model, and no latency-based price movement between decision
  and fill. **This means paper P&L is systematically optimistic** relative
  to what live trading would produce, especially for ATM weekly NIFTY
  options, which can have real spreads of several rupees — on a strategy
  sized in single-digit lots, that is a material, not cosmetic, difference.
- **Fees: none, at all.** No brokerage, no STT, no exchange transaction
  charges, no GST, no stamp duty, no SEBI turnover fee are modeled anywhere
  in `PaperBroker`, `HybridPaperBroker`, `TradeJournal`, or `Position.mtm()`.
  Every journaled `daily_result` is **pure price P&L**, before costs. For
  intraday option selling, round-trip costs are a real, recurring drag —
  paper results should be read as an upper bound on live results, not an
  estimate of them.
- **Partial fills**: the machinery exists and is tested
  (`PaperBroker.__init__`'s `partial_fill_qty` parameter,
  `ExecutionEngine`'s partial-fill handling, C4's tests) — but it is **never
  triggered in normal operation**. `partial_fill_qty` defaults to `None`,
  and nothing in `HybridPaperBroker`/`FyersBroker` ever sets it. Every real
  paper run fills 100% of the requested quantity, instantly, every time.
  "Partial fills supported" is accurate for the *code path*; "partial fills
  occur" is false for *actual paper trading*.
- **Idempotency (C3) does apply** to paper fills — a retried `place_order`
  with the same `client_order_id` returns the same result rather than
  double-filling, verified by existing tests.

**Bottom line**: paper mode validates the *software* (does the state
machine, recovery, journaling, and now the Tick Engine work correctly) very
well. It does **not** validate the *economics* of the strategy — no
slippage and no fees means paper P&L will be better than live P&L, by an
amount that isn't quantified anywhere in this codebase. No code change is
proposed for this; it needs to be a documented, understood limitation before
anyone reads a paper-trading report and draws conclusions about
profitability. Adding a slippage/fee model (if desired) would be a
deliberate, separate decision — not something to bolt on silently.

---

## 2. Restart recovery — verified, one real gap found and fixed

**Question asked → answer, each independently verified:**

| Question | Answer | Evidence |
|---|---|---|
| Does it reconnect? | Yes | `Orchestrator.startup()` → `ExecutionEngine.connect()`; pre-existing, re-confirmed by the new tests below still passing |
| Does it recover the existing position? | Yes | C1 (`_resume_position`), already tested; re-verified here alongside the fix |
| Does it resume monitoring P&L? | **Partially, until this pass — now yes** | See the gap below |
| Does it avoid duplicate exits? | Yes | `square_off`'s `if pos is None: return True` early-exit + C3 idempotency; newly tested explicitly for the tick/candle race this pass introduces |

**Gap found and fixed**: `_resume_position` (crash recovery) restored the
FSM state and the `Position` object correctly, but never published a
`POSITION_OPENED` event. The Tick Engine only starts its WebSocket
subscription and monitoring loop in response to that event — so a position
recovered after a restart was silently left on **candle-only** monitoring
for the rest of the day, contradicting the whole point of adding continuous
tick-based risk checking. Fixed in `Orchestrator._resume_position`
(`bujji/core/orchestrator.py`) by publishing the same event a fresh entry
does. Verified: `test_recovery_resumes_tick_monitoring_not_just_candle_monitoring`
constructs a second orchestrator + Tick Engine sharing the same broker/store
(the established crash-simulation pattern), confirms the tick feed
re-subscribes to the same contract, and confirms a tick-driven stop-loss
still fires correctly post-recovery.

**Duplicate-exit safety, explicitly re-verified for the new tick/candle
interaction**: `test_concurrent_tick_and_candle_exit_triggers_do_not_duplicate`
forces both the tick-driven and candle-driven paths to want to exit in the
same window and confirms only one exit order is ever placed and only one
journal entry is recorded — both paths converge on the same
`Orchestrator.square_off`, which is safe by construction (position becomes
`None` after the first successful exit; a second call short-circuits).

---

## 3. Risk kill switches

| Scenario | Verified behavior | How |
|---|---|---|
| **Lost WebSocket** | Tick Engine detects `feed.is_connected == False`, does not crash, does not falsely trigger an exit on missing data; the existing candle-driven risk check (untouched) remains the backstop and still catches a real breach | `test_websocket_loss_falls_back_to_candle_only_monitoring_safely` |
| **Transient REST failure** | Recovers automatically via the existing retry/backoff (C3) — no operator action needed for a single blip | `test_transient_rest_failure_during_exit_recovers_via_existing_retry` |
| **Persistent REST/network outage** | Exit reports failure honestly (`square_off` returns `False`), position is preserved (never silently marked flat), `status.healthy` flips — and a later successful retry still flattens correctly | `test_persistent_rest_connectivity_loss_preserves_position` |
| **Token expiry** | Already extensively covered (E1/E2, `tests/test_e1_e2_auth_expiry.py`) — re-confirmed unaffected by this pass (full suite still green) | Pre-existing |
| **API rate limits** | **Verified live** (not assumed): a real burst against the FYERS API produced `{'message': 'request limit reached', 'code': 429, 's': 'error'}`. Confirmed this is correctly **not** misclassified as an auth failure. **Found, not fixed**: `get_recent_candles`'s current error handling only raises on `s/code`-classified auth errors; a rate-limited `historical` response has neither, so it falls through to `data.get("candles", [])` returning an **empty list silently** — the candle is missed for that cycle rather than retried. `get_spot`/`get_ltp` are safer: their mapping code raises `KeyError` when the expected `d` list is absent, which *does* get caught and retried by `ExecutionEngine`'s existing retry/backoff. This asymmetry (`historical` silently degrades; `ltp` retries) is a real, minor gap — flagging for a decision rather than fixing without sign-off, since it touches the transport-mapping code stabilized in the previous pass. |
| **Network interruption (general)** | Covered by the existing "never let the candle loop die" behavior in `app.py`'s `_candle_loop` (pre-existing, unaffected) | Pre-existing |

---

## 4. Multi-day stability — cannot be substituted with a test

This is explicitly **not** something to fabricate or approximate. No amount
of unit testing proves a strategy's real-world day-to-day behavior; it
requires actual elapsed trading days. What's ready to support that:

- `fyers_paper` mode is the default, with the composite broker's safety
  guarantee that no real order can be transmitted (audited separately,
  `docs/PAPER_TRADING_LIVE_DATA.md`).
- Process supervision (`deploy/bujji.service`, `Restart=always`) so a crash
  doesn't end the multi-day run — combined with the restart-recovery fix
  above, both the position *and* its tick monitoring now survive a restart.
- The dashboard's "Tick / WebSocket Health" and "Market Data Health"
  sections give a human a fast way to spot-check a running session without
  reading raw logs.

**Recommended next step, not performed here**: run the system under
`deploy/bujji.service` for several real trading sessions, and review the
journal/logs afterward against the fee/slippage caveats in section 1 before
drawing any conclusion about strategy performance.

---

## Tests added this pass

`tests/test_unattended_readiness.py` (6 tests) — restart-recovery tick
continuity, no-duplicate-journal-entries on recovery, WebSocket-loss
safety, transient vs. persistent REST failure, and the tick/candle
duplicate-exit race. **144 tests passing total**, no regressions.
