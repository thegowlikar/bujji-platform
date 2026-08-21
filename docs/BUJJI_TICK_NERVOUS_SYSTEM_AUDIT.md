# Bujji Tick-by-Tick Nervous System Integration Audit

Read-only. No code modified. Every claim below is a traced caller→callee
path on the real VPS code, with line numbers. Where a class exists but is
never instantiated, that is stated as the finding — not glossed as
"available".

---

## HEADLINE

**Bujji has a real, battle-tested tick nervous system. It is wired into
the wrong organism.**

`FyersTickFeed` + `TickSilenceWatchdog` (reconnect, silence detection)
are real and consumed by `bujji/tick/engine.py`, which is driven by
`bujji/app.py` — **the deprecated legacy ORB-VWAP entrypoint**
(`deploy/bujji-orb-vwap-legacy.service`) — and by the separate
`run_live_shadow.py` system.

The canonical options trading runner (`bujji_options_os_runner.py`)
consumes **none of it**. Its 16 "tick" references are its own docstring,
comments, and the two counters I added; there is no tick loop, no
subscription, no stream.

And the provider built last session to close this gap is **dead in the
runtime**:

```
bujji_options_os_runner.py:183   self._price_provider = None      ← set once
bujji_options_os_runner.py:511   if self._price_provider is None  ← read
bujji_options_os_runner.py:514   ticked = self._price_provider... ← read
```

**Three references. One assignment. That assignment is `None`.** Nothing
in the runner ever constructs a `HistoricalTickProvider` or
`LiveTickProvider`, so `_current_leg_prices()` takes its fallback branch
on every call, every session, and revaluation silently uses entry prices.

---

## 1. HISTORICAL TICK PROVIDER TRACE

**Defined:** `bujji/production_runtime/intraday_price_provider.py`.

**Interface:** `get_prices(contracts_by_symbol, as_of) -> {symbol: price|None}`.

**What it actually provides — one field:**

| Field | Provided? | Evidence |
|---|---|---|
| Option last price | ✅ | `_PRICE_KEYS = ("ltp","close","last_price")`, line 54 |
| Underlying ticks | ❌ | option identities only; spot is read separately by `StoreChainProvider` |
| Bid / Ask | ❌ | present in the stored payload, **not returned** |
| Volume | ❌ | stored, not returned |
| Open Interest | ❌ | stored, not returned |
| IV | ❌ | not stored per-tick, not returned |
| Greeks | ❌ | computed elsewhere from chain snapshots, not per-tick |
| Spread | ❌ | derivable from stored bid/ask; not surfaced |

**Timestamp resolution:** 5-minute observation cadence. Semantics are
"last real print at or before T" — a **point-in-time lookup**, never an
interpolation.

**Verdict:**
- (A) real tick stream abstraction? **No.**
- (B) historical data loader behind a provider interface? **Yes — this is
  what it is.** It is *pull*-based (caller asks "price at T"), not
  *push*-based (stream delivers ticks). Nothing subscribes; nothing is
  driven by arrival of data.
- (C) connected to the runtime? **Reachable but never activated** — see
  Headline. Category B.

**Naming risk worth stating plainly:** calling this a "tick provider"
overstates it. It is a *5-minute historical price lookup*. Treating it as
a tick feed in planning would import an assumption the code does not
support.

---

## 2. FINAL RUNTIME FLOW — WHERE IT BREAKS

```
Data Provider ──► Market Observation ──► Intelligence ──► Thesis
      │                                                      │
      │  ✗ BREAK 1                                  ✗ BREAK 2│
      ▼                                                      ▼
ReplayChainProvider (bhavcopy, EOD)          HumanSuppliedRegimeProvider
      │                                              (a human types it)
      └──────────────────┬───────────────────────────────────┘
                         ▼
        TradingSessionGovernor ──► risk ──► construct ──► PaperBroker
                         │                                     │
                         ▼                                     ▼
              Position Lifecycle ──► Outcome Memory ──► [no read-back]
                         ▲
                  ✗ BREAK 3: revalued TWICE per session, from entry prices
```

**Does each tick reach this pipeline? No. No tick reaches it at all.**

Three exact break points:

1. **Line 309** — `ReplayChainProvider(bhavcopy_path=...)`. A single EOD
   snapshot. Not live, not intraday.
2. **Line 312** — `HumanSuppliedRegimeProvider(...)`. Market understanding
   does not reach the decision; a person does.
3. **Line 183 + cadence** — the tick provider is `None`, and the
   management pass is invoked **exactly twice per session**:
   ```
   :567  _run_one_management_pass("POSITION_MANAGEMENT")
   :572  _run_one_management_pass("EOD_CLOSE")
   ```
   No loop. No interval timer. No market-hours bound.

Intelligence → Thesis → Decision is fully built and tested, but it is
running in a *different process* (the observation timers), not in this
one.

---

## 3. POSITION REVALUATION AUDIT

Per tick: **nothing happens — there are no ticks.**

Per management pass (twice a session), tracing `_run_one_management_pass`
at line 442:

| On each pass | Status | Evidence |
|---|---|---|
| Option price update | 🟡 exists, inert | `_current_leg_prices()` :511 — provider is `None`, returns entry prices |
| Unrealized P&L | ✅ real | `revalue_all(...)` :455 (per position-group, refuses partial groups) |
| Greeks update | ❌ | no per-cycle Greeks recomputation in this path |
| Volatility update | ❌ | VSB exists; not called by this runner |
| Risk update | 🟡 | hard limits evaluated at exit policy; no intraday margin re-check |
| Thesis validity check | 🟡 | D.4 evaluation runs; its inputs are the frozen entry-time prices |

`Tick → valuation → management assessment` **does not exist.** What
exists is `stage → valuation → management assessment`, fired twice.

---

## 4. DYNAMIC TRADE MANAGEMENT

Traced against `trade_lifecycle_executor.py`'s real dispatch (:115–:118)
and the governor's call sites:

| Capability | Status | Fact |
|---|---|---|
| EOD exit | ✅ wired | `MANDATORY_EXIT` dispatches real orders; enforced by governor |
| Early exit / stop loss | 🟡 disconnected | `max_loss_fraction` real in `exit_policy`; cannot fire because prices never move between the two passes |
| Profit booking | 🟡 disconnected | `profit_target_fraction` real; same cause |
| Adjustment | 🟡 partial | only `REDUCE_SIZE` reaches an order (:115) |
| Hedge | 🟡 disconnected | `ACTION_ADD_HEDGE` has a real executor branch (:118); the governor never invokes it |
| Roll | ❌ missing | `EXECUTION_INTENT_ROLL` exists only as vocabulary in `trading_brain/ontology/taxonomy.py:140`; **no executable ROLL action, no dispatch branch** |

The critical nuance: stop-loss and profit-target are **not missing** —
they are real, configured, and evaluated. They are unreachable because
the price they evaluate against never changes during the session. Wiring
the provider + cadence makes them live without touching risk code.

---

## 5. MFE / MAE VERIFICATION

Traced end to end:

```
:462  self._valuation_history.append(valuation.total_unrealized_pnl)   ← 1 per pass = max 2
:634  attribute_and_remember(..., valuation_history=tuple(...))
      → attribute_position_outcome(lifecycle, valuation_history)
      → compute_mfe_mae(...)  → OutcomeMemoryRecord.mfe / .mae
```

The plumbing is **real and complete** — genuinely fixed last session, no
longer structurally `None`.

But the values today are:
- **not synthetic** (never fabricated), and
- **not from tick movement** — sampled twice, from entry prices, because
  the provider is `None`.

Classification: **batch-calculated from 2 samples of a frozen price.**
Technically honest, analytically worthless. A trade that went deeply
underwater and recovered is indistinguishable from one that never moved —
which is precisely the distinction MFE/MAE exist to preserve.

---

## 6. DATA STORAGE / LEARNING

**Already captured (strong):** aggregated 5-min candles and option
observations (162k rows/session, ltp+bid+ask+volume+OI); market snapshots;
intelligence cycles; `DecisionArtifact` incl. **rejected strategies and
reasons**; event-sourced position lifecycle; structured exits; outcome
attribution; `OutcomeMemoryRecord`.

**Missing:**
- **Raw ticks** — never persisted by the canonical path (the legacy tick
  engine has them; its lineage is deprecated)
- **Per-cycle position state series** — only 2 valuation points/session
- **Per-cycle Greeks/IV series** for an open position
- **Mistake classification** — no vocabulary anywhere
- **Expected-vs-actual** comparison
- **Rejected-reason → outcome join** — both halves stored, never linked

**The asymmetry that matters:** Bujji stores the *market* richly and the
*position's journey* barely at all. Learning about market conditions is
well-supplied; learning about management quality is not.

---

## 7. AUTONOMOUS DAY ZERO READINESS

**Can Bujji replay 2026-08-14 and behave like an autonomous trader?**

| Moment | Verdict |
|---|---|
| 09:20 observe | ⚠️ chain available (`StoreChainProvider`, proven, 2,162 rows @ 09:20) — but runner constructs `ReplayChainProvider` |
| 09:20 decide | ⚠️ decision engine real — but regime comes from a human, not from thesis |
| 09:20 enter | ✅ genuinely works — real fill, real risk gate, real construction |
| receive every tick | ❌ no tick loop exists in this runtime |
| update understanding | ❌ intelligence not called by this runner |
| manage position | ❌ two passes on frozen prices |
| exit | ✅ mandatory EOD exit real and proven |
| outcome | ✅ real `OutcomeMemoryRecord` |
| learn | ❌ write-only by explicit design |

**CLASSIFICATION: (B) Needs minimal tick wiring first.**

Not (C). Nothing architectural is missing for a 5-minute observation
loop: providers, evidence recorder, thesis, regime translator, executor
branches, outcome chain all exist and are tested. Not (A): three
assignments and a loop stand between here and a coherent replay.

---

## RISK ASSESSMENT

| Risk | Severity | Note |
|---|---|---|
| **Silent fallback masks inertness** | **High** | `_price_provider = None` degrades quietly to entry prices; only `cycles_priced_from_ticks` reveals it. A session can look successful and be blind. |
| MFE/MAE read as measured | **High** | Values are real-shaped and analytically empty; a campaign could accumulate 30 sessions of meaningless excursion data believing it meaningful |
| "Tick provider" naming | Medium | 5-min lookup, not a tick stream — plans built on the name will overshoot |
| Stop/target appear implemented | Medium | Real and configured, unreachable — an operator may believe protection is active |
| Two competing tick lineages | Medium | Real feed lives in the deprecated ORB-VWAP organism; risk of reviving dead code instead of reusing the feed deliberately |
| ~950MB per chain load | Low | Disclosed; one load/session is fine |

---

## EXACT NEXT IMPLEMENTATION STEPS (minimum changes only)

**No new modules. No new intelligence. Four changes, all in the runner.**

1. **Assign the provider** (~5 lines, config-selected): construct
   `HistoricalTickProvider(store)` for replay / `LiveTickProvider(broker)`
   for live, into `self._price_provider`. Immediately activates the
   already-tested path at :511.
2. **Loop the management pass** (~15 lines): replace the two fixed calls
   (:567, :572) with a market-hours-bounded loop at the capture cadence
   (5 min), preserving the existing EOD call as the terminal pass. This
   alone converts 2 valuation points into ~75 and makes stop/target/
   thesis-invalidation reachable.
3. **Swap the regime source** (~3 lines): `MarketThesisRegimeProvider` in
   place of `HumanSuppliedRegimeProvider` at :312 — removes the human
   from the loop; built and tested last session.
4. **Swap the chain source** (~3 lines): `StoreChainProvider` at :309 for
   the replay case — entry book and revaluation then come from one
   captured day.

**Explicitly deferred (do not bundle):** live FYERS chain adapter;
per-tick (sub-5-min) streaming via `FyersTickFeed`; Greeks/IV per cycle;
hedge invocation; ROLL execution; outcome read-back. Each is a separate,
reviewable step — and none is required for a coherent Day Zero replay.

---

## AUTONOMOUS DAY ZERO READINESS SCORE

| Dimension | Score |
|---|---|
| Tick capture infrastructure exists | 85 |
| Tick infrastructure wired to canonical runtime | **5** |
| Per-tick market state update | 0 |
| Per-tick position revaluation | 0 |
| Management triggers reachable | 25 |
| MFE/MAE fidelity | 20 |
| Outcome capture completeness | 80 |
| Learning read-back | 0 |
| **Autonomous Day Zero Readiness** | **30 / 100** |

30 is not a verdict on the codebase — it is the distance between organs
that work and an organism that runs. Four edits in one file move it
materially; none of them is intelligence work.
