# Phase 20.7 — Opportunity Ranking Engine

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Among today's qualified opportunities, which deserves priority?"** Not capital allocation, not position sizing, not portfolio optimization, not execution — no trade-sizing, margin, or order/broker vocabulary exists anywhere in this phase's code.

---

## 1. Repository audit (Step 1, mandatory, done before any code was written)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.msi_dynamic_management.query.priority_rank`/`highest_priority_decision` (Series 109) | **B) Wrong domain** | Ranks **six position-management actions** (strike roll, expiry roll, delta rebalance, wing adjustment, strategy conversion, full exit) for a single already-open options position — a completely different ranking problem (managing one position's next action, not prioritizing across many opportunities). Not reused. Its `priority_rank` dict + `max(..., key=...)` tie-break pattern is a reasonable small precedent, already independently arrived at in Phase 20.5's own `rank_strategies`. |
| `bujji.strategy_intelligence.ranking.rank_strategies` (Phase 20.5, this engagement) | **A) Reusable pattern, not reusable as-is** | Sorts raw `StrategyScore` by `effective_score` — no concept of qualification state, so it cannot express "BLOCKED/INSUFFICIENT_EVIDENCE must never rank," which this phase's Rules 1–2 require structurally. A new, small `rank_opportunities()` was built to consume `OpportunityAssessment` (Phase 20.6) instead — not a duplicate of Phase 20.5's function, since it operates on a materially different input and adds exclusion semantics that function was never designed to express. |
| `bujji.trading_brain.risk_governor.*` (`RiskActionRecommendation`, `AdaptiveRiskRecommendation`, `PositionSizeRecommendation`) | **Out of scope** | Capital/position-sizing recommendations — explicitly excluded from this phase's boundary regardless of domain fit. |
| `decision_coverage.recommend` | **Unrelated** | Test-coverage-dashboard recommendation text, not a trading concept. |
| Any other "priority"/"rank"/"recommend" hit across the codebase | **No duplicate found** | Confirmed by direct inspection of every remaining grep hit; the rest are docstring/comment usages of the words, not ranking engines. |

**Conclusion:** no opportunity-ranking engine existed to duplicate. The one candidate close enough to be worth naming (Phase 20.5's own `rank_strategies`) was deliberately not reused as-is, because reusing it would have required either bolting qualification-awareness onto a function whose contract never anticipated it, or silently dropping Rules 1–2 — both worse than a small, purpose-built function.

## 2. Architecture

```
bujji/opportunity_ranking/
    __init__.py
    models.py    -- OpportunityCandidate, RankedOpportunity, ExcludedOpportunity, RankingResult
    scorer.py     -- rank_opportunities()
    explain.py    -- build_reasons(), build_penalties(), explain_ranking()
```

Four files, ~200 lines total.

```
Strategy Intelligence (20.5, StrategyScore)
        ↓
Opportunity Qualification (20.6, OpportunityAssessment: ELIGIBLE/WATCH/BLOCKED/INSUFFICIENT_EVIDENCE)
        ↓
Opportunity Ranking (20.7, this phase)  →  Priority Order
```

`OpportunityCandidate` is a thin wrapper around Phase 20.6's `OpportunityAssessment` — no field is duplicated or recalculated; `rank_opportunities()` reads `strategy_score.effective_score` and `decision.state` and nothing else.

## 3. Ranking model

**Rule 1 & 2 (structural, not a rule the scorer has to remember):** any candidate whose qualification state is `BLOCKED` or `INSUFFICIENT_EVIDENCE` is routed straight to `excluded` — it never enters the sortable set, never receives a `priority_score`, never has an ordering position relative to other excluded candidates.

**Rule 3 (qualification modifies priority):** included candidates (`ELIGIBLE`/`WATCH`) get `priority_score = effective_score * qualification_weight`, where `qualification_weight` is **1.0 for ELIGIBLE, 0.6 for WATCH** — a disclosed, round, bounded multiplier, never above 1.0.

**Rule 4 (evidence remains dominant), enforced by construction, not by a separate check:** because `qualification_weight <= 1.0` always, qualification can only ever *shrink* `effective_score`, never grow it — a strategy cannot out-rank another strategy with a large evidence lead purely by having better current market fit. This is why Rule 4 needed no additional logic: it falls out of the formula's own shape.

Priority tiers (`HIGH`/`MEDIUM`/`LOW`) are friendly labels derived from `priority_score` bands (≥60 HIGH, ≥30 MEDIUM, else LOW) — display only; ordering is by the numeric score.

## 4. Decision examples (real evidence, Phase 20.5/20.6)

| Scenario | Trend Following | Mean Reversion |
|---|---|---|
| TREND_UP, all NORMAL | **Rank 1, HIGH, score 79** | Excluded — INSUFFICIENT_EVIDENCE |
| TRANSITION | **Rank 1, MEDIUM, score 47** (WATCH discount applied) | Excluded — INSUFFICIENT_EVIDENCE |
| EXTREME risk | Excluded — BLOCKED (EXTREME_RISK) | Excluded — INSUFFICIENT_EVIDENCE |

In the EXTREME-risk scenario, **zero opportunities rank** — `NO QUALIFIED OPPORTUNITY` is the correct, produced-without-hesitation output, matching this phase's own instruction not to optimize for always producing a trade.

## 5. Real-data validation (`scripts/run_phase20_7_ranking.py` — no strategy discovery, optimization, or backtesting rerun; evidence taken verbatim from Phase 20.5/20.6)

Full rendered output, three scenarios, confirms every rule directly:
- **TREND_UP scenario:** Trend Following ranks alone at HIGH/79; Mean Reversion excluded with its Phase 20.4 FAIL evidence cited verbatim as the reason.
- **TRANSITION scenario:** Trend Following's rank survives, but its `priority` drops HIGH→MEDIUM and `priority_score` drops 79→47 (the WATCH 0.6× discount, `78.62 × 0.6 = 47.17`, rounded) — the qualification-modifies-priority rule visible in real numbers, not just asserted.
- **EXTREME-risk scenario:** both strategies excluded, `result.ranked` is the empty tuple — the engine's own explicit `NO QUALIFIED OPPORTUNITY` case.
- **Explicit proof line, logged directly:** `Trend Following evidence_score: 78.62 (identical in every scenario above)` — the underlying evidence score never moved across all three scenarios; only `priority_score` (which depends on qualification too) changed.

## 6. Limitations

1. **The WATCH discount (0.6) and priority-tier thresholds (60/30) are disclosed, round, pre-registered reference points**, not derived from any formal procedure — the same honestly-disclosed judgment-call limitation already carried forward from Phase 20.5/20.6's own constants.
2. **Only two strategies were available to validate the ranking against** — the ordering logic is exercised across three environment scenarios, not against a larger or more varied opportunity set.
3. **`priority_score` is a single scalar** — it cannot currently express *why* two candidates are close versus far apart beyond the raw number difference; `explain_ranking()`'s "ahead by N points" framing is the only comparative signal produced.
4. **No correlation or overlap awareness** — if two ranked opportunities happened to be highly correlated (e.g. both trend-following variants), this package has no way to know or say so; that concern belongs to a future portfolio layer, not this one.
5. **Stateless, as designed** — like Phase 20.6, this package has no memory across calls; it cannot detect a candidate flapping between ranks cycle to cycle.

## 7. Future capital allocation integration

This phase deliberately stops at priority order. A future Capital Allocation / Position Sizing layer would consume `RankingResult.ranked` (already sorted, already excluding anything untrustworthy or blocked) and decide *how much* — this package has no opinion on that question and contains no arithmetic that could answer it. The natural extension point is `RankedOpportunity.priority`/`priority_score` as an input signal to a sizing function that this phase does not build, define the shape of, or import anything in anticipation of.

---

## Testing & regression

15 new tests (evidence dominance: 2, qualification enforcement: 2, insufficient-evidence enforcement: 2, ranking stability: 3, explainability: 4, safety boundary: 2), all passing. Full regression: **6,247 passed, 0 failed** (6,232 baseline from end of Phase 20.6 + 15 new). Protection hashes for `bujji/mic_v0/engine.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/execution_reality/` (unchanged, never imported), `bujji/execution_backtest/driver.py`, `bujji/execution_profiles/profiles.py`, `bujji/strategy_intelligence/*.py`, `bujji/opportunity_intelligence/*.py`, and every other previously-verified file all byte-identical to their state at the end of Phase 20.6 — none were touched. `grep` confirms no file in `bujji/opportunity_ranking/` references `place_order`, `modify_order`, `cancel_order`, `PaperBroker`, `FyersBroker`, or any trade-sizing vocabulary.
