# Phase 20.2 — Execution Reality Report

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Can Bujji evaluate strategies without pretending fills are free?"** Not whether any strategy is profitable — strategy validation comes later.

---

## 1. What existed

A complete execution-simulation stack already existed before this phase, in `bujji/broker/simulation/` ("Gate F.2"), already wired into `PaperBroker`:

- `market_snapshot.py` — `MarketSnapshot` (price/bid/ask/volatility/liquidity, caller-supplied only, never fabricated)
- `slippage.py` — `SlippageCalculator` (ZERO / FIXED_TICK / PERCENTAGE / VOLATILITY_ADJUSTED, deterministic, always adverse)
- `fill_simulator.py` — `FillSimulator` (composes slippage + injectable latency + rejection + partial fills)
- `charges.py` — `ChargesCalculator` (brokerage, STT, exchange charges, GST, SEBI charges, stamp duty — from a disclosed, overridable `ChargesConfig`)
- `order_lifecycle.py` — `OrderLifecycleTracker` (order-scoped fill-progress state machine)
- `execution_report.py` — `ExecutionReport` (one immutable record per simulated order)

Also already existing, in `position_lifecycle/pnl.py` and `position_lifecycle/paper_bridge.py` (Phase 15K/15L): a **complete net P&L pipeline** — `compute_net_pnl(gross_pnl, fees, slippage)`, wired end-to-end from `PaperBroker.get_execution_report()` through `build_exit_evidence_from_observations()` into `build_structured_exit()`.

## 2. What was dormant

Every real `PaperBroker(...)` call site (`broker/factory.py` ×2, `state_persistence/paper_broker.py`, `production_runtime/composition_root.py`) constructs it with **no config arguments**. `PaperBroker`'s own source comment confirms this was deliberate: *"BYTE-IDENTICAL behavior to before this gate: SlippageConfig()'s own default mode is ZERO... LatencyConfig()'s own default mode is ZERO."* Every paper trade this system has ever simulated ran at zero slippage and zero latency — full realism machinery, switched off by default.

**A second, more consequential dormant issue, found during this phase's audit and NOT fixed here (out of scope — no Phase 15K/15L file was modified):** the existing `paper_bridge.py` pipeline computes gross P&L from the **exit fill price** (already slippage-adjusted by `FillSimulator`) but the **entry** side uses `entry_mid` (never fill-adjusted), and then separately subtracts `ExecutionReport.slippage` — which is a **per-unit price delta**, not a position-level currency amount — via `compute_net_pnl`. This is a units mismatch: it double-counts exit-side slippage at the wrong (unscaled-by-quantity) magnitude, and never reflects entry-side slippage at all. This is disclosed here as a real, pre-existing correctness gap for a future targeted phase — Phase 20.2's own new code (§3 below) does not repeat it.

## 3. What was activated / built

- **`bujji/execution_profiles/`** (new, additive) — `NORMAL`/`STRESS`/`EXTREME` profiles, each bundling `SlippageConfig`+`LatencyConfig`+`RejectionConfig`+`ChargesConfig`. Every profile is Level C, `CALIBRATION_PENDING` (see §6). Slippage modeled as percentage-of-price (0.02% / 0.05% / 0.15%) rather than fixed-tick, to avoid asserting an unverified NIFTY futures tick size as fact. Charges identical across all three (charges don't change with market stress). `PaperBroker()`'s own default construction is untouched — these are opt-in only.
- **`bujji/execution_backtest/`** (new, additive) — minimal driver: real candles → MIC-classified window (reuses `mic_v0_validation.intraday_validation` unmodified) → Family A/B illustrative signal → `FillSimulator`+`ChargesCalculator` (reused unmodified) → `ExecutionReport` → net P&L via `compute_leg_gross_pnl`+`compute_net_pnl` (reused unmodified from `position_lifecycle.pnl`), composed correctly (gross P&L from reference prices only, slippage cost scaled explicitly by quantity×multiplier here — avoiding §2's units bug). No optimizer, no parameter search, no ML.

## 4. What is reusable (confirmed, unmodified)

`FillSimulator`, `SlippageCalculator`, `ChargesCalculator`, `OrderLifecycleTracker`, `ExecutionReport`, `compute_leg_gross_pnl`, `compute_position_gross_pnl`, `compute_net_pnl`, `mic_v0_validation.generate_rolling_windows`/`classify_intraday_window`. Every one of these existed before this phase and is used byte-identical.

## 5. What remains unavailable

Level A (real historical bid/ask/spread/depth/liquidity for NIFTY futures or options) — confirmed NOT AVAILABLE in Phase 20.0, unchanged by this phase. No real captured spread/impact dataset exists to calibrate Level B against. `MarketSnapshot.liquidity_score` is never supplied by the new backtest driver (never fabricated), so STRESS/EXTREME's liquidity-gated rejection never actually fires in this phase's proof run (0 rejections across every profile, §8) — a disclosed limitation, not a claim that stress scenarios never reject in reality.

## 6. Current Execution Reality Level

**Level C — deterministic simulation, not capital decision evidence.** Every number produced by `execution_profiles`/`execution_backtest` is MODELED, `CALIBRATION_PENDING`. Level B remains unreachable until a real captured spread/impact dataset exists (none does today).

## 7. Calibration status

`CALIBRATION_PENDING` on every profile and every assumption. No profile parameter in this phase was fit to real captured market microstructure data — none exists to fit to.

## 8. Test evidence

- 22 new tests (`test_execution_profiles.py`: 10, `test_execution_backtest.py`: 12), all passing.
- Full regression: **6,144 passed, 0 failed** (6,122 baseline + 22 new; baseline itself reconfirmed clean before any Phase 20.2 file was added).
- `git status` confirms no protected file (Phase 19.19/19.20 runtime, MIC v0, Gate F.2 simulation files) was modified — only new files added under `bujji/execution_profiles/` and `bujji/execution_backtest/`, plus two new test files and this doc.
- No order-placement, no live broker wiring, in any new file — confirmed by direct code inspection (only `FillSimulator.simulate()`, a pure function, is called; no `Broker`/`PaperBroker` instance is constructed by the new driver).

**Real-data proof run** (`scripts/run_phase20_2_proof.py`, read-only, NIFTY futures, 72 real trading days 2026-05-01→2026-08-13, 30-minute MIC windows, 1 lot = 50):

| Family | Profile | Trades | Theoretical gross | Net P&L | Fees | Slippage cost | Δ |
|---|---|---:|---:|---:|---:|---:|---:|
| A (trend-following) | NORMAL | 480 | ₹11,03,970 | ₹8,24,124 | ₹49,012 | ₹2,30,835 | −25.3% |
| A (trend-following) | STRESS | 480 | ₹11,03,970 | ₹4,77,875 | ₹49,009 | ₹5,77,086 | −56.7% |
| A (trend-following) | EXTREME | 480 | ₹11,03,970 | −₹6,76,287 | ₹48,997 | ₹17,31,259 | −161.3% (flips negative) |
| B (mean reversion) | NORMAL | 727 | −₹3,12,900 | −₹7,36,615 | ₹74,201 | ₹3,49,514 | −135.4% |
| B (mean reversion) | STRESS | 727 | −₹3,12,900 | −₹12,60,880 | ₹74,196 | ₹8,73,784 | −303.0% |
| B (mean reversion) | EXTREME | 727 | −₹3,12,900 | −₹30,08,431 | ₹74,179 | ₹26,21,352 | −861.5% |

**Answer to this phase's own question: yes — execution reality materially changes theoretical results.** Family A's theoretical result is strongly positive but turns negative under EXTREME; Family B's already-negative theoretical result deepens sharply under any stress profile. Neither family rule is optimized, tuned, or claimed profitable — both are illustrative placeholders (Family A: buy/sell in the window's own realized direction; Family B: fade the window's own net move) that exist only to produce real reference prices for the simulator, per this phase's explicit "no strategy optimization" boundary.

---

## Next recommendation

Not proving profitability, per this phase's own governance rule. Based only on the evidence above:
1. **Execution cost is not a rounding error at any tested profile** — even NORMAL erodes 25–135% of theoretical P&L on these illustrative signals. Any future strategy research must report net, not gross, results by default.
2. **The paper_bridge.py units/double-count issue (§2) should be a scoped follow-up**, not fixed inside this phase's boundary.
3. **Level B calibration remains blocked** on the same historical bid/ask gap Phase 20.0 already found — unchanged by this phase.
