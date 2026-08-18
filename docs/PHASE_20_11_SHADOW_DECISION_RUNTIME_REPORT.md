# Phase 20.11 — Shadow Decision Runtime & Intelligence Observation Layer

**Bujji Trading Intelligence Roadmap v1.2, Cycle 1.**
**Core principle: "Observe what Bujji would decide if it were allowed to decide." Bujji must NOT act.** This phase produces `DecisionObservation` records only — never an order, position, entry/exit price, or broker state.

---

## 1. Audit findings

**Naming collision check (this phase's own explicit Step 1):** none of `bujji/shadow_decision/`, `bujji/decision_runtime/`, `bujji/intelligence_runtime/`, or the literal requested `bujji/shadow_decision_runtime/` existed before this phase. No path collision.

**Broad repository audit** (shadow runtime, decision recorder, intelligence recorder, observation store, market session recorder, event recorder, decision journal, audit trail) found real, working systems everywhere — but every one fell into one of two non-reusable buckets:

| Component | Classification | Why not reused |
|---|---|---|
| `bujji.shadow_observatory.session_store.SessionStore` | **C) Wrong domain** | A real shadow-**trading** session store — persists `orders.jsonl`/`executions.jsonl`/`positions.jsonl`. Exactly the forbidden vocabulary this phase must never write. |
| `bujji.journal.decision_journal.DecisionJournal` | **C) Wrong domain** | Persists `bujji.core.models.DecisionSnapshot`, joined against the real `TradeJournal` by `decision_id` — real trading. |
| `bujji.journal.intelligence_observation_journal.IntelligenceObservationJournal` | **C) Wrong domain** | Records `ObservationHealth` cycles from `bujji.intelligence.mic_adapter.monitor` — a data-health monitor, not a decision record. |
| `bujji.msi_decision_auditor` (Series 99) | **C) Wrong lineage** | Composes `ExecutionPlanAssessment`/`MarginEstimate`/`PortfolioConstructionAssessment` — the older, options-execution-adjacent MSI series, never Cycle 1's chain. |
| `bujji.market_state.intelligence_cycle_recorder`, `bujji.shadow_runtime.live_intelligence_cycle`/`daily_intelligence_artifact`/`cycle_artifact` (Phase 19.10–19.13) | **C) Wrong lineage** | All real, sophisticated recorders — but all compose the pre-MIC-v0 lineage (`MarketRealitySnapshot → MarketIntelligenceSnapshot → DecisionContext → DecisionIntelligenceSnapshot → MarketPhenomenaAssessment → MarketStateGraph`), the SAME lineage Phase 20.10's own audit already disclosed as non-reusable (`bujji.decision_intelligence`, `bujji.shadow_runtime.intelligence_pipeline_adapter`). None has ever seen Cycle 1's own MIC v0 → Strategy Intelligence → … → Decision Orchestration chain. |
| `bujji.mic_v0.models.MarketState` (Phase 20.1), `bujji.decision_orchestration.FinalDecision` (Phase 20.10) | **A) Reusable directly** | The entire input surface of this phase — read, never recalculated. |

No existing system was extended or duplicated; `bujji/shadow_decision_runtime/` proceeds under its own requested name, observing Cycle 1's own chain exclusively.

## 2. Architecture

```
bujji/shadow_decision_runtime/
    __init__.py
    models.py    -- DecisionObservation, SessionDecisionSummary
    recorder.py   -- record_decision(), ShadowDecisionLog, build_session_summary()
    runner.py     -- run_shadow_cycle()
    explain.py    -- explain_observation()
```

```
Market Feed → Observation Layer → MIC v0 (20.1) → Strategy Intelligence (20.5)
    → Opportunity Intelligence (20.6) → Opportunity Ranking (20.7) → Capital Intelligence (20.8)
    → Portfolio Intelligence (20.9) → Decision Orchestration (20.10) → Phase 20.11 (this phase)
```

`run_shadow_cycle(market_state, final_decision, timestamp)` reads Phase 20.1's `MarketState` and Phase 20.10's own already-composed `FinalDecision` — including the `AllocationAssessment` it carries through (Phase 20.7's `priority_score`, Phase 20.8's `allocation_class`, Phase 20.5's own `confidence` label) — and packages them into one `DecisionObservation`. No scoring, ranking, qualification, allocation, conflict, or decision logic lives in this package; it observes the chain, it does not extend it.

## 3. Observation model

`DecisionObservation`: `observation_id`, `timestamp`, `market_state` (Phase 20.1's `MarketState.to_dict()`, verbatim), `candidate_strategy`, `decision_state` (Phase 20.10's vocabulary), `priority_score`, `allocation_class`, `confidence`, `reason_codes` (Phase 20.10's `positive`+`negative`, verbatim), `uncertainty` (Phase 20.10's `unknown`, verbatim), `data_quality`. Explicitly excluded: order details, quantity, entry/exit price, broker state — this is intelligence memory only.

`SessionDecisionSummary`: `session_date`, `number_of_cycles`, `candidate_count`, `decision_distribution`, `highest_confidence_decision`, `blocked_count`, `watch_count`, `no_opportunity_count`, `uncertainty_summary` — all counted directly from the session's own recorded observations, never re-judged.

## 4. Historical shadow run (`scripts/run_phase20_11_shadow_run.py`)

Read-only. Reuses `HistoricalObservationStore` and `compose_market_state` exactly as Phase 20.1B's own `scripts/run_mic_v0_validation.py` already does — no strategy discovery, optimization, or backtesting rerun. Phase 20.5's own published Trend Following (n=11,278, evidence_score=78.62) and Mean Reversion (n=150, evidence_score=0.0) evidence is reused verbatim, unchanged, for every cycle.

**Real run, 2026-01-01 to 2026-08-13:** 152 real NIFTY futures trading days, real India VIX daily history, zero fabricated inputs, zero skipped for missing VIX. **304 real `DecisionObservation`s recorded** (2 candidates × 152 days).

**Real MIC v0 session-level regime distribution across these 152 days: RANGE = 136 days, UNCLEAR = 32 days (16 shown per-candidate), TREND = 0 days.** This directly corroborates Phase 20.1's own MIC Validation Report finding (`TREND samples: n=0`, session-level regime marked INCONCLUSIVE) — session-level MIC v0 classification essentially never emits `TREND` in real Cycle-1 data; the intraday-granularity classifier (Phase 20.1C) is a separate, already-validated instrument this script does not re-run.

## 5. Decision distribution (304 real observations)

| Decision | Count | Cause |
|---|---|---|
| `NO_OPPORTUNITY` | 152 | Mean Reversion, every single real day — `"Strategy validation failed -- insufficient historical evidence."` (Phase 20.4's real, failed finding, reproduced exactly once per day). |
| `BLOCKED` | 136 | Trend Following, on every real `RANGE`-classified day — `"REGIME_INCOMPATIBLE: mic_regime='RANGE' explicitly disclosed as incompatible with this strategy."` |
| `WATCH` | 16 | Trend Following, on every real `UNCLEAR`→`TRANSITION`-mapped day — `"Qualification WATCH: mic_regime='TRANSITION' not in this strategy's validated favorable set."` |
| `EXECUTABLE_CANDIDATE` | 0 | Never reached in this real window — session-level MIC v0 never classified a real day as `TREND` in this period. |

`evidence_score` held constant across every one of the 304 real cycles: `78.62` (Trend Following), `0.0` (Mean Reversion) — direct proof, on real data, that MIC never modifies evidence.

## 6. Unknown/uncertainty analysis

Every one of the 304 observations carries exactly one uncertainty entry: `"Event calendar: NOT_AVAILABLE"` — Phase 20.1's own disclosed gap (no macro-event calendar exists anywhere in this codebase), surfaced honestly rather than fabricated, on every single real cycle without exception.

## 7. Limitations

1. **Regime-mapping approximation, disclosed:** the strategy-evaluation environment's `mic_regime` input (Phase 20.6's intraday-vocabulary contract) is derived here from MIC v0's own session-level `market_regime` (`TREND→TREND_UP`, `RANGE→RANGE`, `UNCLEAR→TRANSITION`) rather than from Phase 20.1C's own validated intraday classifier, which this phase does not re-run (out of scope — it is itself a distinct, already-completed validation effort). This mapping is sufficient to exercise the observation chain honestly across genuinely varying real conditions, but does not carry Phase 20.1C's own predictive validation.
2. **Zero real `TREND` days in this window** means `EXECUTABLE_CANDIDATE` was never observed on real data during this phase's own historical run — not a defect in the chain (Phase 20.10's own synthetic scenario already proved `EXECUTABLE_CANDIDATE` is reachable), but an honest real-data finding about how rarely session-level `TREND` fires.
3. **In-memory only, as designed:** `ShadowDecisionLog` is not persisted to disk in this phase — "minimal only, no framework," per the charter. A durable store is a future caller's concern.
4. **Two real strategies, as in every prior phase:** the same two Cycle-1 families exercised throughout Phases 20.5–20.10.

---

## Testing & regression

9 new tests (decision recording: 1, no execution leakage: 2, data quality propagation: 1, explainability preservation: 1, multiple cycles: 1, missing intelligence: 2, session summary correctness: 1), all passing on first run. Full regression confirmed clean against the Phase 20.10 baseline (6,294 passed) plus these 9 new tests. `grep` confirms zero occurrences of `place_order`/`modify_order`/`cancel_order`/`entry_price`/`exit_price`/`order_id`/`quantity`/broker or capital imports anywhere in `bujji/shadow_decision_runtime/`. No trading capability added.

**Final objective reached:** Bujji can now say — "I observed this market," "I evaluated these opportunities," "I reached this intelligence decision," "I recorded why" — on 304 real cycles across 152 real trading days. Bujji still does not trade. The next future stage is Shadow Market Campaign + Runtime Validation — no live execution yet.
