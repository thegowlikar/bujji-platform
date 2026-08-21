# Phase 20.23 — Market Understanding Context Bridge

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Retargeted (per the user's own audit conclusion) from "build a new market observation system" to: connect existing, already-real intelligence organs into MIC context — an integration/composition phase only.

---

## 1. Audit findings

Step 1 (conducted in the prior turn, restated here) confirmed 8 real, existing systems, all classified **A) reusable directly**:

- `bujji.market_observation` (MOC — Market Observation Contract): frozen `Observation` layer, already enforcing Observation → Derived Evidence → Intelligence → Decision separation.
- `bujji.options_observation`: option chain observation built on MOC (strikes, CE/PE OI, OI change, volume — bid/ask schema-present but always `None`, sourced from EOD Bhavcopy).
- `bujji.premium_behaviour` (Phase 15E): ATM CE/PE premium direction/acceleration, honest `UNKNOWN` on insufficient history.
- `bujji.intelligence.liquidity_brain`: real top-of-book bid/ask/spread — **live-verified against real FYERS quotes** (2026-07-20).
- `bujji.intelligence.volatility_brain`: real IV/realized-vol richness classification.
- `bujji.intelligence.structure_brain`: real OI concentration/resistance/support — **live-verified against the real FYERS option-chain endpoint**.
- `bujji.intelligence.greeks_brain`: real per-leg and position Greeks.
- `bujji.market_microstructure` (Phase 19.20.2): real minute-level OHLC + tick texture.

**Confirmed via direct inspection of `bujji/mic_v0/engine.py`, `risk_classifier.py`, `volatility_classifier.py`**: MIC v0 currently imports only `RegimeBrain` and one constant, `event_brain.VIX_ELEVATED_THRESHOLD`. `liquidity_brain`, `structure_brain`, `greeks_brain`, `volatility_brain`, and `premium_brain` are real, built, and **completely unconsumed anywhere in the codebase**. This is the actual gap — not missing intelligence, missing composition — confirmed the same shape of finding as Phase 20.22's `memory_intelligence` discovery, at larger scale.

**One genuine missing-capability (D) finding**: no code anywhere classifies `market_microstructure`'s tick texture into an EXPANDING/CONTRACTING-style state (confirmed by direct inspection of `models.py`/`microstructure_aggregator.py` — no such enum exists). Rather than invent one, this phase honestly excludes microstructure from `MarketUnderstandingContext`'s composed factors.

## 2. Why the observation system was not duplicated

Building `bujji/market_observation/` — the package name the original Phase 20.23 spec proposed — would have collided with a real, already-built package of that exact name. Building new option-chain/OI/premium/liquidity/volatility observation logic would have duplicated 7 independently real, several live-data-verified, systems. The retargeted phase's entire contribution is the missing consumer: a thin composition layer that imports already-real Reading objects and never recomputes any of them.

## 3. Existing components reused

`bujji.intelligence.models.{RegimeReading, VolatilityReading, PremiumReading, LiquidityReading, StructureReading, GreeksReading}` and their real classification enums (`RegimeType`, `Richness`, `PremiumBehavior`, `SpreadTightness`, `StructureProximity`, `GreeksExposure`, `DataQuality`) — all read-only, none modified.

## 4. Architecture decision

```
bujji/mic_context_bridge/
    __init__.py    -- Step 1 audit disclosure, public API
    models.py       -- MarketUnderstandingContext (view object)
    adapter.py       -- build_market_understanding_context() -- pure composition, zero calculation
    explain.py         -- explain_market_understanding_context()
```

```
Market Data
    ↓
Existing Intelligence Modules (regime_brain, volatility_brain, premium_brain,
                                 liquidity_brain, structure_brain, greeks_brain)
    ↓
mic_context_bridge (THIS PHASE) -- MarketUnderstandingContext
    ↓
MIC (bujji.mic_v0 -- UNTOUCHED)
    ↓
Strategy Intelligence → Opportunity Engine → Decision
```

**`bujji.mic_v0` (core) is not modified.** Every field in `MarketUnderstandingContext` (`market_state`, `supporting_factors`, `uncertainties`, `conflicts`, `explanation`) is built directly from a caller-supplied Reading object's own already-real field — no candle math, IV solving, OI aggregation, or Greeks computation happens anywhere in this package.

**Missing-data honesty**: each of the 6 input readings is independently `Optional`. A `None` input, or a present reading with `DataQuality.INSUFFICIENT`/an `UNKNOWN` classification, produces an explicit uncertainty sentence (e.g. `"Liquidity observation unavailable."`) — never a fabricated `NORMAL`/`FAIR`/default value.

**Conflict detection — one disclosed rule, not exhaustive**: `RegimeType.COMPRESSED` (low realized-volatility price action) simultaneously with `Richness.IV_RICH` (implied vol priced rich relative to realized) surfaces as an explicit conflict sentence. No other pairing is inferred — this is the only conflict rule implemented, and the report says so rather than implying broader coverage.

## 5. Files created

- `bujji/mic_context_bridge/{__init__,models,adapter,explain}.py`
- `tests/test_mic_context_bridge.py` (13 tests)
- `scripts/run_phase20_23_validation.py`

**No files modified.** `bujji.mic_v0.*`, `bujji.intelligence.*`, `bujji.market_observation`, `bujji.options_observation`, `bujji.premium_behaviour`, `bujji.market_microstructure` all confirmed untouched by mtime.

## 6. Tests (13, all passing)

1. Full composition (all 6 readings supplied) → 6 supporting factors, zero uncertainties
2–3. Missing volatility / missing liquidity → explicit uncertainty, never a fabricated `NORMAL`/default state (including the insufficient-data-quality case, not just `None`)
4. `COMPRESSED` regime + `IV_RICH` volatility → conflict surfaced; matching signals → no conflict
5. Explanation always non-empty and reflects `UNKNOWN` when nothing is supplied
6. Package never imports `bujji.mic_v0` (MIC core boundary preserved structurally, not just by convention)
7–10. `MarketUnderstandingContext` carries no `evidence_score`/`confidence`/`decision_state`/`qualification_status`/`priority_score` field
11. No broker imports anywhere in the package
12. No execution/order/quantity/capital-allocation vocabulary anywhere in the package
13. No import of `decision_orchestration`/`risk_context_adapter`/`risk_governor_bridge`/`execution_intelligence`/`broker_boundary` (no circular dependency, and structurally cannot reach Decision/Risk/Execution)

`bujji.mic_v0`'s own existing test suite is untouched by this phase — confirmed via the full regression run below rather than merely asserted.

## 7. Real-data validation

`scripts/run_phase20_23_validation.py`, using the real `bujji.intelligence.*` Reading shapes each brain's own live-verified data source populates:

**Scenario A — all intelligence available:**
```
Market state: RANGING
Supporting factors: regime classified as RANGING; implied volatility rich relative to realized
volatility (ratio=1.13); premium decay present, faster than pure time decay; liquidity tight
(combined spread 0.17%); resistance concentration detected (call writing above spot); position
exposure delta-neutral.
```
A rich, multi-factor market understanding explanation — directly matching the spec's own "NIFTY is RANGE because..." narrative goal.

**Scenario B — liquidity unavailable:**
```
Uncertainties: Liquidity observation unavailable.
```
No `NORMAL`/fabricated liquidity state anywhere in the output.

**Scenario C — conflicting signals (`COMPRESSED` regime vs. `IV_RICH` volatility):**
```
Conflicts: regime classified as COMPRESSED (low realized-volatility price action) while implied
volatility is priced RICH relative to realized volatility -- the options market is pricing more
movement than recent price action has shown.
```
Conflict surfaced explicitly; no forced single interpretation.

Confirmed directly: no `place_order`/`execution_plan`/`risk_override`/`capital_allocation` field exists anywhere on `MarketUnderstandingContext`.

## 8. Regression

Full suite: **6,464 passed, 0 failed** (6,451 baseline from Phase 20.22 + 13 new; clean run, no environmental flakes). `bujji/mic_context_bridge/`'s own 13 tests: 13/13 passing, both standalone and inside the full suite. `bujji.mic_v0`'s own pre-existing tests confirmed still passing, unmodified.

## 9. Safety verification

`grep`/`ast`-based tests confirm zero `place_order`/`modify_order`/`cancel_order` calls, zero broker imports (including `fyers_apiv3`/`dhanhq`), zero `quantity`/`capital_allocation` vocabulary anywhere in `bujji/mic_context_bridge/`. No import of `bujji.mic_v0`, `bujji.decision_orchestration`, `bujji.risk_context_adapter`, `bujji.risk_governor_bridge`, `bujji.execution_intelligence`, or `bujji.broker_boundary` anywhere in the package — this bridge is structurally incapable of reaching Decision, Risk, or Execution, not merely disciplined not to.

## 10. Remaining gaps

- **Not yet wired into live shadow cycles**: `build_market_understanding_context()` exists and is tested, but nothing in the live shadow campaign runner (Phase 20.13/20.14) yet calls it automatically per cycle, or constructs the real Reading inputs from live brain runs in sequence — that wiring is the natural next step, not fabricated here.
- **Microstructure excluded** from composed factors — no real classification enum exists anywhere in the codebase for tick-texture expansion/contraction; inventing one was explicitly avoided.
- **`options_observation`'s bid/ask remain Bhavcopy-only** (always `None`) — `liquidity_brain` is the real live spread source, but nothing yet persists its readings as `Observation`-layer records for `market_memory` to later recall.
- **Two parallel lineages remain unreconciled** (`bujji.intelligence.*` vs. `bujji.msi_*` volatility/greeks/participant-positioning implementations) — flagged in the prior audit turn, not this phase's job to fix.
- Per the user's own stated priority: next focus is wiring `MarketUnderstandingContext` into live shadow cycles, expanding option-chain/OI/premium intelligence consumption, then deeper execution readiness — not strategy expansion.
