# Phase 20.15.1 — Market Memory Read Layer

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Closes the Interface Map's Memory → Strategy Intelligence arrow: "How should previous similar situations influence our confidence?"

---

## 1. Audit findings

| Component | Classification | Disposition |
|---|---|---|
| `bujji.epistemics.uncertainty` (Phase 16C) — `demote()`, `rank()`, `cap()`, the `HIGH/MODERATE/LOW/NONE` vocabulary | **A) Reusable directly** | Reused unmodified. This module exposes no `promote()` (never needed before this phase) — `evaluator.py`'s `_promote_one_band()` is a small, disclosed, symmetric mirror of `demote()`'s own logic, not a private-symbol import. |
| `bujji.strategy_intelligence.scoring._apply_mic_context()` (Phase 20.5) | **B) Reusable PATTERN only** | The only prior confidence-modifying precedent in Cycle 1 — one-band, bounded, disclosed, never touches `evidence_score`. Its exact discipline is mirrored in `evaluator.py`; the function itself is never imported or modified — this package composes around `StrategyScore`'s own already-computed `confidence`. |
| `bujji.market_memory.{retrieval,models}` (Phase 20.15) | **A) Reusable directly** | `MemoryContext`, `OutcomeMemoryRecord`, `find_similar_conditions_as_of()`. This package is their sole consumer; storage/retrieval is never reimplemented here. |
| `bujji.trading_brain.risk_governor.adaptive_risk_governor`/`adaptive_risk_recommendation` | **C) Wrong domain** | Real capital-risk decisioning, MSI/Trading Brain lineage. Not imported. |
| `bujji.market_phenomena`/`bujji.market_state_graph` (Phase 19.7/19.8) | **C) Wrong lineage** | Real phenomenon/state-graph memory over the older Phase 19.x lineage identity — the same disclosed non-reuse precedent every Cycle-1 phase since 20.10 has already established. Not imported. |

**Design correction made mid-phase**: interpretation logic was initially drafted inside `bujji/market_memory/` itself, then relocated to a new, separate `bujji/memory_intelligence/` package per the explicit "keep storage separate from interpretation" instruction — `market_memory` remains pure storage/retrieval, unmodified from its Phase 20.15 state.

## 2. Files created

- `bujji/memory_intelligence/__init__.py`, `models.py`, `evaluator.py`, `integration.py`, `explain.py`
- `tests/test_memory_intelligence.py` (10 tests)
- `scripts/run_phase20_15_1_memory_intelligence_validation.py`

**No files modified.** `bujji/market_memory/` is byte-identical to its Phase 20.15 state; `bujji.epistemics.uncertainty`, `bujji.strategy_intelligence.scoring`, `bujji.decision_orchestration`, `bujji.mic_v0` all confirmed untouched by mtime.

## 3. Architecture placement

```
Decision Brain (StrategyScore, Phase 20.5)
        ↓
Memory Context (MemoryContext, Phase 20.15's own retrieval.py)
        ↓
Confidence Adjustment (memory_intelligence.apply_memory_influence — THIS PHASE)
        ↓
Decision Brain continues (caller decides whether/how to use the assessment; FinalDecision itself is never touched)
```

`integration.py`'s `apply_memory_influence()` is the single entry point — reads only `score.strategy_name`/`score.confidence`, never `evidence_score`/`effective_score`. A caller that never calls it gets exactly Phase 20.10's own unmodified `FinalDecision`; this package is purely additive, composition-only.

## 4. Memory influence rules (fixed order)

1. **No memory** (`similar_count == 0`) → `UNCHANGED`, flagged `NO_MEMORY_AVAILABLE`.
2. **Too few similar conditions** (< 3) → `UNCHANGED`, honest insufficiency.
3. **Too few known outcomes** (< 3) → `UNCHANGED`, never guessed.
4. **Favorable ratio > 50%** → `SUPPORTED`: confidence raised exactly one band (`_promote_one_band`, capped at `HIGH`), `evidence_score` untouched.
5. **Unfavorable ratio > 50%** → `CONTRADICTED`: confidence demoted exactly one band (`epi.demote`, reused unmodified), `evidence_score` untouched.
6. **Roughly balanced** → `UNCHANGED`, flagged `CONTRADICTORY_HISTORICAL_OUTCOMES` — uncertainty surfaced honestly rather than guessed in either direction.

## 5. Tests (10, all passing)

1. Memory unavailable honesty — no fabricated adjustment
2. Evidence preservation — `evidence_score` identical before/after (78.62 == 78.62)
3. Qualification preservation — a real `BLOCKED` `FinalDecision` (EXTREME risk) stays `BLOCKED` regardless of maximally favorable memory
4. Strategy independence — `MemoryInfluenceAssessment` carries no `priority_score`/`allocation_class`/`evidence_score`/`effective_score` field at all
5. Negative memory effect — 4/4 unfavorable outcomes → `CONTRADICTED`, demoted exactly one band
6. Positive memory effect — 4/4 favorable outcomes → `SUPPORTED`, raised exactly one band, capped at `HIGH`
7. No-lookahead / honest insufficiency — fewer known outcomes than the threshold never guesses a direction
8. Explainability — every assessment carries a non-empty `explanation`, strategy name present in rendered text
9. Safety boundary — two tests: forbidden call patterns, forbidden broker/decision-orchestration/MIC modification

## 6. Before/after validation (real data)

`scripts/run_phase20_15_1_memory_intelligence_validation.py`, real data, the same 9 real trading days (2026-08-01–08-13) validated throughout Phases 20.11–20.15, reusing Phase 20.5's own published evidence verbatim. Built **8 real `KNOWN` outcome memories** by linking each real day's `MarketMemoryRecord` to the real next day's.

**Scenario A — no memory:**
```
Base confidence: HIGH  evidence_score: 78.62
No memory adjustment applied -- no similar historical conditions exist in memory.
```

**Scenario B — real memory available:**
```
Confidence support: 5/5 similar historical conditions showed favorable outcomes
(regime held) -- confidence raised one band, evidence_score unchanged.
Historical outcome summary: {'favorable': 5, 'unfavorable': 0}
```

**Proof, on real data:** `evidence_score` logged and asserted identical across both scenarios (`78.62 == 78.62 == 78.62` recomputed) — confidence moved (or, here, stayed capped at `HIGH` since it started there — a correct, honest outcome, not a test artifact), the score never did.

## 7. Regression

Full regression: **6,355 passed, 0 failed** (6,345 baseline from Phase 20.15 + 10 new).

## 8. Safety boundary confirmation

`grep` confirms zero forbidden call patterns, zero broker/capital imports anywhere in `bujji/memory_intelligence/`. No `compose_decision(`, `score_strategy(` definition, or `compose_market_state(` call exists anywhere in the package — confirmed by direct inspection excluding disclosure prose (two test-assertion bugs, not source bugs, were fixed to check actual call syntax rather than docstring mentions of the same function names, matching the established pattern from every prior phase's own safety tests).

## 9. Remaining roadmap status

```
Market → Observation → Intelligence → Decision → Shadow Result → Memory → Improved Intelligence
                                                                    ✅         ✅ (this phase)
```

**The full loop is now real, on both the write and read sides.** Bujji can persist real shadow decisions as memory, retrieve real similar historical conditions, and let that real history raise or lower confidence by exactly one disclosed band — never touching evidence, qualification, ranking, or allocation. The next roadmap step (Phase 20.16, evidence-gated strategy expansion) can now proceed with a working memory layer already informing confidence, exactly as the roadmap's own sequencing intended.
