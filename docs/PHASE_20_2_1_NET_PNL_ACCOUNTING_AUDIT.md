# Phase 20.2.1 — Net P&L Accounting Correction Audit

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Scope: fix the net P&L accounting correctness gap Phase 20.2 found in the Phase 15K/15L pipeline. No redesign, no new P&L system, no strategy engine.**

---

## 1. Existing behavior (before this phase)

`shadow_lifecycle.orchestrator.close_position()` places entry orders at `client_order_id` sequence 1 and exit orders at sequence 2 (its own comment: *"sequence 2 so the bridge can distinguish them from the entry fills"*). It then called `paper_bridge.reconcile_position_exit_async(broker, position_id, legs)` with no sequence bound.

`observe_leg_fills_async` scanned sequence `1..32` unconditionally for every leg and aggregated everything it found into one `LegFillObservation` — `avg_fill_price` was a quantity-weighted average, `total_slippage` a raw sum of `ExecutionReport.slippage`.

## 2. Bug description — TWO bugs found, both fixed

**Bug A (the one flagged going into this phase): units mismatch.** `ExecutionReport.slippage` is a **per-unit** adverse price delta (`SlippageCalculator`'s own documented contract), not a currency amount. `observe_leg_fills_async` summed it raw across fills, so the "slippage" fed into `compute_net_pnl(gross, fees, slippage)` as a currency deduction was too small by a factor of quantity. Entry-side slippage was never observed at all (the entry `ExecutionReport` was never read for P&L purposes), so entry execution impact was silently zero regardless of real entry slippage.

**Bug B (found auditing Bug A, more severe, not previously flagged): entry/exit blending.** Because `observe_leg_fills_async`'s scan had no lower bound, `reconcile_position_exit_async` — called at close time — picked up **both** the entry fill (sequence 1) **and** the exit fill (sequence 2) for the same leg and averaged them together. `exit_prices[...]["exit_price"]` was therefore not the exit price at all; for a simple round trip it was the **midpoint between the entry and exit fill prices**, which understated the real price move captured in gross P&L by roughly half. This directly contradicted the orchestrator's own stated intent (quoted above) to keep entry and exit fills distinguishable.

## 3. Correct accounting model (implemented)

```
Gross P&L         = (theoretical exit reference price − theoretical entry reference price) × quantity × multiplier
  − Entry execution impact   (entry order's slippage, per-unit delta × filled qty, i.e. currency)
  − Exit execution impact    (exit order's slippage, per-unit delta × filled qty, i.e. currency)
  − Brokerage / Exchange charges / GST / STT / Stamp duty   (ChargesCalculator.total, entry + exit, unchanged)
= Net realized P&L
```

The entry reference price was already correct and untouched — `LegRecord.entry_premium` is the strategy's real decision price (`leg.entry_mid`), used verbatim as `place_order`'s `reference_price` at entry. The **exit reference price** did not previously exist as tracked evidence (exit orders are placed with `reference_price=None`); it is now **reconstructed exactly** from already-recorded fields — `avg_fill_price` and the (now currency-correct) average per-unit slippage — by inverting `SlippageCalculator.apply()`'s own documented BUY-fills-high/SELL-fills-low identity. This is algebra over real recorded numbers, not a fabricated value: with zero slippage it returns the fill price unchanged.

## 4. Files changed (all additive/behavioral-fix, no new P&L system)

- **`bujji/position_lifecycle/pnl.py`** — added `reconstruct_reference_price()`. Lives here (not in the bridge) because it is P&L-shaped arithmetic; `paper_bridge.py` is architecturally forbidden from performing that (`test_bridge_never_computes_pnl_arithmetic`, a pre-existing AST-level boundary test, correctly caught an earlier draft of this fix and forced the move). `compute_net_pnl`, `compute_leg_gross_pnl`, `compute_position_gross_pnl` are **unmodified**.
- **`bujji/position_lifecycle/paper_bridge.py`**:
  - `observe_leg_fills`/`observe_leg_fills_async` gained an optional `min_sequence: int = 1` parameter (default preserves every prior caller's scan range exactly) and fixed the slippage sum to `report.slippage * filled` (currency, Bug A).
  - `build_exit_evidence_from_observations` gained an optional `leg_exit_sides: Optional[Dict[str, str]] = None` parameter; when supplied, `exit_price` is the reconstructed theoretical reference price instead of the raw fill price. `None` (the default — every prior direct caller) is unaffected.
  - `reconcile_position_exit_async`/`reconcile_position_exit` gained an optional `exit_sequence_start: int = 1` parameter. Default 1 preserves every existing test's exact behavior. When > 1, the exit observation is scoped to `[exit_sequence_start, max]` (fixing Bug B) and entry fills `[1, exit_sequence_start-1]` are separately observed and their fees/slippage folded into the position-level totals (fixing the "entry execution impact" gap).
- **`bujji/shadow_lifecycle/orchestrator.py`** — one-line change: `close_position()` now passes `exit_sequence_start=2`, matching its own already-documented entry=1/exit=2 convention. This is the only change to the live orchestration path; `open_position()`, `monitor_position()`, all other logic untouched.
- **`tests/test_phase_20_2_1_net_pnl_accounting.py`** — new, 11 tests, including the exact worked example from this phase's spec.
- No file in `bujji/execution_profiles/`, `bujji/execution_backtest/`, `bujji/mic_v0*`, `bujji/broker/simulation/`, or `bujji/broker/paper.py` was touched.

## 5. Backward compatibility impact

Every new parameter (`min_sequence`, `leg_exit_sides`, `exit_sequence_start`) is optional and defaults to the exact pre-fix behavior. The only unconditional behavior change is Bug A's currency scaling (`report.slippage * filled` instead of raw) inside `observe_leg_fills_async` — which changes `LegFillObservation.total_slippage`'s numeric value whenever real slippage is configured (it was silently wrong before; no existing test asserted a specific numeric value for it, so nothing broke). With the default zero-slippage `PaperBroker()` used by every pre-existing test and every current production call site, this fix changes **nothing** — confirmed directly (§6, `test_zero_slippage_default_paperbroker_gross_pnl_unaffected_by_fix`).

## 6. Test evidence

**New tests (11, all passing)** — `tests/test_phase_20_2_1_net_pnl_accounting.py`, driving a real `PaperBroker` through real entry (seq 1) + exit (seq 2) fills:

| Assertion | Result |
|---|---|
| Entry BUY @100 theoretical fills @100.10 (adverse, 1 tick) | ✓ |
| Exit SELL @110 theoretical fills @109.90 (adverse, 1 tick) | ✓ |
| Reconstructed `exit_price` = 110.0 (theoretical, not 109.90 fill) | ✓ |
| Position-level `slippage` = 20.0 (10 entry + 10 exit, currency) | ✓ |
| `gross_realized_pnl` = 1000.0 ((110−100)×100×1, theoretical) | ✓ |
| `net_realized_pnl` = 1000.0 − fees − 20.0, drag = fees + slippage exactly once | ✓ |
| `reconstruct_reference_price` BUY/SELL inversion, zero-slippage passthrough, unknown-side passthrough | ✓ (4 tests) |
| Default `exit_sequence_start=1` preserves every pre-fix caller's exact behavior | ✓ |
| Default zero-slippage `PaperBroker()` numerically unaffected by this fix | ✓ |

**Existing test suites re-run in full** (all previously-passing, none newly broken): `test_position_lifecycle_paper_bridge.py`, `test_position_lifecycle_paper_bridge_safety.py`, `test_position_lifecycle_paper_bridge_replay.py`, `test_shadow_lifecycle_orchestrator.py`, `test_shadow_lifecycle_orchestrator_recovery.py`, `test_shadow_lifecycle_orchestrator_safety.py`, `test_position_lifecycle_pnl.py`, `test_position_lifecycle_pnl_replay.py`, `test_position_lifecycle_pnl_safety.py`, `test_position_lifecycle_structured_exit.py`, `test_portfolio_intelligence_safety.py` — **all pass**, including `test_bridge_never_computes_pnl_arithmetic` (the pre-existing architectural boundary test this phase's first draft violated and was corrected against).

**Full regression:** **6,155 passed, 0 failed** (6,144 baseline from end of Phase 20.2 + 11 new).

**Protection hashes** — `bujji/mic_v0/engine.py`, `bujji/mic_v0/models.py`, `bujji/intelligence/regime_brain.py`, `bujji/intelligence/event_brain.py`, `bujji/broker/paper.py`, every `bujji/broker/simulation/*.py`, every `bujji/execution_profiles/*.py`, every `bujji/execution_backtest/*.py` — **byte-identical** to their state at the end of Phase 20.2. None were read from or written to by this phase.

No order-placement, no live-broker wiring, no strategy logic added or changed anywhere in this phase.

---

## Confirmation

**Execution reports now represent real monetary impact.** Entry and exit execution costs are both captured, in currency, exactly once each; gross P&L reflects the strategy's theoretical decision prices, never a slippage-contaminated or entry/exit-blended figure. The Phase 20.2 real-data proof run's directional finding (execution reality materially erodes or flips theoretical results) stands — this phase corrects the accounting mechanics underneath it, not the qualitative conclusion, since the Phase 20.2 `execution_backtest` driver was already built with the same correct model this phase now applies to the older Phase 15K/15L pipeline.
