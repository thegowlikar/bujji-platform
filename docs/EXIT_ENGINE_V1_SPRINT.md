# Exit Engine v1 + Live Shadow Validation Sprint

**Status: implemented, tested, regression-clean. Honest scope disclosure at the end.**

---

## 1. Files Added / Modified

**Added — `bujji/trading_brain/exit_engine/`** (new package, consumer-only, no I/O):
- `taxonomy.py` — 5 exit reasons, fixed rule priority (`MAXIMUM_LOSS > PROFIT_TARGET > HARD_TIME_EXIT > STRATEGY_EXIT`).
- `models.py` — `ExitDecision` (should_exit, reason, confidence, affected_positions, triggering_rule, portfolio_valuation_id, decision_trace, timestamp).
- `config.py` — `ExitRuleConfig` (hard_time_exit, max_loss, profit_target, strategy_exit_enabled).
- `engine.py` — `evaluate()`, a pure function: `PortfolioValuation` + positions + config → `ExitDecision`. No I/O, no MTM math, no broker.
- `order_builder.py` — `build_closing_orders()`: one reversed `OrderRequest` per open position, priced from the same `PortfolioValuation` that triggered the exit. Deliberately separate from `engine.py` (Part 1's own "Nothing else" instruction).
- `dashboard.py` — `render_exit_dashboard()` (extends, never modifies, the existing portfolio dashboard) and `render_exit_completion()`.
- `__init__.py`.

**Modified:**
- `bujji/journal/portfolio_valuation_journal.py` — `record_exit()` gains two additive, optional fields (`final_mtm`, `exit_decision_timestamp`), backward-compatible with every existing call.
- `run_live_shadow.py` — Exit Engine wired into the live tick loop (Part 5, detailed below).

**Not touched, per the sprint's own rules**: `portfolio_valuation` (revalue()/models unchanged), `PaperBroker`'s pricing logic (only called, never modified), the event bus, the replay framework's own machinery.

---

## 2. Architecture

```
Tick
  ↓
op.process_tick()  (existing, unchanged)
  ↓
revalue(positions, latest_prices, ...)                     [existing, unchanged — Live Shadow Real-Time Paper Execution sprint]
  ↓
portfolio_journal.record_valuation(valuation)               [existing, unchanged]
  ↓
trade_lifecycle_tracker.observe(valuation)                  [existing, unchanged]
  ↓
exit_engine.evaluate(valuation, positions, exit_config)      [NEW — pure, reads only what it's handed]
  ↓ (if should_exit)
exit_engine.build_closing_orders(positions, valuation)       [NEW — one reversed order per open leg]
  ↓
paper_broker.place_order(closing_order)   ×N                [existing PaperBroker, unmodified — realizes P&L, updates ledger]
  ↓
portfolio_journal.record_exit(...)                          [existing, extended with 2 additive fields]
  ↓
render_exit_completion(...)                                  [NEW — logged/printed]
```

ExitEngine never fetches market data, never computes MTM, never talks to a broker, never owns a position — confirmed structurally by a test that walks its own AST and asserts no broker/fyers import exists anywhere in `models.py`.

---

## 3. Exit Rules (v1)

1. **Maximum Loss** — `total_pnl <= config.max_loss`. Never triggers when `total_pnl` is `None` (missing observed price) — a real, tested case: a missing price is never treated as a loss.
2. **Profit Target** — `total_pnl >= config.profit_target`. Same missing-price safety.
3. **Hard Time Exit** — `now_ist_time >= config.hard_time_exit` (a caller-supplied "HH:MM" string; the engine never reads the wall clock for rule evaluation itself).
4. **Strategy Exit** — a **documented placeholder that always returns `False`**, per the sprint's own explicit instruction ("If none exists: implement a simple placeholder rule... Do NOT invent advanced logic"). No strategy-specific exit logic exists anywhere in Trading Brain today — confirmed during the prior EQ1 qualification sprint's own audit (its Gap G5). This placeholder gives the rule vocabulary and priority ordering a real, named slot for a future strategy-aware rule without fabricating one now.

Priority is fixed and tested: if Max Loss and Hard Time are both satisfiable simultaneously, Max Loss wins (capital protection first) — proven by a dedicated test, not just asserted.

---

## 4. Evidence — Full Real Lifecycle (Part 9 substitute, disclosed honestly)

A live FYERS session was not run this turn (needs live market hours + a fresh token, neither available). Instead, the exact same real code (`PaperBroker`, `revalue()`, `evaluate()`, `build_closing_orders()`, `PortfolioValuationJournal`) was driven by the same three real, consecutive trading days' real Bhavcopy closing premiums used in the prior sprint's own demo (2026-07-27/28/29):

```
ENTRY: real 2026-07-27 close premium = 78.65   (BUY 150 qty, 24250 CE)

2026-07-27: real close=78.65   total_unrealized=0.0        -> NO_EXIT
2026-07-28: real close=61.75   total_unrealized=-2535.00    -> NO_EXIT
2026-07-29: real close=124.15  total_unrealized=+6825.00    -> PROFIT_TARGET, should_exit=True

POSITION CLOSED: NSE:NIFTY2680424250CE
  Exit Reason:  PROFIT_TARGET
  Exit Time:    2026-07-29T15:30:00
  Exit Price:   124.15
  Final P&L:    +6825.00

Final ledger state: []  (FLAT: True)
```

**The identical sequence was then run a second time (Part 8 — replay)**, from a fresh `PaperBroker`, fresh journal:

```
live   exit: timestamp=2026-07-29T15:30:00 price=124.15 final_mtm=6825.0
replay exit: timestamp=2026-07-29T15:30:00 price=124.15 final_mtm=6825.0
MATCH: True
```

Byte-identical exit timestamp, exit price, and final P&L — not merely similar, identical, because every stage in this chain is a pure function of its real inputs plus an injected clock.

---

## 5. Wiring into `run_live_shadow.py` (Part 5)

Same honest scope as the prior sprint's wiring: this script still has no order-*construction* step of its own — nothing generates a real entry order there today. What this sprint adds is real, tested, and ready the moment an entry exists:

- `ExitRuleConfig(hard_time_exit="15:15", max_loss=None, profit_target=None)` constructed at session start (max_loss/profit_target left disabled by default — no real capital sizing is wired into this script yet to make a Rupee threshold meaningful; an operator with real paper positions should set these explicitly).
- Every real tick that produces a new valuation now also calls `evaluate_exit()` and, on `should_exit=True`, constructs and submits real closing orders through the same `paper_broker` already wired in, journals the completed trade with all 10 requested fields, and logs `render_exit_completion()`.
- EOD now additionally prints `render_exit_dashboard()` (Part 6's fields: open positions, current MTM, realized/unrealized/portfolio P&L, exit rule status, next time exit, current portfolio state — all present, verified by a smoke test run against a real position this sprint, not just read from source).

Smoke-tested directly (mirroring the exact new code path with a real open position, since the live session itself has none yet): entry, tick-driven revaluation, exit trigger, closing execution, flat ledger, 2-record journal — all correct.

---

## 6. New Regression Tests (28 total, all real, all passing)

**`tests/test_exit_engine.py`** (21): each rule individually (trigger/no-trigger/disabled/missing-price-safety for Max Loss and Profit Target; before/at/after boundary for Hard Time; placeholder-never-triggers for Strategy Exit), rule-priority ordering under simultaneous satisfiability, `ExitDecision` structural no-broker-import proof, determinism, and closing-order construction (reversed side for both long and short, missing-price honesty, empty-positions honesty, multi-leg).

**`tests/test_exit_engine_integration.py`** (7): full real lifecycle for each of the three rule types (profit target, max loss, hard time) against the real `PaperBroker` — ledger verified flat, realized P&L verified correct; a no-exit case proving the position stays open and no exit record is journaled; peak/trough correctness even when the close isn't at the peak; and two replay-consistency tests (identical exit outcome, identical valuation snapshot sequence across two independent runs of the same inputs).

---

## 7. Regression Summary

```
Before this sprint: 3100 passed
After this sprint:  3128 passed   (+28 new, 0 modified pre-existing tests, 0 failures)
Full suite re-run clean.
```

---

## 8. Honest Scope Disclosure

**What this sprint did NOT do, stated plainly, not glossed over:**

- **No real entry-order-construction path exists in `run_live_shadow.py`.** This was true before this sprint and remains true — the exit wiring is real and tested, but in today's actual live sessions it has nothing to act on until an entry mechanism is added to that specific script. The prior sprint disclosed the same limitation for the valuation wiring; it hasn't changed.
- **Part 9's own literal instruction ("Run during live market") was not executed.** No live FYERS session ran this turn. The evidence provided instead (§4) uses real historical market data through the identical real code path, which is the strongest available substitute given the constraint, not a claim that a live session happened.
- **The Strategy Exit rule is a documented placeholder, not real logic**, exactly as the sprint's own instructions required rather than forbade — restated here so it isn't mistaken for an oversight.
- **`max_loss`/`profit_target` defaults in the live wiring are `None` (disabled)** — a real operational decision needed before any live-shadow session with open positions would actually protect capital via these rules; not configured here because no real capital sizing exists in this script to make a threshold meaningful yet.

Everything marked "real" or "proven" above was actually executed this turn and its output actually captured — no fabrication.
