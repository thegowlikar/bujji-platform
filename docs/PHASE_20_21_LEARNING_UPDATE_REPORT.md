# Phase 20.21 — Learning Update Layer: Shadow Result → Market Memory Feedback

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Closes the final arrow of the Interface Map's own pipeline: `Market → Observation → Intelligence → Decision → Risk → Execution Simulation → Shadow Result → Learning Update → Market Memory → Improved Intelligence`.

---

## 1. Audit findings (Step 1)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.outcome_memory` (Phase 15N) | **C) Wrong domain, B) pattern for its own disclosure** | Real P&L/position outcome memory — requires real positions Cycle 1 does not have. Its own module docstring states the exact discipline this phase follows verbatim: *"Decision → Outcome → Memory ... NEVER Decision → Outcome → Memory → Decision (that feedback loop is an explicit future phase with a much higher trust bar)."* `evaluator.py` mirrors this: it is never called from any live decision path. |
| `bujji.trading_brain.risk_governor.adaptive_risk_recommendation` (Gate D.5, Part 5) | **B) Reusable pattern only** | A real, deterministic "fixed if/elif rule table, every threshold a named constant, the rule that fired always recorded" classifier over real risk-memory statistics. `evaluator._classify()` mirrors this exact discipline over `ShadowResultRecord` fields instead — not imported, a different domain (real position risk, not paper-execution learning). |
| `bujji.market_memory.models.OutcomeMemoryRecord` (Phase 20.15) | **C) Wrong schema for this phase's data** | Genuinely inspected: this record encodes MARKET-REGIME continuity (`regime_after`, `volatility_state_after`, `regime_unchanged`), sourced from `DecisionObservation`/`MarketMemoryRecord` — it has no field for a paper-execution outcome or a reconciliation result. Forcing this phase's data into it would misuse an existing schema for something it was never designed to hold. |
| `bujji.state_persistence.store.EventStore` / `deduplicated_events()` (Phase 15B) | **A) Reusable directly** | The exact same primitive `bujji.market_memory.store` and `bujji.shadow_result.store` are already built on. This phase adds a fourth, additive event type (`LEARNING_UPDATE_RECORDED`) that coexists in the SAME durable file `market_memory` already writes to for a session — satisfying "reuse `bujji.market_memory`, do not create another memory database" literally: one file, one `EventStore` class, a fourth coexisting event type. `market_memory`'s own read functions filter by their own event_type and are structurally unaffected (verified by a dedicated coexistence test — see §5, test 15). |
| `bujji.memory_intelligence` (Phase 20.15.1) | **B) Reusable pattern only** | Its own "fixed rule ladder, one-band confidence adjustment, every branch as prominent and tested as every other" discipline informed this phase's rule-ladder shape. Its own function (`apply_memory_influence`) reads a different input shape (`MemoryContext`/`OutcomeMemoryRecord`) and is not called here. |
| `bujji.shadow_result.ShadowResultRecord` (Phase 20.20) | **A) Reusable directly** | This phase's sole input. Never recomputed, never modified. |

**Conclusion**: no existing memory schema fits paper-execution-outcome learning; the correct reuse is the underlying `EventStore` mechanism itself, not a forced fit into `OutcomeMemoryRecord`.

## 2. Reused components

- `bujji.state_persistence.store.{EventStore, deduplicated_events}` — persistence primitive, unmodified.
- `bujji.shadow_result.ShadowResultRecord` — sole input, unmodified.
- `bujji.decision_orchestration`/`bujji.risk_context_adapter`/`bujji.execution_intelligence`/`bujji.broker_boundary` status constants — read-only, for classification comparisons only.

## 3. Architecture decision

```
bujji/learning_update/
    __init__.py    -- Step 1 audit disclosure, public API
    models.py       -- LearningUpdateRecord, classification constants, deterministic update_id
    evaluator.py      -- evaluate_shadow_result_for_learning() -- fixed if/elif rule table
    store.py            -- record_learning_update() / read_all_learning_updates(), same EventStore file as market_memory
    explain.py             -- explain_learning_update()
```

```
ShadowResultRecord (Phase 20.20)
        ↓
evaluate_shadow_result_for_learning()  -- pure, deterministic, no lookahead
        ↓
LearningUpdateRecord
        ↓
record_learning_update()  -- appended to the SAME EventStore file bujji.market_memory already uses
        ↓
MarketMemory (durable file) -- LEARNING_UPDATE_RECORDED coexists with MARKET_MEMORY_RECORDED/
                                DECISION_MEMORY_RECORDED/OUTCOME_MEMORY_RECORDED, never a new database
```

**Classification rule ladder** (`evaluator._classify()`, fixed if/elif, every branch named and tested):
1. `decision_state` in `(NO_OPPORTUNITY, BLOCKED, INSUFFICIENT_INTELLIGENCE)` → `INSUFFICIENT_RESULT` (no decision).
2. `risk_context_status` unavailable/un-evaluated/`RESTRICTED` → `INSUFFICIENT_RESULT` (no execution outcome could exist).
3. `reconciliation_consistency is None` (risk cleared but pipeline never reached reconciliation) → `UNAVAILABLE_DATA`.
4. `reconciliation_consistency` in `(INCONSISTENT, MISSING_RESPONSE)` → `CONFLICTING_SIGNAL`.
5. Reconciliation `CONSISTENT`:
   - `execution_result_status is None` (simulation correctly skipped) → `INSUFFICIENT_RESULT`.
   - `execution_result_status == REJECTED` → `FAILED_PATTERN`.
   - `execution_result_status in (FILLED, PARTIAL)` and `decision_state == EXECUTABLE_CANDIDATE` → `CONFIRMED_PATTERN`.
   - Otherwise (a real fill under a `WATCH` decision) → `INSUFFICIENT_RESULT`, a disclosed conservative default, never silently upgraded to a confirmed pattern.

**Critical rules enforced by construction, not convention:**
- **Rule 1**: `ShadowResultRecord` itself carries no `evidence_score`/`qualification_score`/`ranking_score`/`allocation_score`/`risk_score` field (Phase 20.20's own established boundary) — there is nothing in this module's only input to accidentally touch. Verified by a dedicated `hasattr` test on both the input and output objects.
- **Rule 2**: `evaluate_shadow_result_for_learning()` returns a `LearningUpdateRecord` — memory evidence only, never a decision, recommendation, or opportunity. It is never imported by, and never called from, any decision-path package.
- **Rule 3**: the sole input is one already-completed `ShadowResultRecord`; no market data, no future cycle, no lookahead of any kind is read anywhere in `evaluator.py`.

## 4. Files created

- `bujji/learning_update/{__init__,models,evaluator,store,explain}.py`
- `tests/test_learning_update.py` (16 tests)
- `scripts/run_phase20_21_validation.py`

**No files modified.** `bujji.state_persistence.*`, `bujji.market_memory.*`, `bujji.memory_intelligence.*`, `bujji.decision_orchestration`, `bujji.risk_context_adapter`, `bujji.execution_intelligence`, `bujji.broker_boundary`, `bujji.shadow_result` all confirmed untouched by mtime.

## 5. Tests (16, all passing)

1. Successful shadow result → `CONFIRMED_PATTERN`
2. Rejected fill → `FAILED_PATTERN`
3. `NO_OPPORTUNITY`/`BLOCKED` decisions never produce `CONFIRMED_PATTERN` (parametrized) — always `INSUFFICIENT_RESULT`
4–5. Missing outcome handled honestly: reconciled-but-no-fill → `INSUFFICIENT_RESULT`; risk-cleared-but-never-reconciled → `UNAVAILABLE_DATA`; reconciliation mismatch → `CONFLICTING_SIGNAL`
6. Evidence/qualification/ranking/allocation/risk score fields absent from both input and output objects (Rule 1)
7. `RESTRICTED` risk never produces `CONFIRMED_PATTERN` (risk governor boundary preserved)
8. Package never calls `simulate_execution()`/`.place_order(` (execution boundary preserved)
9. Restart recovery: write via one `EventStore` instance, read via a fresh instance on the same file
10. Duplicate learning update idempotency: same `ShadowResultRecord` evaluated and written twice → deduplicated to one record on read
11. Explainability: every `LearningUpdateRecord` renders a non-empty, strategy-named, classification-named explanation
12. No live-broker imports anywhere in the package
13. No order-placement vocabulary anywhere in the package
14. **Memory write verification**: a `LearningUpdateRecord` and a real `MarketMemoryRecord` written to the SAME `EventStore` file both read back correctly and independently — proves coexistence, not a second database

## 6. Real-data validation (real evidence, reused verbatim from Phase 20.5)

`scripts/run_phase20_21_validation.py`, full chain (20.10 → 20.17.1 → 20.18 → 20.19 → 20.20 → 20.21):

**Scenario A — successful paper execution** (Trend Following, `EXECUTABLE_CANDIDATE`):
```
Memory count BEFORE: 0
Learning classification: CONFIRMED_PATTERN
Memory count AFTER: 1
```

**Scenario B — failed opportunity** (a real, reconciled `REJECTED` simulated fill):
```
Learning classification: FAILED_PATTERN
```

**Scenario C — extreme risk restriction** (`WATCH`, `RESTRICTED`):
```
Learning classification: INSUFFICIENT_RESULT
PROVEN: no successful (CONFIRMED_PATTERN) learning was written for a RESTRICTED risk cycle.
```

Final memory count: 3, classifications `['CONFIRMED_PATTERN', 'FAILED_PATTERN', 'INSUFFICIENT_RESULT']` — exactly matching the 3 required scenarios.

## 7. Regression

Full suite: **6,436 passed, 0 failed** (6,420 baseline from Phase 20.20 + 16 new; clean run, no environmental flakes). `bujji/learning_update/`'s own 16 tests: 16/16 passing, both standalone and inside the full suite.

## 8. Safety verification

`grep`/`ast`-based tests confirm zero forbidden order-placement patterns and zero imports of any live-broker module anywhere in `bujji/learning_update/`. Confirmed untouched by mtime: MIC (`bujji.mic_v0`), Market Memory storage (`bujji.market_memory`), Memory Intelligence (`bujji.memory_intelligence`), Decision Orchestration (`bujji.decision_orchestration`), Risk Governor (`bujji.trading_brain.risk_governor.*`, `bujji.risk_context_adapter`), Execution Intelligence (`bujji.execution_intelligence`), Broker Boundary (`bujji.broker_boundary`). No `evidence_score`/`qualification_score`/`ranking_score`/`allocation_score`/`risk_score` field or attribute exists anywhere in this package.

## 9. Remaining gaps

- **No automatic wiring yet**: `evaluate_shadow_result_for_learning()`/`record_learning_update()` exist as callable functions but are not yet invoked automatically from the live shadow campaign runner (Phase 20.13/20.14) — that wiring is a future, separately-audited integration step, not fabricated here.
- **`market_context_signature` is honestly `None` by default**: `ShadowResultRecord` (Phase 20.20) does not carry market regime; a caller must explicitly supply it from the same real `AllocationAssessment`/`MarketEnvironment` used earlier in the same cycle. No automatic enrichment path exists yet.
- **No consumption side yet**: `LearningUpdateRecord`s are durably written and readable, but no future-intelligence-cycle component yet reads and applies them (per Rule 2, this is deliberate — "future intelligence decides," not this phase).
- **D.2/margin-snapshot gap** (Phase 20.17/20.17.1's own disclosed finding) remains open and unaffected by this phase.

---

## Complete intelligence loop — reassessment

```
Market Data → Observation → MIC → Market Memory → Strategy Intelligence → Opportunity Engine
    → Decision Brain → Risk Context → Execution Intelligence → Broker Boundary → Paper Execution
    → Shadow Result → Learning Update → Market Memory → Improved Intelligence
        ✅        ✅      ✅        ✅              ✅                  ✅
    ✅ (20.10)     ✅ (20.17.1)      ✅ (20.18)         ✅ (20.19)          ✅ (20.19)
        ✅ (20.20)      ✅ (this phase)   ✅ (durable, coexisting)   ⏳ (not yet consumed)
```

Every arrow in the originally stated loop now has real, tested, disclosed code behind it, end to end, from real market data through to a durable, restart-recoverable learning signal — with the sole remaining gap being deliberate: nothing yet *consumes* `LearningUpdateRecord`s to influence a future cycle's confidence, by design (Rule 2), and the known structural gaps already disclosed in Phase 20.17 (D.2 margin snapshot) and Phase 20.20 (session-level continuity, live dashboard) remain open, unaffected by this phase.
