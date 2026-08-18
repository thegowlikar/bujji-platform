# Phase 20.2 — Revised Execution Plan (post Step-1 audit)

**Mission (unchanged, now scoped correctly):** "Can Bujji evaluate strategies without pretending fills are free?" — not proving profitability, not building a new execution engine.

**Governing finding from Step 1:** the execution simulation stack (`bujji/broker/simulation/`: MarketSnapshot, SlippageCalculator, FillSimulator, ChargesCalculator, OrderLifecycleTracker, ExecutionReport) already exists and is already wired into `PaperBroker`. It has never been activated with realistic parameters — every real call site uses `PaperBroker()` with no config, which resolves to zero slippage / zero latency / always-full-fill. No backtest driver exists. No net-P&L layer exists. Phase 20.2 is therefore **activate, connect, validate** — not build.

## Step 2 — Execution Reality Classification
Write the Level A/B/C classification as fixed fact, not aspiration:
- **Level A (historical bid/ask/spread/depth/liquidity):** NOT AVAILABLE. Not attempted.
- **Level B (calibrated model):** PENDING CALIBRATION. The mechanism (SlippageConfig/LatencyConfig) exists; no real captured spread/impact data exists yet to calibrate against.
- **Level C (deterministic OHLC/volume/OI simulation with documented assumptions):** AVAILABLE. Every report from this phase is labeled "Level C simulation — not capital decision evidence."

## Step 3 — Activate, don't rewrite
Audit every `PaperBroker(...)` call site (4 found in Step 1: `broker/factory.py` ×2, `state_persistence/paper_broker.py`, `production_runtime/composition_root.py`) — none change; defaults stay ZERO/full-fill for backward compatibility. Add `bujji/execution_profiles/` (new, additive) with `NORMAL`/`STRESS`/`EXTREME` profiles, each a bundle of `SlippageConfig` + `LatencyConfig` + `RejectionConfig` + `ChargesConfig`, values marked MODELED / CALIBRATION PENDING in-module. These are opt-in — passed explicitly to a `PaperBroker(...)` instance or the new backtest driver, never wired into existing default construction.

## Step 4 — Minimal backtest driver
New, small, additive module (candidate location: `bujji/execution_backtest/` or similar single-purpose package). Pipeline only: historical candles → strategy signal (Family A/B from Phase 20.1/20.1C, reused) → `FillSimulator`/`ChargesCalculator` (reused, unmodified) → `ExecutionReport` → net P&L (Step 5). No optimizer, no parameter search, no ML.

## Step 5 — Net P&L layer
Additive function(s) in/near `position_lifecycle/pnl.py` composing existing `compute_position_gross_pnl()` with `ExecutionReport.slippage`/`.charges` → net P&L. Pure, deterministic, no broker dependency, unit tested.

## Step 6 — Reuse margin_scenario_engine.py for capital stress only
Execution stress (spread/latency/partial-fill/rejection, via Step 3 profiles) and capital stress (price move/margin/exposure, via existing `margin_scenario_engine.py`) stay two separate concerns — composed at the report layer, not merged into one model.

## Step 7 — Report
`docs/PHASE_20_2_EXECUTION_REALITY_REPORT.md` answering: what existed, what was dormant, what was activated, what's reusable, what remains unavailable, current Level, calibration status, test evidence.

## Testing gate before completion
Existing regression green; `PaperBroker()` default behavior byte-identical; new profile + net-P&L tests; no order-placement/live-broker capability added; Phase 19.19/19.20 and MIC v0 hashes unchanged.
