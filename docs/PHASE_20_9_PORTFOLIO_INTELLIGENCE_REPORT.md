# Phase 20.9 — Portfolio Intelligence & Multi-Opportunity Conflict Resolution

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "What happens when multiple qualified opportunities compete for limited portfolio attention?"** Portfolio *preference and conflict resolution* only — no quantity, capital amount, margin figure, rebalancing, order routing, or position creation exists anywhere in this phase's code.

---

## 0. Critical naming-collision finding (discovered before any other audit work)

The requested path, `bujji/portfolio_intelligence/`, **already exists** — built in Phase 15M of this same engagement. It is a real, tested, unrelated engine: aggregation of Greek exposure, underlying/expiry concentration, and thesis health across **real, open options positions** (`Dict[position_id, PositionLifecycle]`), with its own `CONFLICT_*` vocabulary (`DIRECTIONAL_CONFLICT`, `UNDERLYING_CONCENTRATION`, `EXPIRY_CONCENTRATION`, `GREEK_CONCENTRATION`, `CORRELATED_EXPOSURE`) scoped to `involved_position_ids`. This phase's subject — pre-trade compatibility between Cycle-1 *strategy opportunities*, never a real position — is a different problem entirely. Overwriting or extending that package in place would conflate two unrelated systems and risk corrupting a real, already-shipped engine.

**Resolution:** this phase's package is named `bujji.opportunity_portfolio` instead, following the identical "disclosed collision, distinct name" precedent already established twice in this engagement — `mic_v0` (vs. the pre-existing "Market Intelligence Core" brains) and `strategy_research` (vs. the pre-existing `msi_strategy_*` series). `bujji/portfolio_intelligence/` was not read for modification, only inspected to confirm the collision and its scope.

## 1. Repository audit (Step 1, mandatory)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.portfolio_intelligence` (Phase 15M) | **C) Wrong domain (see §0)** | Real options-position aggregation, not reused. Its `PortfolioConflictFinding(finding, detail, involved_ids)` record shape is a reasonable structural precedent, not imported. |
| `bujji.trading_brain.risk_governor.portfolio_risk_aggregator` (Gate D.2) | **C) Wrong domain** | Real margin/exposure aggregation across live positions — already classified this way in Phase 20.7/20.8's own audits; confirmed again, not reused. |
| Correlation engine (statistical price correlation) | **Not found anywhere** | Direct grep for `correlat` found zero implementations. Confirms this phase must not invent one — matches its own "no optimizer" boundary. `CorrelationAssessment` (this phase) is therefore explicitly declarative, never statistically computed — see §3. |
| Portfolio optimizer / diversification logic | **Not found anywhere** | Direct grep found nothing beyond this phase's and Phase 20.7's own docstrings disclaiming it. |
| `bujji.strategy_research.ALL_FAMILIES` (Phase 20.3) | **A) Reusable directly** | Each family's own already-declared `market_conditions_required`/`incompatible_conditions` is the **sole** source of truth for whether two strategies expect opposite market behavior — reused unmodified as the entire basis of conflict detection. |
| `bujji.capital_intelligence.AllocationAssessment` (Phase 20.8) | **A) Reusable directly** | Every input (`priority_score`, `rank`, `allocation_class`, and transitively `strategy_score`/`environment`) read from this single type, never recalculated. |

**Conclusion:** no portfolio-conflict or correlation engine existed to duplicate, aside from the naming collision resolved in §0. This phase's only real code reuse is Phase 20.3's own family declarations and Phase 20.8's own allocation assessments.

## 2. Architecture

```
bujji/opportunity_portfolio/
    __init__.py
    models.py     -- COMPATIBLE/NEUTRAL/CONFLICTING/EXCLUSIVE, SELECT_PRIMARY/ALLOW_MULTIPLE/REDUCE_CONFLICT/NO_SELECTION/INSUFFICIENT_EVIDENCE,
                      OpportunityConflict, CorrelationAssessment, PortfolioDecision
    conflict.py    -- evaluate_conflicts()
    portfolio.py   -- rank_portfolio_choices()
    explain.py     -- explain_portfolio_decision()
```

Five files, ~260 lines total.

```
Strategy Intelligence (20.5) → Opportunity Qualification (20.6) → Opportunity Ranking (20.7) → Capital Intelligence (20.8) → Portfolio Intelligence (20.9, this phase)
```

## 3. Conflict model

**`CorrelationAssessment` is declarative, never computed from price data** — the exact discipline the audit (§1) forced: with no correlation engine anywhere in the codebase and this phase forbidden from building an optimizer, "correlation" here means "declared market-assumption overlap," read directly from `bujji.strategy_research.ALL_FAMILIES`. For two strategies A and B: `shared_favorable_regimes = required_A ∩ required_B`; `opposing_regimes = (required_A ∩ incompatible_B) ∪ (required_B ∩ incompatible_A)`.

**Conflict classification:**
- `opposing_regimes` non-empty **and** both candidates currently `ELIGIBLE` → **EXCLUSIVE** ("only one should survive," per the phase's own definition — escalated from CONFLICTING only by real, currently-observed simultaneity, never assumed from the declared sets alone).
- `opposing_regimes` non-empty, not both ELIGIBLE → **CONFLICTING**.
- `shared_favorable_regimes` non-empty, no opposition → **COMPATIBLE**.
- Neither → **NEUTRAL** (includes any strategy not registered in `ALL_FAMILIES` — an honestly empty assessment, never a fabricated overlap).

**Portfolio decision**, from Phase 20.8's own allocation tier and Phase 20.7's own `priority_score` (Rule 1, evidence dominance — reused, never recalculated):
1. Every `NONE`-allocation candidate is excluded outright. If none survive and all were excluded for `INSUFFICIENT_EVIDENCE` → **INSUFFICIENT_EVIDENCE**; otherwise → **NO_SELECTION**.
2. Exactly one survivor → **SELECT_PRIMARY**.
3. Multiple survivors, no CONFLICTING/EXCLUSIVE pair among them → **ALLOW_MULTIPLE** (no forced diversification *and* no forced exclusion — matches the phase's own "sometimes one strong opportunity beats several weak ones" instruction by simply never assuming the reverse either).
4. Any EXCLUSIVE pair → the strongest (by allocation tier, then `priority_score`, then sample size) survives; everything EXCLUSIVE-conflicted with it is cut → **SELECT_PRIMARY**.
5. CONFLICTING pairs present, no EXCLUSIVE pair → **REDUCE_CONFLICT** (real tension flagged, nothing forcibly excluded — the strongest is still named as the reference point).

## 4. Portfolio decisions (worked examples)

| Input | Decision |
|---|---|
| Trend Following (strong) alone | SELECT_PRIMARY |
| Trend Following + Mean Reversion (real, FAILED evidence) | SELECT_PRIMARY (Trend Following) — Mean Reversion excluded before conflict evaluation even runs, since it never clears the evidence-sufficiency gate |
| Trend Following + a synthetic strong, non-opposing "OTHER" strategy | ALLOW_MULTIPLE |
| Only failed evidence supplied | INSUFFICIENT_EVIDENCE |
| No candidates supplied | NO_SELECTION |
| Trend Following + Mean Reversion, both forced `ELIGIBLE` (synthetic test only) | SELECT_PRIMARY, conflict correctly classified EXCLUSIVE |

## 5. Real scenario validation (`scripts/run_phase20_9_portfolio.py` — no strategy discovery or optimization rerun; Trend Following evidence taken verbatim from Phase 20.5)

**Scenario 1 (fully real evidence, TREND_UP regime):** `SELECT_PRIMARY`, primary `FAMILY_A_TREND_FOLLOWING`, Mean Reversion excluded — its real Phase 20.4 FAILED evidence never reaches the conflict-evaluation stage at all, since it's filtered out by the evidence-sufficiency gate first (same discipline Phase 20.6/20.7/20.8 already established).

**Scenario 2, and the honest finding it produced:** attempting to construct a "two real, currently-competing opportunities" scenario using a *hypothetically strong* synthetic Mean Reversion (clearly labeled as synthetic, never presented as Phase 20.4's real result) still resolved to `SELECT_PRIMARY` with Mean Reversion excluded — **not because its evidence was weak this time, but because Cycle-1's own eligibility gates (Phase 20.6, reusing Phase 20.3's declared regime sets) make it structurally impossible for Trend Following and Mean Reversion to BOTH be `ELIGIBLE` under the same real MIC regime.** TREND_UP is favorable for Trend Following and *explicitly unfavorable* (hard-BLOCKED) for Mean Reversion; RANGE is the exact mirror. This means **`EXCLUSIVE` is a real, correctly-implemented, unit-tested code path that genuine Cycle-1 regime data can never actually reach** — disclosed here rather than presented as a limitation discovered later. The mechanism is proven correct by `test_evidence_dominance_in_exclusive_conflict`, which deliberately overrides qualification-time favorable regimes to force both candidates `ELIGIBLE` simultaneously while `evaluate_conflicts` still correctly reads each strategy's *true* registered declaration for the correlation basis.

**Proof line, logged directly:** `Trend Following evidence_score: 78.62 (scenario 1) / 78.62 (scenario 2)` — identical regardless of portfolio outcome, confirming MIC/portfolio-level decisions never touch the underlying evidence.

## 6. Limitations

1. **`EXCLUSIVE` is unreachable from real Cycle-1 data** (§5) — a structural consequence of only two, mutually-exclusive-by-design families existing. This is not a bug; it becomes meaningful the moment a third family with a genuinely overlapping-but-opposed regime set is added.
2. **Conflict detection is entirely name-keyed against `bujji.strategy_research.ALL_FAMILIES`** — a strategy evidence record whose `strategy_name` doesn't match a registered family degrades to `NEUTRAL` (honest, not fabricated), but this means the conflict layer is currently blind to any strategy outside that one registry, by design, not by oversight.
3. **`CorrelationAssessment` is purely declarative** — it says nothing about actual historical price-series correlation between the two strategies' P&L streams, which Step 1's own audit confirmed doesn't exist anywhere in this codebase yet. A future phase building a real correlation engine would be a new capability, not a fix to this one.
4. **No portfolio-level risk budget total** — this phase decides *preference and coexistence*, never *how much of anything* — that remains Phase 20.8's `RiskAllocationClass` per opportunity, unaggregated across a portfolio.
5. **Stateless, as designed** — like every prior phase in this Cycle-1 intelligence stack, no memory across calls.

## 7. Future path

A future Position Sizing / Portfolio Execution layer would consume `PortfolioDecision.kept`/`.primary` (already evidence-dominant, already conflict-resolved) as ONE input, alongside the real capital/margin systems this Cycle deliberately never touches (`bujji.trading_brain.risk_governor`, `bujji.capital`, §1), to decide actual position sizes across a real, multi-position portfolio. This phase contains no arithmetic that could answer that question. Per the roadmap's own standing rule: **execution remains a future phase, only after the full intelligence stack survives shadow validation** — Phase 20.9 extends that stack one layer further; it does not shortcut past the validation still pending.

---

## Testing & regression

14 new tests (evidence dominance: 2, conflict detection: 2, MIC influence boundary: 2, multiple-strategy support: 1, weak-strategy exclusion: 3, explainability: 2, safety boundary: 2), all passing. Full regression: **6,278 passed, 0 failed** (6,264 baseline from end of Phase 20.8 + 14 new). Protection hashes for `bujji/mic_v0/engine.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/execution_reality/` (unchanged, never imported), `bujji/execution_backtest/driver.py`, `bujji/strategy_intelligence/*.py`, `bujji/opportunity_intelligence/*.py`, `bujji/opportunity_ranking/*.py`, `bujji/capital_intelligence/*.py`, and — critically — **`bujji/portfolio_intelligence/` itself** all byte-identical to their state before this phase began; none were touched. `grep` confirms no file in `bujji/opportunity_portfolio/` references `place_order`, `modify_order`, `cancel_order`, `PaperBroker`, `FyersBroker`, or imports `bujji.broker`/`bujji.capital` (the real, forbidden capital/broker systems).
