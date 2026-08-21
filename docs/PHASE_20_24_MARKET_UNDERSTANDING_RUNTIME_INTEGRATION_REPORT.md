# Phase 20.24 — Market Understanding Runtime Integration

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Wires the completed `MarketUnderstandingContext` (Phase 20.23) into Bujji's runtime decision flow by activating a real, already-existing, already-tested integration point that had been dormant for the entire engagement.

---

## 1. Step 1 audit findings

| Component | Classification | Disposition |
|---|---|---|
| `bujji.strategy_intelligence.MarketContext` + `scoring._apply_mic_context()` (Phase 20.5) | **A) Reusable directly — the entire finding this phase turns on** | `score_strategy(evidence, context: Optional[MarketContext])` already implements exactly the "MIC regime demotes confidence by one band when unfavorable/never-validated, never promotes, never touches evidence_score" rule, fully tested since Phase 20.5. **Confirmed by inspecting every real call site**: `bujji/live_shadow_runner/runner.py:88` called `score_strategy(evidence)` — bare, `context` never supplied. This dormant, already-built path is the actual target. |
| `bujji.mic_context_bridge.MarketUnderstandingContext` (Phase 20.23) | **A) Reusable directly** | This phase's other real input. Never recomputed, never modified. |
| `bujji.decision_context.DecisionContext` (Phase 19.4, MSI lineage), `bujji.intelligence.context.IntelligenceContext` (Phase 19.2.2, MSI lineage), `bujji.memory_context.MemoryDecisionContext` (Phase 20.22) | **C) Wrong domain / already-disclosed-distinct-name precedent** | None extended or renamed. `RuntimeIntelligenceContext` is a new, distinctly-named class avoiding collision with all three. |
| `bujji.mic_v0` (core) | **Read, NOT modified** | `MarketState`'s schema (`market_regime`/`volatility_state`/`risk_state`) unchanged; `bujji/mic_v0/*` untouched by this phase. |
| `bujji.live_shadow_runner.runner.process_cycle()` | **The one runtime file edited** | Not "MIC core" — the orchestration entrypoint, already extended multiple times in this engagement (e.g. Phase 20.14). |

**Naming collision search** (`market_context`, `mic_context`, `runtime_context`, `intelligence_context`, `decision_context`): confirmed 5 real, existing classes across `decision_context`, `epistemics.identity`, `intelligence.context`, `memory_context`, and — critically — `bujji.strategy_intelligence.models.MarketContext`, the real class this phase activates rather than replaces.

## 2. Why the observation system was not duplicated

This phase does not build a new confidence-adjustment mechanism. `MarketContext`/`_apply_mic_context()` already exist, are already tested, and already implement the exact rule the spec describes. The only genuine gap was that nothing in the live pipeline ever constructed and passed a `MarketContext` — an integration gap, the same shape of finding as Phase 20.22 (`memory_intelligence`) and Phase 20.23 (`bujji.intelligence.*` brains), now closed for MIC regime.

## 3. Architecture decision

```
bujji/mic_runtime_context/
    __init__.py    -- Step 1 audit disclosure, public API
    models.py       -- RuntimeIntelligenceContext (view object)
    adapter.py       -- build_mic_runtime_context() -- pure composition, zero calculation
    explain.py         -- explain_runtime_intelligence_context()
```

```
Market
  ↓
Intelligence Brains (regime/volatility/premium/liquidity/structure/greeks)
  ↓
MarketUnderstandingContext (Phase 20.23)
  ↓
mic_runtime_context (THIS PHASE) -- RuntimeIntelligenceContext
  ├─ mic_market_context: bujji.strategy_intelligence.MarketContext  (REAL, feeds score_strategy())
  └─ market_understanding: MarketUnderstandingContext                (explainability only)
  ↓
Strategy Intelligence (score_strategy(evidence, context=mic_market_context))
  ↓
Opportunity Engine → Decision
```

`build_mic_runtime_context(strategy_regime, favorable_regimes, unfavorable_regimes, *, market_understanding=None)` builds the real `MarketContext` from the ALREADY-MAPPED strategy-environment regime vocabulary (`TREND_UP`/`RANGE`/`TRANSITION`) — not `mic_v0.MarketState.market_regime` directly, since `favorable_regimes`/`unfavorable_regimes` are already expressed in the mapped vocabulary at every real call site (confirmed by reading `runner.py`'s own existing `_MIC_REGIME_TO_STRATEGY_REGIME` table and `evaluate_opportunity()`'s own `environment.mic_regime` usage). This module does not duplicate that mapping table — the caller (the runtime entrypoint, which already computes `strategy_regime`) supplies it directly.

## 4. Runtime integration path — the one, minimal, disclosed edit

`bujji/live_shadow_runner/runner.py`, inside the existing per-strategy loop:

```python
# before
score = score_strategy(evidence)

# after
runtime_context = build_mic_runtime_context(strategy_regime, favorable, unfavorable)
score = score_strategy(evidence, context=runtime_context.mic_market_context)
```

No function signature changed. No new parameter. `strategy_regime`, `favorable`, `unfavorable` were all already in scope. This is 100% behavior-preserving for every existing caller and test **except** that MIC regime can now genuinely demote confidence, exactly as Phase 20.5 always intended — verified by running the full pre-existing `live_shadow_runner`/`entrypoint_wiring` test suites unmodified after the change (13/13 passed).

`MarketUnderstandingContext`'s richer factors (volatility/premium/liquidity/structure/greeks) are **not yet threaded into `runner.py`'s live flow** — doing so would require `live_shadow_runner` to call the 6 intelligence brains against real broker quotes every cycle, a separate, larger wiring task Phase 20.23's own report already flagged as future work. This phase provides the tested composition function (`build_mic_runtime_context`) ready for that future caller; it does not fabricate the missing live-brain wiring itself.

## 5. Files created / modified

**Created**: `bujji/mic_runtime_context/{__init__,models,adapter,explain}.py`, `tests/test_mic_runtime_context.py` (17 tests), `scripts/run_phase20_24_validation.py`.

**Modified**: `bujji/live_shadow_runner/runner.py` — one import added, one line changed inside the existing loop (shown above). No other file touched. `bujji.mic_v0.*`, `bujji.strategy_intelligence.*`, `bujji.mic_context_bridge.*` all confirmed untouched by mtime.

## 6. Real finding surfaced during validation

`bujji.mic_v0`'s own regime vocabulary (`TREND`/`RANGE`/`UNCLEAR`, further mapped to `TREND_UP`/`RANGE`/`TRANSITION`) and `bujji.intelligence.regime_brain`'s own `RegimeType` (`TRENDING`/`RANGING`/`VOLATILE`/`COMPRESSED`/`TRANSITIONING`/`UNKNOWN`) are two **independently real, differently-named** regime taxonomies. `build_mic_runtime_context()`'s regime-consistency check correctly detects this mismatch (e.g. `MarketUnderstandingContext.market_state='RANGING'` vs. mapped MIC regime `'RANGE'`) and surfaces it as an explicit, disclosed note — never silently conflating the two vocabularies. This is a genuine architectural observation, not a bug in this phase, and is left unreconciled per the spec's own "expose conflicts, do not resolve" instruction.

## 7. Tests (17, all passing)

1. Complete context composition (regime + market understanding both present)
2. Regime preserved verbatim in `MarketContext.mic_regime`
3–4. Volatility / richer factors preserved unmodified via `market_understanding` passthrough
5. Liquidity uncertainty preserved, never fabricated as `NORMAL`
6. Conflict propagation: `COMPRESSED` + `IV_RICH` → 1 conflict surfaced
7. Missing-data honesty: no `market_understanding` supplied → explicit, honest explanation, never fabricated
8. Package never imports `bujji.mic_v0` (MIC core boundary structural, not just conventional)
9–10. `score_strategy()` still honors the real, unmodified `MarketContext` contract: favorable regime → confidence unchanged (`HIGH`); unfavorable regime → confidence demoted exactly one band (`HIGH`→`MODERATE`)
11. `evidence_score` byte-identical (78.62) whether or not runtime context is supplied
12. Confidence unchanged for a favorable regime
13–14. No strategy/opportunity/execution/order/capital vocabulary anywhere in the package
15. No broker imports
16. No import of `decision_orchestration`/`risk_context_adapter`/`risk_governor_bridge`/`execution_intelligence`/`broker_boundary` (no circular dependency, structurally cannot reach Decision/Risk/Execution)
17. Explainability: every context renders a non-empty, regime-named explanation

Plus: the full pre-existing `tests/test_live_shadow_runner.py` and `tests/test_phase20_14_entrypoint_wiring.py` suites (13 tests) re-run unmodified after the `runner.py` edit — all still passing.

## 8. Real-data validation

`scripts/run_phase20_24_validation.py`, using real Phase 20.5 evidence and a real `process_cycle()` call:

**End-to-end**: a real shadow cycle through the now-wired `process_cycle()` produced `observation.confidence == MODERATE` — demoted from the evidence-based `HIGH`, proving the dormant confidence-adjustment path is genuinely active in the real runtime, not merely unit-tested in isolation.

**Scenario A — all intelligence available**: rich `RuntimeIntelligenceContext` with regime, volatility, and the (correctly disclosed) regime-vocabulary mismatch note.

**Scenario B — liquidity unavailable**: `"Liquidity observation unavailable."` surfaced honestly; no fabricated `NORMAL` state anywhere in the output.

**Scenario C — conflicting intelligence** (`COMPRESSED` regime + `IV_RICH` volatility): the one conflict rule fires, surfaced explicitly, never forcibly resolved.

## 9. Regression

Full suite: **6,481 passed, 0 failed** (6,464 baseline from Phase 20.23 + 17 new; clean run, no environmental flakes). `bujji/mic_runtime_context/`'s own 17 tests: 17/17 passing, both standalone and inside the full suite. Pre-existing `live_shadow_runner` test suites (13 tests) confirmed still passing after the `runner.py` edit.

## 10. Safety verification

`grep`/`ast`-based tests confirm zero `place_order`/`modify_order`/`cancel_order` calls, zero broker imports (`bujji.broker`, `fyers_apiv3`, `dhanhq`), zero `quantity`/`capital` vocabulary anywhere in `bujji/mic_runtime_context/`. No import of `bujji.decision_orchestration`, `bujji.risk_context_adapter`, `bujji.risk_governor_bridge`, `bujji.execution_intelligence`, or `bujji.broker_boundary` anywhere in the package — structurally cannot reach Decision, Risk, or Execution. `evidence_score` confirmed byte-identical (78.62) with and without the runtime context supplied.

## 11. Remaining gaps

- **Richer factors not yet live-wired**: `MarketUnderstandingContext`'s volatility/premium/liquidity/structure/greeks are composed and tested, but `live_shadow_runner` does not yet call the 6 intelligence brains against real broker quotes each cycle to produce one — a separate, larger wiring task, not fabricated here.
- **Two unreconciled regime taxonomies**: `mic_v0`'s own vocabulary vs. `intelligence.regime_brain`'s `RegimeType` — surfaced honestly by this phase's consistency check, not resolved (per the spec's own "expose conflicts" instruction).
- **Per-strategy `favorable_regimes`/`unfavorable_regimes` disclosure gap unchanged**: these are still caller-supplied, per Phase 20.5's own established design; this phase does not compute them.
- Per the user's own stated priority: next focus is expanding option-chain/OI/premium intelligence, deepening the live shadow campaign, continuing to bridge remaining disconnected intelligence modules, and moving toward real-money readiness — not strategy expansion.
