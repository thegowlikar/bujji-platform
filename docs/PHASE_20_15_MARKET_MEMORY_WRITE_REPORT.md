# Phase 20.15 — Market Memory Write Layer

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Fills the Interface Map's missing stage: **Shadow Result → Market Memory → Future Intelligence.** Memory stores and retrieves evidence only — never a strategy optimizer.

---

## 1. Audit findings

**Naming check:** no `bujji/market_memory/` package existed prior to this phase.

| Component | Classification | Disposition |
|---|---|---|
| `bujji.market_understanding.{memory_models,memory_similarity,memory_query}` (Phase 19.5) | **B) Reusable PATTERN only** | Already solves exactly this shape — deterministic weighted-dimension similarity, explainable scoring, no-look-ahead `as_of_time` querying, honest `NOT_YET_OBSERVED` outcomes. Its own `MarketMemoryEntry` is keyed to `MarketIntelligenceSnapshot.intelligence_snapshot_id` (Phase 19.3's own lineage) over a six-dimension feature space Cycle 1's own three MIC v0 dimensions don't share. The algorithm shape is mirrored in `retrieval.py`; the code itself is not imported. |
| `bujji.market_state_graph.memory` (Phase 19.8) | **C) Wrong lineage** | Extends the same Phase 19.5 lineage further. Not reusable code-wise. |
| `bujji.state_persistence.store.EventStore` (Phase 15B) | **A) Reusable directly** | Genuinely domain-agnostic append-only persistence, already used by multiple independent systems. Used in `store.py` — the third phase in this engagement to need append-only persistence, and the first to reuse the canonical primitive instead of a bespoke JSONL convention. |
| `bujji.market_regime_memory` (Phase 11) | **C) Wrong domain** | Session-scoped regime duration tracking, not cross-session decision/outcome memory. |
| `bujji.outcome_memory` (Phase 15N) | **C) Wrong domain** | Real POSITION-level P&L outcome tracking — Cycle 1 has no positions. |
| `bujji.reality_memory` | **C) Wrong domain** | A data-capture availability catalog, not decision memory. |
| `bujji.trading_brain.risk_governor.adaptive_risk_memory` | **C) Wrong domain** | Real capital risk memory, MSI/Trading Brain lineage. |

None of the wrong-domain/wrong-lineage findings were imported or duplicated. `bujji/market_memory/` proceeds under its own name, using EventStore directly and mirroring Phase 19.5's own similarity/query pattern without importing its lineage-coupled code.

## 2. Files created

- `bujji/market_memory/__init__.py`, `models.py`, `store.py`, `index.py`, `retrieval.py`, `explain.py`, `builder.py`
- `tests/test_market_memory.py` (14 tests)
- `scripts/run_phase20_15_market_memory_validation.py`

**No files modified.** This phase is purely additive — confirmed by mtime on every reused component (`EventStore`, `state_persistence.models`, `market_understanding.memory_models`/`memory_similarity`, `mic_v0`, `decision_orchestration`, `shadow_decision_runtime`).

## 3. Architecture position

```
Market Data → Observation Schema → MIC → Strategy Intelligence → Opportunity Intelligence
    → Decision Orchestration → Shadow Decision Runtime (DecisionObservation)
    → Phase 20.15: Market Memory (builder.py reads DecisionObservation, never recomputes it)
    → [Future: confidence-adjustment phase, explicitly NOT this phase]
```

`builder.py` composes `MarketMemoryRecord`/`DecisionMemoryRecord` directly from a real `DecisionObservation`'s own fields — `market_regime`/`volatility_state`/`risk_state`/`data_quality` from Phase 20.1's `MarketState`, `decision_state`/`confidence`/`allocation_class`/`priority_score` from Phase 20.5/20.8/20.10. Nothing is recomputed. `retrieval.py`'s `build_memory_context()` returns pure evidence (similar-condition count, known/pending outcome counts, outcome distribution) — never a numeric confidence delta; that step is explicitly out of this phase's scope, matching the roadmap's own Phase 20.15 (write) / 20.15.1 (read/confidence-adjustment) split.

## 4. Tests (14, all passing on first run)

1. Memory persistence (`EventStore` round-trip)
2. Historical records immutable (no update/delete API exists; re-read is byte-identical)
3. Similar-condition retrieval (exact-regime-match ranking, real fixture data)
4. No-look-ahead exclusion (a future record is provably excluded from retrieval)
5. Missing-memory honesty (empty universe → honest empty result, never fabricated)
6. `evidence_score` unchanged by memory-context construction
7. MIC boundary respected (structural grep: no `mic_v0.engine`/`compose_market_state` call anywhere in the package)
8. Decision chain preserved (structural grep: no `compose_decision` call anywhere in the package)
9. Explainability survives storage/retrieval
10. No execution capability (two safety tests: forbidden call patterns, forbidden broker/capital imports)
11. Outcome memory honest `NOT_YET_OBSERVED` → `KNOWN` transition, real fixture data
12. `OutcomeMemoryRecord` structurally rejects a fabricated `KNOWN` status without a real `observed_at`

## 5. Regression

Full regression: **6,345 passed, 0 failed** (6,331 baseline from Phase 20.14 + 14 new).

## 6. Real historical validation (`scripts/run_phase20_15_market_memory_validation.py`)

Real data, 2026-08-01 to 2026-08-13 (the same 9 real trading days validated in Phase 20.11–20.14), reusing Phase 20.5's own published Trend Following evidence verbatim. Built one real `MarketState` → `FinalDecision` → `DecisionObservation` → `MarketMemoryRecord`/`DecisionMemoryRecord` per real day, persisted via `EventStore`.

**BEFORE memory** (2026-08-13, real day): `evidence_score=78.62`, `market_regime=RANGE`, `volatility=LOW`, `risk=NORMAL` — decision based only on current intelligence.

**AFTER memory**: same target, plus real historical context —

```
Similar historical conditions found: 5
Known outcomes: 0  Not yet observed: 0
Similarity breakdown: 5 real prior days, each 100/100 (regime/volatility/risk all match)
This is historical EVIDENCE only -- it does not modify evidence_score, qualification, or allocation.
```

**Proof, on real data:** `evidence_score` before the memory query and after are logged and asserted identical (`78.62 == 78.62`) — memory only ever added context, never touched the number.

## 7. Safety boundary confirmation

`grep` confirms zero occurrences of order/position/broker-call vocabulary anywhere in `bujji/market_memory/`. No `bujji.broker`/`bujji.capital.` import exists. No `mic_v0.engine`/`compose_market_state`/`compose_decision` call exists anywhere in the package — memory reads Phase 20.1–20.11's own already-computed outputs and nothing else. `disable_live_execution()` is irrelevant here since this package never touches a broker at all.

## 8. Intelligence loop status

```
Market → Observation → Intelligence → Decision → Shadow Result → Memory → Improved Intelligence
                                                                    ✅        ⬜ (not yet)
```

**The `Memory` node is now real and closed on the write side.** Every real shadow decision can be turned into a persisted, retrievable, explainable memory record, and real historical evidence is demonstrably retrievable without touching any upstream score. The final arrow — **Memory → Improved Intelligence** (a confidence adjustment actually feeding back into Strategy Intelligence) — remains open by design: that is Phase 20.15.1's own explicit scope, not this phase's. Bujji can now say "I remember conditions like this" on real data; it does not yet say "and that changes how confident I am."
