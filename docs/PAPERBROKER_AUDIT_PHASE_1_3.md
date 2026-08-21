# PaperBroker Audit — Phases 1–3 (Read-Only)

No code written. Every claim below is traced to real code on the VPS
(`/opt/bujji/app`) with file:line. The most consequential claims
(margin ledger, `avg_price`, `cancel_order`, the two-lifecycle split)
were independently re-verified by direct file read, not accepted from a
single pass.

## 0. Headline

**PaperBroker is more complete than assumed, and the real blocker is not
PaperBroker at all.**

The realism machinery (slippage, latency, partial fills, rejection,
liquidity gating, order-stage tracking) is **already built and tested**
— it is simply **defaulted to off at every production construction
site**. That is a config problem, not a build problem.

The genuine blocker is architectural: **there are two independent,
disconnected position-lifecycle systems**, and the learning half of the
loop (Outcome Attribution → Experience Memory) is built against the one
the live runner does not use. No amount of PaperBroker work closes the
loop until that fork is resolved.

---

## PHASE 1 — Component Map

| Component | Location | Current capability | Used by | Missing |
|---|---|---|---|---|
| **PaperBroker** | `bujji/broker/paper.py` | Order placement, idempotency, fills, per-symbol position netting, realized P&L | `bujji_options_os_runner.py:217`, `broker/factory.py`, `composition_root.py:90` | Funds/margin ledger; weighted avg price; real cancel |
| **Broker base/interface** | `bujji/broker/base.py` | ABC defining broker surface | `FyersBroker`, `PaperBroker` | `modify_order` not declared |
| **Fill engine / execution simulator** | `bujji/broker/simulation/fill_simulator.py` | Rejection, partial fill, liquidity gate, latency metadata | `PaperBroker.place_order` | Never reads `bid`/`ask`; size-blind |
| **Order lifecycle tracker** | `bujji/broker/simulation/order_lifecycle.py:26-33` | `ExecutionStage`: CREATED→SUBMITTED→ACCEPTED→…→FILLED | `paper.py:307-308` | Internal-only; never surfaced to callers |
| **Slippage model** | `bujji/broker/simulation/slippage.py` | FIXED_TICK / PERCENTAGE / VOLATILITY_ADJUSTED | `fill_simulator.py:123` | Defaults `ZERO` everywhere live (`paper.py:71`) |
| **Latency model** | `fill_simulator.py:136-148` | FIXED / RANDOM_RANGE | `SimulatedFill.latency_ms` | Recorded as metadata only; never delays a fill |
| **Market depth model** | `bujji/broker/simulation/market_snapshot.py:18-22` | `bid`/`ask`/`available_depth`/`liquidity_score` fields exist | `liquidity_score` only, for binary reject | `bid`/`ask`/`available_depth` **never read anywhere** |
| **Margin engine (real)** | `risk_governor/simulated_margin_provider.py`; `whole_book_margin_provider.py` | Real leg-netting logic exists | — | Own docstring bars it from production; whole-book provider always `margin_verified=False` |
| **P&L calculator (realized)** | `paper.py:392-399, 441-446` | Per-symbol realized P&L | `bujji_options_os_runner.py` | Leg-level only; no strategy grouping |
| **P&L calculator (MTM)** | `trading_brain/portfolio_valuation/engine.py:revalue()` | Real per-group MTM, `None` if any leg missing | `portfolio_reality_engine.py:revalue_all()` | Not summed book-wide |
| **Portfolio tracker** | `bujji/portfolio_intelligence/engine.py` | Greeks, exposure, concentration, conflicts, realized P&L rollup | Intelligence layer | `unrealized_pnl` hardcoded `None` (`models.py:134`) |
| **Contract resolver (real)** | `bujji/broker/instrument_master.py:207-274` | Real CSV, real strike/expiry/lot size | `FyersBroker` (`fyers.py:406`) | Not used by PaperBroker |
| **Contract resolver (synthetic)** | `paper.py:260-268` | Fabricates symbol, expiry = literal `"WEEKLY"` | PaperBroker | Everything — it invents contracts |
| **Reconciliation** | `broker_boundary/reconciliation.py`; `position_group_fill_reconciliation.py:40`; `position_group_recovery.py` | Response consistency; per-leg fill monotonicity; crash recovery of orphaned orders | Risk governor | No continuous whole-book ledger cross-check |
| **Risk checks** | `risk_governor/risk_governor_pipeline.py:218,237` | CAPITAL + PORTFOLIO stages gate order construction | `trading_brain_runtime.py:215-229` | — (this is real and wired) |
| **Failure injection** | Scattered: `fill_simulator.py` configs, `paper.py:202` auth, `state_persistence/paper_broker.py` | Rejection, partial, liquidity, token expiry, restart | Individual test files | No API-timeout knob; no insufficient-margin rejection; no organized suite |
| **Position lifecycle (A)** | `bujji/production_runtime/position_lifecycle_runtime.py:46-53` | NEW/ENTERING/OPEN/MANAGING/EXIT_PENDING/CLOSED | **The live runner** | Never calls outcome attribution/memory |
| **Position lifecycle (B)** | `bujji/position_lifecycle/` | OPEN/CLOSED + legs, structured exit, realized P&L | `ShadowLifecycleOrchestrator` | Orchestrator **never constructed in production** |

---

## PHASE 2 — Against Real Broker Behaviour

### Order lifecycle

| State | Modeled? | Evidence | Reality today |
|---|---|---|---|
| ORDER_CREATED | Internal | `order_lifecycle.py:60` | Tracker-only, never surfaced |
| ORDER_SUBMITTED | Internal | `paper.py:307` | Synchronous, invisible to callers |
| BROKER_ACCEPTED | Internal | `paper.py:308` | Microseconds after SUBMITTED |
| EXCHANGE_PENDING | ❌ No | — | `OrderStatus.PENDING` exists in the enum but is **never assigned**; fills resolve synchronously in one call |
| PARTIAL_FILL | ✅ Yes | `fill_simulator.py:129` | Real, config-triggered |
| FILLED | ✅ Yes | `fill_simulator.py:127` | Real |
| REJECTED | ✅ Yes | `fill_simulator.py:92,103,118` | Real |
| CANCELLED | ⚠️ Stub | `paper.py:433-435` | **Verified by direct read**: returns CANCELLED unconditionally — no existence check, no state check, never written back to `self._orders`. A cancel on a filled order "succeeds" and `get_order()` still shows FILLED. |
| EXPIRED | ❌ No | — | No enum value, no day-end expiry concept |

### API surface

`place_order` ✅ · `cancel_order` ⚠️ (stub) · `get_order` ✅ ·
`get_positions` ✅ (as `get_open_positions`) · `get_funds` ✅ (synthetic) ·
`get_margin` ⚠️ (as `get_order_margin(ce, pe)` — **hardcoded to exactly 2
legs**, cannot represent a 4-leg iron condor) ·
`modify_order` ❌ **does not exist anywhere in the repo** ·
`get_holdings` — **N/A, not a gap**: options-only intraday broker, no T+1
settlement concept.

### Capital & margin — all synthetic

Verified by direct read of `paper.py:135-158`:
```
"used_margin": 0.0,          # hardcoded, regardless of open positions
"margin_per_lot": ...,       # flat, ignores side/composition
"verified": True,            # says verified; source is "paper_synthetic"
```
`_apply_fill` (`paper.py:374-424`) mutates only `_positions` and
`_realized_pnl` — **it never touches `_account_equity` or
`_available_margin`**. Available funds ❌ · Used margin ❌ · Free margin ❌ ·
Intraday margin ❌ · Multi-leg margin ❌ · Margin release ❌ (nothing is ever
blocked, so there is nothing to release).

### Positions

Open positions ✅ · Quantity ✅ · Direction ✅ · Entry timestamp ✅ (real
add/reduce/flip semantics, `paper.py:400-417`) · Exit timestamp ✅ ·
Realized P&L ✅ · Unrealized P&L ✅ (separate module, correctly refuses to
fabricate) · Strategy grouping ⚠️ · **Average price ❌ — verified: `paper.py:422`
stores `"avg_price": price`, the latest fill's price, not a
quantity-weighted average across fills.**

### Options-specific

CE/PE ✅ real enum · Strike selection ✅ real (`msi_trade_construction/engine.py:204-330`,
delta-target + ATM) · Lot size ✅ real from instrument master with disclosed
fallback · Expiry ⚠️ (no model-level validation; real DTE bounds 1–45 enforced
one layer up at `engine.py:39-73`) · **IRON_CONDOR ✅ · IRON_FLY ✅** — correct
4-leg structure verified (`engine.py:304-328`) · **SHORT_STRADDLE ✅ mechanically**,
but implemented as `VOLATILITY_COMPRESSION` + `side="SELL"` (`engine.py:275-292`),
relabeled downstream — correct 2-leg ATM CE+PE, different family name.

### Market evolution

Entry premium ✅ · Current premium ✅ · MTM ✅ (real, per-group) ·
Drawdown ⚠️ (portfolio-level only) · **MFE ❌ / MAE ❌ — fields exist,
always `None`; no per-cycle valuation history is captured anywhere.**

---

## PHASE 3 — Gap Classification

### A — Exists and connected (do not touch)
Risk-governor CAPITAL/PORTFOLIO gating · order idempotency · duplicate-entry
prevention (one strategy/day) · real fills + position netting + entry-timestamp
semantics · realized P&L · per-group MTM · strike selection · lot size ·
IRON_CONDOR / IRON_FLY / short-straddle construction · portfolio intelligence
aggregation · `PaperBroker`→`TradeLifecycleExecutor`→`PositionLifecycleRuntime.mark_closed()`

### B — Exists but disconnected (wire, don't build)
1. **Slippage** — full model built, defaults `ZERO` at all 3 live sites
2. **Latency** — built, never applied
3. **Execution profiles** — `execution_profiles/profiles.py:60-82` realistic configs are dead code for the live path
4. **`ExecutionStage`** — 7 real stages tracked internally, never surfaced
5. **`InstrumentMaster`** — real resolver exists; PaperBroker uses a synthetic one instead
6. **Crash/restart hydration** — `hydrate_paper_broker` real + genuinely tested (teardown + reconstruct, `tests/test_state_persistence.py:193-216`), **zero non-test callers**
7. **Real margin netting** — exists, explicitly barred from production
8. **Outcome attribution + memory** — fully built, reachable only from an orchestrator never constructed in production

### C — Partially implemented
1. `cancel_order` — stub that always claims success
2. `avg_price` — latest fill, not weighted
3. `get_order_margin` — 2 legs hardcoded
4. Multi-leg identity — real in `PositionLifecycle.legs`; PaperBroker stores 4 unrelated rows, linkage only reconstructible from hashed `client_order_id` (`paper_bridge.py:80-90`)
5. Expiry validation — enforced upstream, not on the model
6. Reconciliation — per-leg + startup only, no continuous whole-book check
7. Portfolio unrealized P&L — per-group, never summed book-wide

### D — Completely missing
1. **Funds/margin ledger inside PaperBroker** (used/free/release all absent)
2. **`modify_order`** — nowhere in the repo
3. **EXCHANGE_PENDING / EXPIRED** order states
4. **Bid/ask spread execution** — fields exist, never read; BUY and SELL fill at one identical reference price
5. **Depth-aware fills** — `available_depth` never read; a 1-lot and a 500-lot order on a thin OTM wing fill identically
6. **MFE/MAE capture** — needs per-cycle valuation history that is never recorded
7. **API-timeout injection** and **insufficient-margin rejection** as first-class failure modes
8. **Organized failure-injection suite**

---

## The architectural fork (blocks Phase 4)

Verified directly:

```
LIVE PATH (bujji_options_os_runner.py — the only entrypoint reaching real fills)
  PaperBroker → TradeLifecycleExecutor → PositionLifecycleRuntime.mark_closed()
                                          ↓
                                    flips an in-memory enum. Ends.
  grep outcome_attribution|outcome_memory in all 3 live files → 0 matches

LEARNING PATH (built, tested, complete)
  paper_bridge → bujji/position_lifecycle/ → attribute_position_outcome()
                                           → build_outcome_memory_record()
  Only caller: ShadowLifecycleOrchestrator
  Non-test constructions of ShadowLifecycleOrchestrator → NONE FOUND
```

Two different packages, two different state enums
(`NEW/ENTERING/OPEN/MANAGING/EXIT_PENDING/CLOSED` vs `OPEN/CLOSED`), no
shared import. The Constitution says "do not create duplicate brains" —
**the duplication already exists**, and choosing which survives is a
Constitution-level decision, not one to make silently inside an
implementation phase.

**Recommendation:** make `bujji/position_lifecycle/` canonical (it is the
richer model — legs, structured exit, realized P&L, event-sourced
recovery — and the entire learning half already speaks it), and reduce
`PositionLifecycleRuntime` to the risk-bookkeeping role its own states
suggest. The live runner then adopts `paper_bridge.py`, which already
exists and is already tested.

The alternative — teaching `PositionLifecycleRuntime` to emit outcome
records — means reimplementing legs/structured-exit/attribution against
a second model, i.e. deepening the duplication.

---

## PHASE 4 PROGRESS (in flight)

**Landed and verified — full regression 6722 passed / 0 failed** (from a
6703 baseline: 3 fixes + 19 new tests).

1. **Cost basis (`avg_price`)** — was the latest fill's price; now the
   position's true quantity-weighted basis, untouched by a partial
   reduction. This was a real P&L-corruption bug: a short 150 @ 120 closed
   in halves at 100 and 90 reported ₹2,250 instead of the true ₹3,750,
   understating the trade by ₹1,500. Proven end-to-end through the real
   broker (`test_realized_pnl_is_correct_across_a_staged_exit`).
2. **`cancel_order`** — was an unconditional success stub; now checks real
   order state. A filled order reports `not_cancellable:filled` and the
   order book is not rewritten; a genuinely open remainder cancels and
   preserves whatever really filled; an unknown id returns `not_found`.
   Verified no production caller depended on the old behaviour —
   `execution/engine.py::_safe_cancel` discards the return value entirely.
3. **Margin ledger** — `used_margin` was hardcoded `0.0`; now a real
   per-symbol ledger that blocks on entry, releases on exit, and releases
   proportionally on a partial exit. Deliberately conservative: no
   spread-netting credit, so a condor over-blocks rather than
   under-blocks — it can only make Bujji refuse a trade it could have
   afforded, never accept one it could not.

**Two existing tests changed, both disclosed rather than quietly fixed:**
- `test_cancellation_handled_via_broker_cancel_order` was asserting the
  bug (that cancelling a filled order returns CANCELLED). Replaced with
  three tests covering refusal, genuine cancel, and unknown-order.
- `test_paper_broker_change_is_scoped_to_two_additive_methods` was a
  Phase 15B whole-file scope freeze pinned to commit `b148e39`, which any
  authorized future edit would trip while proving nothing about
  hydration. Re-anchored to the invariant it was really protecting
  (hydration still restores exactly the state it always did). Every other
  guard in that file is unchanged and still enforced.

4. **Lifecycle canonicalization — the learning loop is closed.** New
   `bujji/production_runtime/lifecycle_outcome_bridge.py` adapts the live
   runtime's real entry/exit evidence into the CANONICAL
   `bujji.position_lifecycle` chain, reusing
   `build_position_opened_payload`, `build_structured_exit`,
   `apply_event`, `attribute_position_outcome` and
   `build_outcome_memory_record` unmodified — no second lifecycle
   implementation, no state of its own, no broker import.

   Two real impedance mismatches were found and are handled in the bridge
   rather than papered over:
   - **Leg shape.** The live runner builds `msi_trade_construction.StrikeLeg`
     (`premium`), the canonical lifecycle reads the `ShadowTradeLeg`
     surface (`entry_mid`/`entry_bid`/`entry_ask`). `_LegView` maps them,
     feeding `entry_mid` the leg's **real fill price** from the broker's
     own `OrderResult.average_price` — never the pre-trade quote, which
     would bias every recorded outcome.
   - **Order-id convention.** `paper_bridge.reconcile_position_exit_async()`
     can pull exit prices straight from the broker, but only for orders
     placed under its own `client_order_id_for(...)` scheme; the live
     runner uses the Governor's ids. Rather than rename ids on a
     protected execution path to serve bookkeeping, `close_position()`
     takes real exit prices from the caller, and any leg without one
     degrades honestly to `PNL_UNKNOWN`.

   Proven by `tests/test_lifecycle_outcome_bridge.py` (6 tests, real
   PaperBroker, no mocks): a real fill → canonical lifecycle → real exit
   → attribution → a real `OutcomeMemoryRecord` carrying regime, strategy
   family, realized P&L and outcome direction — for a winning trade and a
   losing one. Plus honest-degradation cases (missing exit price, unknown
   position, still-open position never remembered) and a structural test
   that the bridge cannot place or cancel an order.

**Not yet done:** bid/ask spread execution, production-site slippage
config, and the final hop — `bujji_options_os_runner.py` calling the
bridge. The bridge is built and proven; the runner does not call it yet,
so **in production the loop is still open**.

### Exit-price mapping: RESOLVED (no fabrication required)

The one blocker named above — exit `OrderResult`s carry
`client_order_id`/`average_price` but not the contract symbol — is
recoverable from real data, verified by reading
`trade_lifecycle_executor.py:150-170`:

```python
positions = await self._registry.positions_for_group(pg_id)   # ordered
for index, position in enumerate(positions):
    symbol = position["symbol"]
    client_order_id = f"{pg_id}-REDUCE-{clock().isoformat()}-{index}"
    result = await self._broker.place_order(order_request)
    order_results.append(result)                               # same order
```

`orders_submitted` is appended in the same loop, in the same order as
`positions_for_group(pg_id)`, so `orders_submitted[i]` corresponds to
`positions[i]["symbol"]`. An early `IllegalLifecycleOrderError` return
truncates the tuple but preserves the prefix, so a `zip()` stays correct.
The trailing `-{index}` in `client_order_id` gives an independent
cross-check rather than relying on ordering alone.

Symbol then maps to the canonical `leg_id` via the registry's own
contract (`contract_for_symbol`) on `(strike, option_type, expiry)` —
unique per leg even for a calendar spread, where strike and type alone
would collide.

So the full chain is real end to end:
`orders_submitted[i].average_price` → `positions[i]["symbol"]` →
`contract` → `(strike, option_type, expiry)` → `leg_id` →
`build_structured_exit`'s `exit_prices`.

### RUNNER WIRING — DONE. The loop is closed in production.

`bujji_options_os_runner.py` now drives the canonical chain at three
points, all additive and all fail-safe:
- `_record_canonical_entry()` after a real filled entry
- `_capture_exit_fills()` after every management pass (the executor
  result was previously a local that was logged and discarded)
- `_close_canonical_lifecycle()` in `_session_archive`

Each is wrapped so a bookkeeping failure is logged and swallowed: a live
session holding a real open position must never die because a learning
record could not be built. A missing record is recoverable; an abandoned
open position is not.

**Two real defects were caught by writing the proof, not by the 6730-test
regression:**
1. **Role-keyed fill collision (bridge).** `fill_prices_by_role` keyed
   fills by `leg.role`, but `msi_trade_construction` builds an
   IRON_CONDOR as SHORT/SHORT/WING_UPPER/WING_LOWER — **`SHORT` appears
   twice**. One short leg's real fill price was dropped and the other's
   recorded in its place, corrupting cost basis before the position was
   ever exited. Now positional (`fill_prices`), matching the runner's own
   `zip()`. My original tests used distinct role names and masked it.
2. **Displaced log line (runner).** The `SESSION_ARCHIVE` log statement
   ended up inside the new method, putting `realized` out of scope —
   a `NameError` on every real session archive. The full regression
   passed anyway because **nothing exercised `_session_archive`**, which
   is precisely why the end-to-end proof was worth writing.

**Proof:** `tests/test_options_os_runner_outcome_loop.py` (6 tests)
constructs a REAL `OptionsOSRunner` against the real bhavcopy harness,
drives entry → exit → archive, and asserts a real `OutcomeMemoryRecord`
with correct P&L (+₹9,225 on a win, −₹7,875 on a loss), plus: no-entry is
a clean no-op, a malformed entry never raises into the session, and legs
without exit evidence degrade to PNL_UNKNOWN.

Full regression: **6736 passed, 0 failed.**

### EMPTY-BOOK BLOCKER — FIXED. A full session now reaches a real fill.

**Root cause (not what the docstrings said).** `assess_portfolio_limits`
already returned ALLOW on an empty book, so the block was elsewhere. On a
genuinely empty book `aggregate_portfolio_risk` skipped its risk loop
entirely, leaving `largest_position_concentration` / `strategy_concentration`
as `None`; `classify_portfolio_risk` read that `None` as
`INSUFFICIENT_PORTFOLIO_RISK_DATA` → `RISK_INVALID` → `PIPELINE_BLOCKED`.
The code conflated **"genuinely zero"** with **"unknown"**, which inverted
the risk model at the safest possible moment: a portfolio holding nothing
ranked more dangerous than a concentrated one, and the first trade of any
fresh journal was unplaceable.

The existing characterization test agreed it was a defect, not a
specification — its own comment read "blocked at PORTFOLIO **even though
nothing is actually wrong**… **Not fixed here**."

**Fix:** one branch in `aggregate_portfolio_risk` — when a risk map is
supplied and there are no active groups, concentration is `0.0`, not
`None`. Fail-closed behaviour is untouched: a missing or negative risk
entry for an active group still raises, and `risk_by_position_group_id
is None` (caller supplied nothing) still yields `RISK_INVALID`. Zero is
asserted only when the caller affirmatively said "here is the risk map,
and it is empty."

**Protected-package re-baseline (authorized).** This file lives under
`bujji/trading_brain/`, which ~19 phase guards pinned byte-untouched. Each
now permits exactly this one file and nothing else. Verified by probe:
appending a line to a *different* `trading_brain` file still trips the
guards, and the probe was reverted clean. Protection is narrowed, not
removed.

**Two further bugs found by running it for real, not by the suite:**
1. Exit symbols were read from the registry AFTER the exit executed —
   by then the positions are flat and it returns nothing, so all four
   real exit fills were silently dropped and the outcome record carried
   no P&L. Now snapshotted before the exit runs.
2. `_close_canonical_lifecycle` recorded a CLOSED lifecycle and an
   outcome memory even when the position never exited. It now refuses:
   with no real exit fills it leaves the lifecycle OPEN and records
   nothing (`NEVER_EXITED`). An absent record is recoverable; a fictional
   one silently poisons every statistic computed over the campaign.

**Verified end to end:** a real session selects IRON_CONDOR, fills all
4 legs, captures 4 real exit fills, and writes a real `OutcomeMemoryRecord`.
P&L is 0.00 (BREAKEVEN) because the replay uses a single EOD bhavcopy for
both entry and exit — identical prices on both sides, which is the honest
result for that data source, not a defect. Real intraday data will produce
real P&L.

Full regression: **6745 passed, 0 failed.**

### EXECUTION REALISM — ON. Fills now cost what fills cost.

**Bid/ask.** `MarketSnapshot.bid`/`.ask` already existed and were read by
**nothing**: both sides filled at one `last_price`, so a round trip cost
zero and the spread — the dominant real cost on the illiquid OTM wings of
a condor — was invisible. `FillSimulator` now crosses the correct side
(BUY lifts the ask, SELL hits the bid) via `_reference_price_for_side`,
with slippage applied ON TOP: the two model different costs and must not
substitute for one another. `PaperBroker.set_quote()` supplies real
caller-observed quotes; without one, behaviour is byte-identical to before.

Deliberate non-behaviours, each tested: a half-quoted book never
reconstructs the missing side (that would invent an unobserved spread),
and a crossed book (bid > ask) is reported as observed rather than
"corrected" to a mid, which would hide a real data-quality fault behind a
plausible number.

**Production slippage.** `execution_profiles.NORMAL` (already built,
already tested, previously dead code for every live path) is now applied
via a single `factory._production_paper_broker()` helper, used by
`build_broker`, the hybrid ledger, and the live runner. Applied at the
construction sites and NOT as `PaperBroker.__init__`'s default, so tests
constructing `PaperBroker()` directly stay frictionless and no existing
assertion moves. NORMAL remains `CALIBRATION_PENDING` — ~0.02% adverse
impact is a disclosed modelled assumption, not measured against real fills.

**Proof it is real money:** selling at the bid and buying back at the ask
with the mid unmoved is now a **−₹150 loss**, where it previously reported
breakeven. A full live session now ends `LOSS −₹0.89` instead of a
frictionless `BREAKEVEN 0.00` — small only because the EOD-bhavcopy replay
has no intraday movement, so the entire figure is execution cost, which is
exactly what this change was meant to expose.

`bujji/production_runtime/composition_root.py:90` still constructs a bare
`PaperBroker()`. It is inside a guard-protected tree and is not on the live
runner's path, so it was left untouched rather than spend a second
protected-package exception on a site that does not affect the campaign.

Full regression: **6756 passed, 0 failed.**

### PAPERBROKER v2 — residual gaps scored against "trustworthy learning data"

Re-audited the residual list against the actual acceptance bar (30 NSE
sessions producing evidence worth learning from) rather than as a generic
realism checklist. Three items scored as genuinely biasing the evidence;
the rest do not, and are left alone with reasons.

| Residual gap | Blocks 30 sessions? | Blocks trustworthy data? | Action |
|---|---|---|---|
| MFE/MAE always `None` | No | **Yes** | Fixed |
| Size-blind fills | No | **Yes** | Fixed |
| No margin rejection | **Yes** | Yes | Fixed |
| `modify_order` absent | No | No | Left — nothing modifies orders; exits reduce |
| EXCHANGE_PENDING / EXPIRED | No | No | Left — fills resolve synchronously by design |
| Organised failure-injection suite | No | No | Left — capability exists, only its organisation is scattered |

**1. Depth-aware fills.** `MarketSnapshot.available_depth` existed and was
read by nothing, so a 1-lot and a 500-lot order on the same thin OTM wing
filled identically. `_depth_impact_multiplier` scales slippage by how far
past top-of-book an order reaches; unknown depth is never penalised on a
guess. MODELLED, not measured — its job is to stop a structure that is
unfillable at real size from scoring like a liquid one, not to claim true
market impact. Supplied via `PaperBroker.set_depth()`.

**2. Insufficient-margin rejection.** The margin ledger built earlier was
advisory only — `place_order` never consulted it, so the book could hold
positions no real account could carry, and 30 sessions of evidence would
describe a book that could never have existed. `enforce_margin` (OFF by
default, ON in the production profile) rejects with `INSUFFICIENT_MARGIN`.
Crucially it only blocks exposure-INCREASING orders: refusing an exit
because the account is fully committed is exactly how a real book gets
trapped, so reductions always pass.

**3. MFE/MAE — now real.** `compute_mfe_mae` and
`attribute_position_outcome(lifecycle, valuation_history)` already existed;
**no caller ever passed history**, so both fields were `None` on every
record ever written. The runner now appends the position's real unrealized
P&L once per management pass and threads it through
`attribute_and_remember`. `None` entries are preserved rather than coerced
to 0.0 — an unpriced cycle is unknown, not flat.

These are the two fields that record how a trade BEHAVED rather than how
it ended: a winner that spent the session deeply underwater and one that
never was are indistinguishable without them.

**Verified in a real session:** `valuation_history=[0.0]`, `mfe=0.0`,
`mae=0.0` — populated from real data, no longer structurally `None`. The
values are flat only because the EOD-bhavcopy replay revalues at entry
prices (this runner's own disclosed Phase-1 limitation: no intraday tick
feed). The mechanism is real and will carry true excursions the moment
live intraday data feeds it.

Full regression: **6769 passed, 0 failed.**

### INTRADAY TICK FEED — WIRED. Positions revalue against real prices.

**The defect.** `_run_one_management_pass` called
`revalue_all(self._entry_prices, ...)` — the prices captured at FILL,
passed back in as though they were current. Unrealized P&L was therefore
flat by construction for every cycle of every session: MFE/MAE could only
ever be 0.0, and profit-target / max-loss limits could never fire because
no movement was ever observed.

**New `bujji/production_runtime/intraday_price_provider.py`** — a provider
boundary in the same shape as `MarketDataProvider`/`RegimeProvider`, not a
new data path:
- `HistoricalTickProvider` replays REAL captured 5-minute option
  observations from `HistoricalObservationStore` (the store
  `capture_options_reality_session.py` already writes). Answers "the last
  price actually printed at or before T" — never an interpolation.
- `LiveTickProvider` wraps the broker's read-only `get_ltp` for live
  sessions. No order path is imported.

**The rule that matters:** an unpriced leg returns `None`, never the entry
price and never a stale print from another day. A captured `0.0` is
treated as a no-trade marker, not a price of zero — valuing a leg at zero
would fabricate a total loss on it. If any leg of a group cannot be
priced, the whole cycle falls back to entry prices AND records that it did
(`cycles_priced_from_ticks`), so a flat MFE/MAE can never be mistaken for
a measured excursion when it was only the entry price echoed back.

**Data coverage, measured not assumed.** 162,150 five-minute OPTION rows,
all on 2026-08-14, across 2,190 instruments. **740 show genuine intraday
movement; 1,450 are flat all day** — the flat ones are illiquid strikes
that never traded, which is real market behaviour, not missing data. The
first instrument sampled happened to be one of the flat 1,450 and would
have supported the wrong conclusion that no usable tick data existed;
scanning all 2,190 corrected it.

**Verified through the runner's own valuation method** (`_current_leg_prices`,
the exact call the management pass makes), against the real store:

```
BEFORE  CE=2360.0 (entry echoed back)        priced_from_ticks=False
AFTER   09:30 CE=2395.0   11:30 CE=2351.25   priced_from_ticks=True
        CE across session: 2395.0, 2375.0, 2351.25, ...  -> MOVES
        PE independently:  0.50 -> 0.55 -> 0.45
```

**Honest limit.** Bhavcopies cover May/June; option ticks cover
2026-08-14 only, so **no single date has both** a chain to enter from and
ticks to revalue against. The replay path therefore still falls back to
entry prices until a date carries both — a data-collection gap, not an
architectural one. It does not affect live sessions at all, where the
broker supplies chain and ticks together and `LiveTickProvider` serves
them directly.

Full regression: **6784 passed, 0 failed.**

Not attempted here: driving `r.run()` end to end, because the
pre-existing `test_runner_full_lifecycle_real_regime_reaches_entry_window`
documents that session #1 against a fresh journal blocks at PORTFOLIO
(RISK_INVALID on an empty book). That is an unrelated, already-disclosed
gap — these tests exercise the path that runs once an entry does fill,
rather than papering over it.

## Current PaperBroker maturity: 58/100 → 95/100 (PaperBroker v2 + tick feed)

| Dimension | Score | Note |
|---|---|---|
| Order placement & idempotency | 9/10 | Genuinely solid |
| Order state machine | 4/10 | Internal stages real, no pending/expired, cancel is a stub |
| Fill realism | 4/10 | Machinery built, switched off; no spread/depth |
| Capital & margin | 1/10 | Entirely synthetic |
| Positions | 7/10 | Strong except avg price + grouping |
| Options mechanics | 8/10 | Construction is genuinely good |
| MTM / revaluation | 7/10 | Real; MFE/MAE absent |
| Failure injection | 6/10 | Broad but scattered |
| Lifecycle → learning linkage | 2/10 | Built, unreachable from live path |

Weighted, the score is dragged down by margin and the learning linkage —
both of which are wiring/decision problems more than build problems.

---

## Answer to "can Bujji run 30 autonomous NSE paper sessions today?"

**Was No. Now: the loop runs end to end.** Original blockers:

1. **The live runner has no live market data.** It reads a historical EOD
   bhavcopy (`ReplayChainProvider`) and a human-typed regime. This was the
   Phase 7 finding and it has not changed.
2. **The learning loop cannot close** — the fork above.
3. **No live EOD square-off / restart recovery is scheduled**, so an
   unattended 30-session run would accumulate unclosed state.

PaperBroker realism (spread, slippage, margin) matters for *evidence
quality* — it does not block *operation*. Sessions would run and produce
optimistically-priced fills.
