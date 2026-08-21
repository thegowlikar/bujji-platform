# Phase 20.8 — Capital Allocation Intelligence Layer

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Question answered: "Given multiple qualified opportunities and limited risk capacity, how much risk allocation does each deserve?"** A recommendation *class*, never a quantity — no lot calculation, contract selection, margin allocation, stop-loss placement, or broker integration exists anywhere in this phase's code.

---

## 1. Repository audit (Step 1, mandatory, done before any code was written)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.trading_brain.risk_governor.risk_budget_governor` (Gate D.3) | **C) Wrong domain** | A real, dynamic sizing engine driven by **actual** `CapitalSafetySnapshot`/`PortfolioRiskSnapshot` (real margin, real drawdown, real account-level risk from Gates B/C/D.1/D.2). Requires live broker/account state this phase never has and never wants. Its own docstring: *"this module places no trade, calls no broker, performs no automatic sizing... output is a RECOMMENDATION downstream systems MAY use later"* — that exact posture is followed here by design, without importing anything. |
| `bujji.trading_brain.risk_governor.capital_safety_governor` (Gate D.1), `portfolio_risk_aggregator` (D.2), `adaptive_risk_governor`, `adaptive_risk_recommendation`, `defined_risk` | **C) Wrong domain** | All require real capital/margin/broker state (funds, margin responses, live positions). Not reused. |
| `bujji.capital.engine.CapitalManagementEngine` | **C) Wrong domain, explicitly out of scope regardless** | `approve_trade()` → `approved_lots` — a real, broker-coupled trade-sizing approval system. Exactly the kind of component this phase is forbidden from building or touching. |
| `bujji.trading_brain.risk_brain`, `bujji.trading_brain.capital_brain` | **Not applicable** | Both explicitly disclaim, in their own docstrings, precisely what this phase also avoids: *"no PnL, no Kelly/Sharpe/Sortino, no Monte Carlo."* Confirms no Kelly-criterion or risk-parity implementation exists anywhere in the codebase (direct grep, zero real hits beyond these two disclaimers). |
| `bujji.epistemics.uncertainty` (Phase 16C/16D) | **A) Reusable directly** | `HIGH`/`MODERATE`/`LOW` confidence constants reused unmodified as the keys for this phase's confidence-cap rule (Rule 2) — no second confidence vocabulary introduced. |
| `bujji.opportunity_ranking.OpportunityCandidate`/`RankedOpportunity`/`RankingResult` (Phase 20.7) | **A) Reusable directly** | `priority_score` is read, never recomputed — the base term this phase's tier thresholds are built from. |

**Conclusion:** the codebase's real capital/risk system is large, mature, and entirely the wrong tool for this phase — it answers "is this actual trade/account safe," not "how much *trust* does this *research-stage* opportunity deserve." No part of it was reused as code; its "recommendation only, never an action" discipline was followed as precedent.

## 2. Architecture

```
bujji/capital_intelligence/
    __init__.py
    models.py     -- RiskAllocationClass vocabulary, AllocationAssessment
    allocator.py   -- assess_risk_allocation(), assess_ranking_result()
    explain.py     -- build_reasons(), build_penalties(), exclusion_reason(), explain_allocation()
```

Four files, ~230 lines total.

```
Strategy Intelligence (20.5) → Opportunity Qualification (20.6) → Opportunity Ranking (20.7) → Capital Allocation Intelligence (20.8, this phase)
```

`assess_risk_allocation()` takes an `OpportunityCandidate` (Phase 20.7's own input type) plus that candidate's already-computed `priority_score`/`rank` when available — every field it reads was computed by an earlier phase; none is recalculated here.

## 3. Allocation model

**`RiskAllocationClass`** — five plain string constants (following this codebase's own established convention, the same pattern `mic_v0.models` and `epistemics.uncertainty` both already use): `MAXIMUM > NORMAL > REDUCED > MINIMAL > NONE`.

**Rule 3 (hard gate), checked first:** `BLOCKED` or `INSUFFICIENT_EVIDENCE` → `NONE`, unconditionally, citing Phase 20.6's own recorded reason verbatim.

**Base tier**, from Phase 20.7's `priority_score` (already evidence + qualification-weighted):
- ≥85 → MAXIMUM
- ≥60 → NORMAL
- ≥35 → REDUCED
- else → MINIMAL

**Deliberate calibration disclosure:** the MAXIMUM threshold (85) is set *above* Trend Following's own real score (78.62/NORMAL) — no Cycle-1 strategy has yet earned the top tier. This is intentional, not an oversight: Phase 20.4 itself already concluded *"neither family is validated as a real strategy yet"* in the fullest sense; reserving MAXIMUM for evidence this Cycle hasn't produced keeps that honesty visible in the allocation output too, rather than letting a single validated signal look more conclusive than it is.

**Rule 2 (confidence caps allocation):** `HIGH` confidence imposes no cap; `MODERATE` caps at `NORMAL`; `LOW` caps at `REDUCED` — regardless of how high the raw tier would otherwise be.

**Rule 4 (execution reality):** `STRESS`/`EXTREME` execution profile demotes the resulting tier by one further step (`demote_allocation()`, a local mirror of `epistemics.demote()`'s own pattern, applied to this phase's differently-shaped 5-value vocabulary).

**Rule 1 (evidence dominance) and Rule 5 (MIC modifies risk, never creates edge)** are not separate checks — they hold *by construction*: the base tier is built entirely from `priority_score`/`effective_score`/`confidence` (all Phase 20.5/20.7 evidence-based, MIC-independent fields); Rules 2 and 4 only ever *shrink* the tier, never grow it, so neither a favorable regime nor a lucky qualification match can lift a weak-evidence strategy above a strong one.

## 4. Decision examples (real evidence, Phase 20.5/20.6/20.7)

| Scenario | Trend Following | Mean Reversion |
|---|---|---|
| TREND_UP, all NORMAL | **NORMAL** — validated edge, HIGH confidence, market compatible, no execution penalty | **NONE** — evidence insufficient |
| TRANSITION | **REDUCED** — WATCH discount applied | **NONE** |
| EXTREME risk | **NONE** — BLOCKED (EXTREME_RISK) | **NONE** |
| STRESS execution | **MINIMAL** — WATCH discount (execution not NORMAL) *and* the Rule 4 stress penalty compound | **NONE** |
| EXTREME execution | **MINIMAL** — same compounding as STRESS | **NONE** |

## 5. Real-data validation (`scripts/run_phase20_8_allocation.py` — no strategy discovery or optimization rerun; evidence taken verbatim from Phase 20.5/20.6/20.7)

Full rendered output across five scenarios (§4 table, generated directly from real Phase 20.4/20.5 numbers) confirms every rule on real data, not only in the unit test suite. The explicit proof line, logged directly: `Trend Following evidence_score: 78.62` and `Mean Reversion evidence_score: 0.0` — **identical across all five scenarios** — while `allocation_class` moved from `NORMAL` down to `NONE`/`MINIMAL` depending on the environment. Mean Reversion's `NONE` reason is reproduced verbatim from Phase 20.6 in every scenario: *"Strategy evidence insufficient... market conditions cannot override failed validation."*

**A finding worth naming explicitly:** under STRESS/EXTREME execution, Trend Following drops from REDUCED to MINIMAL rather than the naively-expected single tier — because Phase 20.6's own qualification rule *already* discounts `priority_score` for any non-NORMAL execution profile (ELIGIBLE→WATCH), and this phase's Rule 4 then demotes the *already-discounted* tier once more. Both effects are individually correct; they compound because they operate at different layers (qualification vs. allocation) that were never designed to cancel each other out. Disclosed here rather than smoothed over — an earlier version of this phase's own test suite wrongly assumed a single-tier drop and had to be corrected once the real compounding was observed.

## 6. Limitations

1. **The tier thresholds (85/60/35) and the confidence-cap/execution-demotion rules are disclosed, round, pre-registered reference points**, not derived from a formal procedure — the same honestly-disclosed judgment-call limitation already carried forward from every scoring/ranking layer in this Cycle.
2. **Only two strategies were available to validate against**, across five scenarios — not a large or varied opportunity population.
3. **The STRESS/EXTREME compounding (§5) means the allocation class is not simply "priority_score, re-bucketed"** — a reader comparing `priority_score` directly to `allocation_class` across scenarios needs to know both the qualification-level and allocation-level rules are contributing, not just one.
4. **No portfolio-level risk-budget total exists yet** — this phase classifies *per opportunity*, in isolation; it has no concept of "total risk capacity already committed elsewhere" and cannot yet answer "how many MAXIMUM-class opportunities can coexist."
5. **Stateless, as designed** — like Phases 20.6/20.7, no memory across calls; cannot detect a strategy's allocation class flapping cycle to cycle.

## 7. Future integration path

This phase deliberately stops at a trust-and-risk *class*, never a number. A future Position Sizing / Execution layer would consume `AllocationAssessment.allocation_class` (and its `priority_score`/`rank` provenance) as ONE input among several real ones (actual account capital, actual margin, actual portfolio exposure — all owned by the existing `bujji.trading_brain.risk_governor`/`bujji.capital` systems, §1) to decide an actual quantity. This package contains no arithmetic that could answer that question and imports nothing in anticipation of it. Per the roadmap's own final line: **"Execution comes only after the intelligence stack survives shadow validation"** — this phase extends that stack; it does not shortcut past the validation it's still waiting on.

---

## Testing & regression

17 new tests (evidence dominance: 2, qualification gate: 2, confidence cap: 2, MIC independence: 2, execution impact: 3, explainability: 3, safety boundary: 2, ranking-result integration: 1), all passing. Full regression: **6,264 passed, 0 failed** (6,247 baseline from end of Phase 20.7 + 17 new). Protection hashes for `bujji/mic_v0/engine.py`, `bujji/intelligence/regime_brain.py`, `bujji/broker/paper.py`, `bujji/execution_backtest/driver.py`, `bujji/execution_profiles/profiles.py`, `bujji/strategy_intelligence/*.py`, `bujji/opportunity_intelligence/*.py`, `bujji/opportunity_ranking/*.py`, and every other previously-verified file all byte-identical to their state at the end of Phase 20.7 — none were touched. `grep` confirms no file in `bujji/capital_intelligence/` references `place_order`, `modify_order`, `cancel_order`, `PaperBroker`, `FyersBroker`, or any lot/margin/stop-loss/sizing vocabulary.
