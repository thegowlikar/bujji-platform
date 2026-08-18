# Phase 20.6 — Opportunity Qualification Engine

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Given today's conditions, which trusted strategies are worthy of attention?"** Not execution, not order placement, not position sizing, not portfolio allocation — no such vocabulary exists anywhere in this phase's code.

---

## 1. Repository audit (Step 1, mandatory, done before any code was written)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.decision_context.compatibility_engine.StrategyCompatibilityEngine` (Phase 19.4, this engagement) | **B) Wrong domain, real design precedent** | Classifies environmental compatibility for *options* strategy families (premium selling/buying/condor/fly) from `MarketIntelligenceSnapshot` (VolatilityBrain/RegimeBrain/LiquidityBrain) — not MIC v0, not Cycle-1's futures families. **Not reused as code.** Its two disclosed disciplines — "UNASSESSED rather than force a guess when no real signal exists" and "one hard blocker overrides everything" — are followed as design precedent in this phase's `rules.py`, without importing anything. |
| `bujji.msi_strategy_eligibility`, `bujji.msi_opportunity_assessment`, `bujji.msi_strategy_selection_foundation`, `bujji.msi_decision_synthesis.MarketOpportunityAssessment` (old MSI Series 77–99 lineage) | **B) Wrong domain** | Already established across Phases 20.3–20.5's own audits: options-domain, consumes MDI/MSSI/Consensus/VSB, not wired into the live `production_runtime`/`shadow_lifecycle` path. Confirmed again here, not reused. |
| `bujji.qualification` (Engineering Series 58) — `QualificationRecord`/`QualificationReport`/`HistoricalQualificationRunner` | **B) Wrong domain entirely** | This is *runtime/replay-session* qualification (did a session COMPLETE / get REJECTED_CIRCUIT / FAIL) — a completely different meaning of "qualification" from strategy-opportunity qualification. Not reused. |
| `bujji.trading_brain.qualification` (`DecisionPipelineQualification`) | **B) Wrong domain** | Pipeline-health qualification (is the decision pipeline itself producing valid output), not strategy-vs-market qualification. Not reused. |
| `bujji.decision_intelligence.models.OpportunityObservation` (Phase 19.6, this engagement) | **B) Too thin to matter** | A plain `(description, supporting_evidence)` text container for narrative output — no evaluation logic to reuse. |
| `bujji.trading_brain.risk_governor.*` (capital/margin adequacy) | **Out of scope** | Capital sizing/margin concern, explicitly excluded from this phase's boundary regardless of domain fit. |
| `bujji.strategy_intelligence` (Phase 20.5, this engagement) | **A) Reusable directly** | `StrategyScore`/`score_strategy` — **reused unmodified** as the entire "Strategy Intelligence" input. Never recalculated. |
| `bujji.mic_v0.models` (`RISK_*`, `VOLATILITY_*`, Phase 20.1) and `bujji.mic_v0_validation.models_intraday` (`INTRADAY_*`, Phase 20.1C) | **A) Reusable directly** | **Reused unmodified** as the vocabulary for `MarketEnvironment`. Intraday regime vocabulary used for `mic_regime` (not MIC v0's raw session-level TREND/RANGE/UNCLEAR, which Phase 20.1 found INCONCLUSIVE at that coarser horizon) — session-level VIX-derived `risk_state`/`volatility_state` used as-is, since those were never found horizon-dependent. |
| `bujji.execution_profiles` names (NORMAL/STRESS/EXTREME, Phase 20.2) | **A) Reusable directly** | Used as-is for `MarketEnvironment.execution_profile_name`. |
| `bujji.epistemics.uncertainty.NONE` | **A) Reusable directly** | Used as-is to detect zero-confidence evidence (already the mechanism `strategy_intelligence` composes into `StrategyScore.confidence`). |

**Conclusion:** no existing opportunity-qualification *engine* was found to duplicate — every candidate with the right name ("qualification," "opportunity," "eligibility") turned out to be a different domain on inspection. The real reuse was entirely at the *input* layer (Phase 20.5's scores, MIC v0's vocabulary, Phase 20.2's execution profiles) and at the *design-pattern* layer (Phase 19.4's honest-abstention and hard-blocker-override disciplines).

## 2. Architecture

```
bujji/opportunity_intelligence/
    __init__.py
    models.py     -- MarketEnvironment, QualificationReason, QualificationDecision, OpportunityAssessment
    rules.py       -- qualification_rules(), has_sufficient_evidence(), hard_block_reason(), is_ideal_environment()
    evaluator.py   -- evaluate_opportunity()
```

Three files, ~230 lines total.

```
Strategy Intelligence (Phase 20.5, StrategyScore)  →  Opportunity Engine  →  Qualification Decision
                                                              ↑
                                    Market Compatibility (MIC v0 regime/risk/vol)
                                    Execution Environment (Phase 20.2 profile + known drag)
```

`evaluate_opportunity()` reads `StrategyScore` but never writes to any of its fields — proven directly by test (`assessment.strategy_score is score`, the literal same object, across every environment tried).

## 3. Qualification model

Four states — `ELIGIBLE`, `WATCH`, `BLOCKED`, `INSUFFICIENT_EVIDENCE` — decided by four rules, applied in a **fixed, disclosed order** (`rules.qualification_rules()` returns this order as data, not just as comments):

1. **INSUFFICIENT_EVIDENCE** if `confidence == NONE` or `effective_score < 30` — checked *before* any environment rule, so no market condition can ever elevate an unconfirmed strategy into consideration.
2. **BLOCKED** if any hard blocker fires: `risk_state == EXTREME`; data quality flagged not OK; the current regime is in the strategy's *disclosed* unfavorable set; or execution drag under an assumed EXTREME execution profile is ≥50% (realistically untradeable). Any one of these overrides everything else.
3. **ELIGIBLE** if the regime is in the strategy's disclosed favorable set **and** risk/volatility/execution are all normal **and** `effective_score >= 70`.
4. **WATCH** otherwise — evidence is sufficient, nothing is blocked, but conditions aren't ideal.

MIC context enters *only* at steps 2–4, as an input to `MarketEnvironment` — it never touches `StrategyScore.evidence_score`, `edge_component`, `execution_component`, or `stability_component` (those come from Phase 20.5 and are read-only here).

## 4. Decision examples (real evidence — Trend Following, from Phase 20.4/20.5)

| Scenario | Decision | Reason |
|---|---|---|
| TREND_UP, NORMAL risk/vol/execution | **ELIGIBLE** | IDEAL_MATCH — regime favorable, all normal, score 78.62 ≥ 70 |
| TRANSITION, NORMAL risk/vol/execution | **WATCH** | regime not in favorable set |
| TREND_UP, ELEVATED risk | **WATCH** | risk not NORMAL |
| TREND_UP, HIGH volatility | **WATCH** | volatility not LOW/NORMAL |
| TREND_UP, STRESS or EXTREME execution profile | **WATCH** | execution profile not NORMAL |
| TREND_UP, EXTREME risk | **BLOCKED** | EXTREME_RISK — overrides the otherwise-favorable regime |
| RANGE (Trend Following's own disclosed unfavorable regime) | **BLOCKED** | REGIME_INCOMPATIBLE |

## 5. Real-data validation (`scripts/run_phase20_6_qualification.py` — no strategy discovery rerun; evidence taken verbatim from Phase 20.4/20.5)

Mean Reversion (FAIL, Phase 20.4) was evaluated across the **same nine environment scenarios** as Trend Following — every single one returned **INSUFFICIENT_EVIDENCE** (`effective_score=0.0`), regardless of regime, risk, volatility, or execution profile. This is the direct, real-data proof of the phase's central instruction: *"MIC must NOT rewrite historical strategy evidence."* A confirmed-failed strategy stays unqualified no matter how favorable the market looks — the engine never manufactures an opportunity out of good weather alone.

Trend Following's `evidence_score` was logged as **78.62 in every one of the nine scenarios** — confirming, on real numbers rather than only in the unit test suite, that environment changes decisions without ever touching the underlying evidence.

## 6. Limitations

1. **`favorable_regimes`/`unfavorable_regimes` must be supplied honestly by the caller** — same limitation already disclosed in Phase 20.5 for `MarketContext`; this package has no way to verify a caller declared them truthfully from real research.
2. **The two hard numeric thresholds (`effective_score >= 30` for evidence sufficiency, `>= 70` for ELIGIBLE, 50% drag for execution-blocked) are disclosed, round, pre-registered reference points**, not derived from a formal statistical procedure — the same honestly-disclosed judgment-call limitation Phase 20.5 already carries for its own scoring constants.
3. **No liquidity-state signal exists in the current MIC v0 lineage** — `execution_profile_name` (NORMAL/STRESS/EXTREME) is used as the disclosed proxy for "how forgiving is the execution environment right now," since Cycle 1 has no dedicated liquidity classifier feeding MIC v0.
4. **Only two strategies were available to validate against** — the rule logic is exercised across nine environment scenarios each, but not against a larger or more varied strategy population.
5. **This package has no memory** — every call to `evaluate_opportunity()` is a pure, stateless snapshot; it does not track how long a strategy has sat at WATCH, or detect flapping between states across cycles.

## 7. Future integration points

- **Risk Governor** (not built): would consume a `Tuple[OpportunityAssessment, ...]`, filter to `ELIGIBLE` (and perhaps `WATCH`, with reduced weight), and decide sizing/exposure — this phase produces the qualification; it does not gate or size capital.
- **Live MIC feed**: `MarketEnvironment` is currently constructed by hand per scenario in the validation script; a live wiring would build it each cycle from `bujji.mic_v0` (unmodified) and `bujji.mic_v0_validation` (unmodified) — deliberately not built here.
- **Liquidity signal**: if a dedicated liquidity classifier is ever added to the MIC v0 lineage (currently absent, per Limitation 3), `MarketEnvironment` has a natural slot for it without redesigning this package.

---

## Testing & regression

16 new tests (evidence preservation: 2, qualification correctness: 8, abstention: 2, safety boundary: 3, plus the real-data script above), all passing. Full regression: **6,232 passed, 0 failed** (6,216 baseline from end of Phase 20.5 + 16 new). Protection hashes for `bujji/mic_v0/engine.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/mic_v0_validation/intraday_validation.py`, `bujji/execution_backtest/driver.py`, `bujji/execution_profiles/profiles.py`, `bujji/position_lifecycle/{paper_bridge,pnl}.py`, `bujji/market_timeseries/indicators.py`, `bujji/epistemics/uncertainty.py`, and every file in `bujji/strategy_intelligence/` all byte-identical to their state at the end of Phase 20.5 — none were touched. `grep` confirms no file in `bujji/opportunity_intelligence/` references `place_order`, `modify_order`, `cancel_order`, `PaperBroker`, or `FyersBroker`.
