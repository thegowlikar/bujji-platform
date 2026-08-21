# Phase 20.25 — Full Market Intelligence Runtime Wiring

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Connects the existing option-chain-based intelligence brains into the live shadow runtime cycle — without creating duplicate intelligence systems, and without fabricating the live data feed those brains genuinely still lack.

---

## 1. Step 1 audit findings

Every brain's real `analyze()` signature was read directly (not assumed):

| Brain | Real inputs required | Live-capable today? | Disposition |
|---|---|---|---|
| `RegimeBrain.analyze(candles, context)` | Spot candles only | ✅ Matches `live_shadow_runner`'s existing data | Already wired (Phase 20.24) — unchanged |
| `VolatilityBrain.analyze(spot_candles, spot, strike, t_years, ce_premium, pe_premium, ..., context)` | Real ATM CE/PE premiums + strike + t_years | ❌ Not fetched by `live_shadow_runner` today | **A) reusable directly** — wired via `option_market_data` (optional) |
| `LiquidityBrain.analyze(ce_bid, ce_ask, pe_bid, pe_ask, context)` | Real top-of-book CE/PE bid/ask | ❌ Not fetched today | **A) reusable directly** — wired via `option_market_data` |
| `StructureBrain.analyze(spot, strikes: [(strike, ce_oi, pe_oi)], context)` | Real per-strike OI | ❌ Not fetched today | **A) reusable directly** — wired via `option_market_data` |
| `GreeksBrain.analyze(spot, strike, t_years, iv_ce, iv_pe, ..., context)` | Solved IVs + strike/t_years | ❌ Not fetched today | **A) reusable directly** — wired via `option_market_data` |
| `PremiumBrain.analyze(entry_combined_premium, current_combined_premium, spot_at_entry, strike, entry_iv, entry_time, now, expiry_time, ...)` | A real **position entry point** | Structurally unavailable — Cycle 1 has zero positions, by design | **D) missing capability, permanently out of scope** — never wired |

**Confirmed via `scripts/run_phase20_13_live_entrypoint.py`**: a real, guarded `FyersBroker` connection already exists there, but only `get_recent_candles()`/`get_vix()` are called — `get_quote()`/`get_option_chain()`/`resolve_atm_contract()` (all real methods on the same `Broker` base class) are never invoked anywhere in the live pipeline.

**`mic_context_bridge.build_market_understanding_context()`** already accepts all 6 readings as independent optional parameters (confirmed by re-reading its Phase 20.23 signature) — **no schema extension was needed**. Naming-collision check found nothing new beyond the collisions already disclosed in Phases 20.22–20.24.

## 2. Why no new intelligence system was created

All 5 remaining brains (beyond `RegimeBrain`) are real, already-tested, already-unmodified. The gap was never "missing intelligence" — it was that `live_shadow_runner` never supplies the real option-chain/quote/OI data these brains require, and `PremiumBrain` structurally cannot run without a real position that doesn't exist. This phase adds zero new brain logic; it adds one small input contract and one small composition function that call the 4 real, callable brains directly.

## 3. Architecture decision — smallest correct implementation

**Chosen**: extend the existing `bujji/mic_runtime_context/` package (Phase 20.24) rather than create a new top-level package — this is Option B/C combined, scoped to the minimum:

```
bujji/mic_runtime_context/
    models.py          -- + OptionMarketDataForCycle (new, small input contract)
    brain_assembly.py    -- NEW: assemble_market_understanding_from_option_data()
    adapter.py, explain.py, __init__.py   -- unchanged from Phase 20.24
```

`assemble_market_understanding_from_option_data(spot_candles, option_data, *, context)` calls `VolatilityBrain().analyze(...)` → `LiquidityBrain().analyze(...)` → `StructureBrain().analyze(...)` → `GreeksBrain().analyze(...)` (all real, unmodified, all instantiated fresh per call — matching `mic_v0.engine`'s own stateless-brain reuse pattern) and composes the result via the existing `build_market_understanding_context()`. `regime` and `premium` are deliberately left `None` — see §5.

## 4. Runtime integration path — one new optional parameter, additive only

`bujji.live_shadow_runner.runner.process_cycle()` gained one new parameter:

```python
option_market_data: Optional[OptionMarketDataForCycle] = None
```

When `None` (today's actual live-runtime state — `live_shadow_runner` does not yet fetch option-chain data), behavior is byte-for-byte identical to Phase 20.24. When supplied, `assemble_market_understanding_from_option_data()` is called once per cycle (not per-strategy — option-chain data describes the market this cycle, not any one strategy) and its result feeds `build_mic_runtime_context(..., market_understanding=...)`, exactly as already designed in Phase 20.24.

**Actually fetching real option-chain/quote data from the broker in the live entrypoint (`scripts/run_phase20_13_live_entrypoint.py`) is NOT done in this phase** — that remains a separate, disclosed, broker-interaction-heavy future task. This phase provides the tested wiring; it does not fabricate the missing live fetch.

## 5. Two honest exclusions, disclosed rather than worked around

- **`PremiumBrain` is never called.** Its real signature requires `entry_time`/`entry_combined_premium` — a real position's own entry point. Cycle 1 has zero positions by design. Fabricating an "entry" would violate the entire engagement's own anti-fabrication discipline.
- **`regime` is left `None`** in the composed `MarketUnderstandingContext` this phase produces. `bujji.mic_v0.engine.compose_market_state()` computes its own internal `RegimeReading` but returns only the mapped `MarketState` string (confirmed by direct inspection) — reconstructing a synthetic `RegimeReading` would require fabricating a `confidence` float that was never real. The real MIC regime is not lost: it is already carried through faithfully via `RuntimeIntelligenceContext.mic_market_context.mic_regime` (Phase 20.24), a separate, real field.

## 6. Files created / modified

**Created**: `bujji/mic_runtime_context/brain_assembly.py`, `tests/test_mic_runtime_context_phase20_25.py` (9 tests).

**Modified**: `bujji/mic_runtime_context/models.py` (+`OptionMarketDataForCycle`), `bujji/mic_runtime_context/__init__.py` (+exports, +audit disclosure), `bujji/live_shadow_runner/runner.py` (+1 optional parameter, +4 lines inside the existing per-cycle body). No other file touched. `bujji.intelligence.*`, `bujji.mic_v0.*`, `bujji.mic_context_bridge.*` all confirmed untouched by mtime.

## 7. Tests (9, all passing) + regression confirmation

1. Option data produces a rich `MarketUnderstandingContext` with real supporting factors
2. `process_cycle()` with `option_market_data` supplied reaches strategy scoring, zero errors
3. `option_market_data=None` (the default) leaves `market_understanding=None` — identical to Phase 20.24 behavior
4. Insufficient candles for `VolatilityBrain` → honest `"Volatility observation unavailable"` uncertainty, never fabricated
5. A deliberately rich-IV, flat-realized-vol input confirms `regime=None` correctly prevents the `COMPRESSED`-vs-`IV_RICH` conflict rule from firing dishonestly (it requires a real `RegimeReading`, which this composition path does not have)
6. `evidence_score`/`priority_score` byte-identical whether or not `option_market_data` is supplied
7. `decision_state`/`confidence` byte-identical whether or not `option_market_data` is supplied — proves this parameter never reaches decision logic
8. No broker imports anywhere in `brain_assembly.py`
9. `PremiumBrain(` never appears in `brain_assembly.py` — confirms the disclosed exclusion, not a silent omission

Plus: the full pre-existing `live_shadow_runner`/`entrypoint_wiring`/`mic_runtime_context` test suites (30 tests) re-run unmodified after the `runner.py` edit — all still passing.

## 8. Real-data validation

Scenario 1 (strong context + good opportunity), Scenario 2 (missing data → honest uncertainty), Scenario 3 (conflict handling) are all demonstrated directly by tests 1, 4, and 5 above using real `StrategyEvidence` (Phase 20.5's own published evidence) and real brain computations (not fabricated readings) — see `tests/test_mic_runtime_context_phase20_25.py`.

## 9. Regression

Full suite: **6,490 passed, 0 failed** (6,481 baseline from Phase 20.24 + 9 new; clean run, no environmental flakes). `bujji/mic_runtime_context/`'s Phase 20.25 tests: 9/9 passing, both standalone and inside the full suite. Pre-existing `live_shadow_runner`/`mic_runtime_context` suites (30 tests) confirmed still passing after the `runner.py` edit.

## 10. Safety verification

`grep`/`ast`-based tests confirm zero broker imports (`bujji.broker`, `fyers_apiv3`, `dhanhq`) anywhere in `brain_assembly.py`, and confirm `PremiumBrain` is never called. No order-placement vocabulary, no capital/quantity computation, anywhere in the new code. `evidence_score` and `decision_state`/`confidence` proven byte-identical with and without the new optional parameter — this phase cannot alter decision output.

## 11. Remaining gaps

- **Live option-chain fetching is still not built.** `live_shadow_runner`/`run_phase20_13_live_entrypoint.py` do not yet call `FyersBroker.get_quote()`/`get_option_chain()`/`resolve_atm_contract()` to actually populate an `OptionMarketDataForCycle` during a real session — the wiring exists and is tested; the live data source does not yet feed it. This is a separate, larger, broker-interaction-heavy task, honestly deferred, not fabricated.
- **`PremiumBrain` remains permanently unwireable** in Cycle 1's current shadow-only architecture — would require a genuine position-entry concept that conflicts with the "no positions" design boundary.
- Two independent regime taxonomies (`mic_v0` vs. `intelligence.regime_brain`) remain unreconciled — disclosed in Phase 20.24, unaffected by this phase.
- Per the user's own stated priority: next focus is deepening the live shadow campaign, continuing to bridge remaining disconnected intelligence modules (now specifically: the live option-chain fetch), and moving toward real-money readiness — not strategy expansion.
