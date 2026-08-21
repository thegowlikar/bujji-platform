# Phase 20.5 — Strategy Intelligence & Ranking Layer

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "When several strategies exist, can Bujji intelligently decide which deserves trust?"** Not a trading decision engine — no order placement, no position sizing, no risk management, no execution, anywhere in this phase.

---

## 1. Repository audit (Step 1, mandatory, done before any code was written)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.epistemics.uncertainty` (Phase 16C/16D) — `Uncertainty`, `Input`, `compose()`, `demote()`, `cap()`, confidence bands HIGH/MODERATE/LOW/NONE | **A) Reusable directly** | The codebase's own canonical, domain-agnostic confidence-composition engine — its own docstring states its purpose is exactly this: giving every derived belief an explainable, composed confidence instead of each package inventing its own scale. **Reused unmodified** as the entire confidence mechanism of this phase — no second confidence model was built. |
| `bujji.msi_performance_analytics.engine._drawdown_and_streaks` | Already reused in Phase 20.4 | Not re-audited; already disclosed there. |
| `bujji.msi_strategy_selector.engine.score_candidate`/`CandidateScore` (Series ~88) | **B) Wrong domain** | Categorical match-count scoring (preferred/acceptable/forbidden weighted overlap) against the options-MSI market-state taxonomy — explicitly "never a numeric fit to outcomes," designed to select among *option strategy families* by *current state fit*, not to rank *backtested futures research candidates* by *historical evidence*. Not reused — wrong upstream, wrong purpose. Its design precedent (disclosed, fixed weights; never a black-box fit) informed this phase's own scoring choices, but no code was reused. |
| `bujji.msi_performance_analytics.models.MetricEstimate`/`ReliabilityNote` (Series 101) | **B) Wrong domain (partially)** | Sample-size + reliability + confidence-interval pairing — a reasonable shape, but tightly coupled to that package's own Wilson-interval machinery and `ShadowPosition`/`DecisionRecord` types. `bujji.epistemics.uncertainty` is the project's own designated canonical replacement for exactly this kind of ad hoc confidence pairing (its own docstring: "20 packages define their own confidence constants... this module supplies the missing algebra"). Confidence composition in this phase uses `epistemics`, not this. |
| Any other scoring/ranking/allocation logic (`msi_dynamic_management`, `market_microstructure`, `ops/alerts`, taxonomy files across `msi_*` packages) | **C) Duplicate — none found; vocabulary hits only** | Broad grep for `score`/`rank` surfaced ~24 files; direct inspection beyond the two above found only taxonomy/vocabulary usages of the words "score"/"rank" (e.g. enum members, docstrings), no second scoring *engine*. |
| Portfolio allocation, Sharpe/Kelly-style risk-adjusted sizing | **Not found anywhere** | Confirmed by direct grep (`sharpe`, `kelly`, `risk.?adjusted`, `portfolio.?alloc`) — the hits that exist are in `risk_brain`/`capital_brain` (margin/capital-adequacy risk, unrelated to strategy scoring) and `msi_performance_analytics` (already covered above). Out of scope regardless — this phase does not allocate capital. |

**Conclusion: the only real reuse target was `epistemics.uncertainty`, and it is used as the entire confidence backbone of this phase.** No scoring or ranking engine existed to duplicate.

## 2. Architecture

```
bujji/strategy_intelligence/
    __init__.py
    models.py    -- StrategyEvidence, MarketContext, StrategyScore, StrategyRankingReport
    scoring.py   -- score_strategy()
    ranking.py   -- rank_strategies()
```

Four files, ~260 lines total. `StrategyEvidence` is deliberately shaped to match what `bujji.strategy_research.stats.PerformanceStats` (Phase 20.4) already produces, but this package accepts plain evidence records rather than importing Phase 20.3/20.4's research machinery — it has no dependency on execution, MIC classification, or broker code of any kind, confirmed by test (`test_no_order_placement...`-style grep, extended in this phase's own tests).

```
Strategy Candidates  →  Strategy Intelligence Layer  →  Ranked Opportunity Set  →  [Risk Governor]  →  [Execution]
```

This phase builds only the middle box. The last two remain out of scope, unbuilt, and unimported.

## 3. Scoring model

**`evidence_score` (0–100), computed ONLY from `StrategyEvidence`, three components:**

- **Edge (0–40):** zero whenever `net_expectancy <= 0` — a losing or breakeven strategy earns no edge credit regardless of profit factor or win rate in isolation. Otherwise, `min(profit_factor / 2.0, 1) * 20 + min(win_rate / 0.55, 1) * 20`. The reference points (profit factor 2.0, win rate 0.55) are round, pre-registered "solid edge" levels chosen before this phase's real-data run — not fit to Phase 20.4's specific 2.30/0.665 numbers.
- **Execution Robustness (0–25):** zero whenever not net-positive, or gross was never positive. Otherwise `25 * (1 - drag_pct)` where `drag_pct = (gross_expectancy - net_expectancy) / gross_expectancy` — Phase 20.2's own gross-vs-net distinction, reused as this component's entire basis.
- **Stability (0–25):** `25 * (periods_positive / periods_known)` over Phase 20.4's train/validation/out-of-sample expectancies — full credit for consistency, proportional penalty for any period turning negative, missing periods excluded from the denominator rather than penalized.

**Confidence**, composed via `epistemics.compose()` (reused, unmodified) from one critical input — sample size, tiered at round statistical thresholds (≥500 HIGH, ≥100 MODERATE, ≥20 LOW, <20 → `epistemics.insufficient()` → NONE, absorbing). **MIC context is applied strictly afterward**, via `epistemics.demote()` (reused, unmodified): the current regime being outside the strategy's disclosed favorable set, or simply unvalidated, demotes confidence by one band — it can never promote confidence above the sample-size-based level, and it is never consulted by `evidence_score` or any of its three components. This is the direct, disclosed implementation of this phase's own instruction: *"MIC modifies confidence... not: MIC says TREND = trade."*

**`effective_score`** — what ranking sorts by — is `evidence_score` capped at a confidence-dependent ceiling (HIGH 100 / MODERATE 70 / LOW 45 / NONE 30). This is the mechanism that prevents a small-sample high-apparent-return strategy from outranking a large-sample stable one, proven by test (§ below), not merely asserted.

## 4. Strategy ranking (real data, Phase 20.4's own published findings — no strategy discovery rerun)

**No MIC context (evidence-only baseline):**

```
1. FAMILY_A_TREND_FOLLOWING
   Score: 79/100  (edge=40, execution=14, stability=25)
   Confidence: HIGH
   Evidence: n=11278, win_rate=0.665, profit_factor=2.3, net_expectancy=517.0, gross_expectancy=949.0

2. FAMILY_B_MEAN_REVERSION
   Score: 0/100  (edge=0, execution=0, stability=0)
   Confidence: MODERATE (limiting factor: sample_size)
   Evidence: n=150, win_rate=0.0, profit_factor=0.0, net_expectancy=-1664.0, gross_expectancy=-1218.0
```

**With MIC context = TRANSITION (unfavorable/unvalidated for both):**

```
1. FAMILY_A_TREND_FOLLOWING — Score: 70/100, Confidence: MODERATE (demoted from HIGH)
2. FAMILY_B_MEAN_REVERSION  — Score: 0/100,  Confidence: LOW (demoted from MODERATE)
```

**Direct proof of MIC independence** (`scripts/run_phase20_5_ranking.py` output): Trend Following's `evidence_score` is **78.62 in all three context scenarios tested** (none / favorable / transition) — only `confidence` moved (HIGH → HIGH → MODERATE).

## 5. Evidence quality

Mean Reversion scores **0/100** not because its confidence is low, but because `evidence_score` itself is zero — `net_expectancy = -1664.0` fails the edge/execution components' `> 0` gate outright. Its confidence (MODERATE at n=150, no MIC context) is *appropriately* not NONE — 150 real trades is a real, if thin, sample, and the model does not need to distrust the sample to correctly rank a confirmed-losing strategy last; the zero score alone does that. This is a deliberate design property: confidence answers "how much do we trust this number," not "is this number good" — those are different questions, and conflating them was the exact "small sample, high apparent return" trap this phase's spec named.

## 6. Limitations

1. **Two strategies is a thin ranking set.** Everything here is provably correct on the *ordering logic*; it has not been exercised against many simultaneous candidates with more varied evidence shapes (partial period data, ties, extreme outliers beyond the two real ones available).
2. **The edge/execution reference constants (profit factor 2.0, win rate 0.55) are disclosed, round, and pre-registered — but still a judgment call**, not derived from any formal statistical procedure. A different reasonable analyst could pick different round numbers; this phase does not claim these are uniquely correct, only that they were not fit to Phase 20.4's specific results.
3. **`MarketContext.favorable_regimes`/`unfavorable_regimes` must be supplied by the caller, honestly, from real research** (as Phase 20.4's own findings do: Trend Following was only ever validated in TREND_UP/TREND_DOWN windows). This package has no way to verify the caller supplied these truthfully — it is a contract, not a checked invariant.
4. **Confidence-band sample-size thresholds (20/100/500) are ordinary statistical rules of thumb**, not derived from this project's specific data's autocorrelation structure (Phase 20.4 §9 already disclosed that Trend Following's 11,278 trades are not independent samples — this phase's confidence model does not additionally account for that; it treats raw trade count as the sample-size input, same simplification Phase 20.4 itself made).
5. **This phase does not know how to combine a ranking with a portfolio decision** — deliberately; that is explicitly out of scope.

## 7. Future integration points

- **Risk Governor** (not built): would consume `StrategyRankingReport` and decide *whether* and *how much* capital a ranked, sufficiently-confident strategy is allowed — this phase produces the ranking; it does not gate or size anything.
- **Live MIC feed**: `MarketContext.mic_regime` is currently supplied manually per call in the validation script; a live integration would read the current regime from `bujji.mic_v0` (unmodified) each cycle and re-score, but no such wiring exists yet — deliberately, per this phase's own scope boundary.
- **Additional strategy candidates**: as Cycle 2 (or later Cycle-1 iterations, e.g. a redesigned `generate_signal` per Phase 20.4's own "next recommendation") produce new validated `StrategyEvidence`, they plug into this same `score_strategy`/`rank_strategies` pair with zero changes to this package.

---

## Testing & regression

17 new tests (ranking correctness: 3, no-overfitting: 2, MIC independence: 4, execution awareness: 3, stability: 4, real-finding reproduction: 1), all passing. Full regression: **6,216 passed, 0 failed** (6,199 baseline from end of Phase 20.4 + 17 new). Protection hashes for `bujji/mic_v0/engine.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/mic_v0_validation/intraday_validation.py`, `bujji/execution_backtest/driver.py`, `bujji/execution_profiles/profiles.py`, `bujji/position_lifecycle/{paper_bridge,pnl}.py`, `bujji/market_timeseries/indicators.py` all byte-identical to their state at the end of Phase 20.4 — none were touched. `grep` confirms no file in `bujji/strategy_intelligence/` references `PaperBroker` or `place_order`.
