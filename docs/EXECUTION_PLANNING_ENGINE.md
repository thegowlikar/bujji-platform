# Execution Planning Engine v1 (EPE v1)
## BUJJI Engineering Series 98

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus, driven directly by Series 90's real `TradeConstructionAssessment`.
A new, independent operational layer -- Portfolio Construction (Series
91) and Position Lifecycle (Series 96) were not modified, per this
series' explicit constraint. No broker is called; no order is placed;
no fill is simulated anywhere in this package.

```
... -> Margin Bridge -> Execution Planning -> (future) Order Placement
```

---

## 1. Deliverable 1 — Capability Audit

Investigated `bujji/execution/engine.py::ExecutionEngine` and `bujji/core/orchestrator.py` directly:

| Component | Real finding | Classification |
|---|---|---|
| Idempotent order placement (`submit_and_confirm`/`_place_idempotent`) | Real, working: looks up `client_order_id` before placing, never blindly re-places on an ambiguous error, verifies first. | **REUSABLE BY PATTERN, adapter required** -- async/live, not directly callable; this package's `GATE_DUPLICATE_PROTECTION` and `FAILURE_REJECTED_ORDER`/`FAILURE_CANCELLED_ORDER` responses are modeled on it exactly, disclosed as such. |
| Partial-fill handling (`submit_and_confirm`'s truthful `filled_quantity`, only a ZERO fill raises) | Real, working. | **REUSABLE BY PATTERN** -- `FAILURE_PARTIAL_FILL`'s response reproduces this exact discipline. |
| Timeout handling (`_await_fill`: poll until fill/terminal, cancel remainder on timeout) | Real, working. | **REUSABLE BY PATTERN** -- `TimeoutPolicy` reproduces this exactly. |
| Reconciliation (`reconcile()`: fetch live positions to detect drift) | Real, working, live-only. | **REUSABLE BY PATTERN, adapter required** -- `RecoveryPolicy` reuses the CONCEPT (reconcile before resuming), not the code. |
| **Leg sequencing** (`orchestrator.py`: `ce_result = await submit_and_confirm(ce_request)` THEN `pe_result = await submit_and_confirm(pe_request)`) | **Real, confirmed: CE and PE are placed SEQUENTIALLY, never simultaneously** -- but hardcoded to exactly 2 legs, one strategy. | **MISSING for N-leg constructions** -- this is the one genuinely new capability this series builds: a general dependency-graph/staging engine for the 13 real families, generalizing this exact confirmed 2-leg precedent. |
| Broker abstraction (`Broker` ABC) / FYERS order APIs | Real, but live-only. | **PRODUCTION ONLY**, unchanged conclusion from every prior series' identical finding. |
| Retry logic (`_with_retry`, exponential backoff, auth-error non-retry) | Real, working. | **REUSABLE BY PATTERN, adapter required.** |
| Clock synchronization | **No `ntp`/`clock_sync`/`time_sync` code exists anywhere in this codebase** (confirmed by direct repo-wide search). | **MISSING entirely** -- disclosed; `GATE_MARKET_OPEN`/`GATE_EXECUTION_WINDOW` are honestly marked not-evaluable in this package for exactly this reason. |
| Risk gates | Already real via Series 91 (Portfolio Construction) and Series 97 (Margin Bridge). | **REUSED DIRECTLY** -- `GATE_MARGIN_STILL_SUFFICIENT` consumes their real outputs, never re-derives them. |
| Position reconciliation | Covered above. | (see Reconciliation row) |

**Conclusion**: no working code was duplicated. Every failure/recovery/timeout policy in this package either reuses a REAL, confirmed production behavior by pattern (disclosed explicitly in `config.py`'s own docstring, per policy) or is a genuinely NEW, conservative default for a gap that has no precedent anywhere in this codebase (also disclosed, per policy).

## 2. Deliverable 2 — ExecutionPlanAssessment

Immutable, frozen. All spec-required fields present: `plan_id`, `position_assessment_id`, `execution_mode`, `order_sequence`, `execution_steps`, `dependency_graph`, `validation_steps`, `rollback_policy`, `recovery_policy`, `timeout_policy`, `estimated_orders`, `estimated_latency`, `explanation`, `provenance`, `schema_version` -- plus `failure_policies` (Deliverable 4's explicit requirement).

## 3. Deliverable 3 — Order Dependency Graph

**Sequencing philosophy** (declarative, in `config.py::STAGE_RULES`, keyed by Series 90's own real `strategy_family`): *the leg that DEFINES or LIMITS the position's risk is staged first; legs that ADD exposure or REDUCE net cost are staged only after the risk-defining stage is confirmed filled.* This single rule reproduces BOTH of the spec's own worked examples exactly, verified directly:
- **Debit Spread** (`LONG_DIRECTIONAL`, single leg in this codebase's current family set) / any BUY-then-SELL family: `Buy Leg -> Verify Fill -> Sell Leg`.
- **Iron Condor**: real output is `[('SELL', [short CE, short PE]), ('BUY', [wing CE, wing PE])]` with exactly 1 dependency edge (stage 0 -> stage 1) -- matching `Short Call + Short Put -> Verify -> Long Wings -> Verify Complete Position` precisely.

Never simultaneous: every stage's legs are grouped logically (same risk role), but the dependency graph always requires the PRIOR stage's real, verified fill before the NEXT stage may begin -- reproducing production's own confirmed CE-then-PE sequential discipline, generalized to all 13 families.

## 4. Deliverable 4 — Failure Planning

All 8 required failure types have a declared, deterministic response in `config.py::FAILURE_RESPONSES`, each explicitly tagged as either a REUSED real production precedent or a NEW, disclosed conservative default (Section 1's table). No response is ever executed -- only planned.

## 5. Deliverable 5 — Validation Gates

All 6 required gates implemented, each explicitly marked `real_time_evaluable`:
- `MARKET_OPEN` / `EXECUTION_WINDOW`: **honestly `real_time_evaluable=False`** -- no clock-sync/market-calendar source exists anywhere in this codebase (Section 1).
- `POSITION_STILL_VALID` / `THESIS_STILL_VALID`: **real, computable** when a `PositionLifecycleAssessment` (Series 96) is supplied -- directly reuses its real `position_state`/`thesis_invalidation.compatible` fields, never re-derived.
- `MARGIN_STILL_SUFFICIENT`: **real, computable** when both a `MarginEstimate` (Series 97) and `PortfolioConstructionAssessment` (Series 91) are supplied -- compares their real figures directly.
- `DUPLICATE_PROTECTION`: **always real and computable**, deterministically, reusing the CONCEPT of `ExecutionEngine`'s own `client_order_id` idempotency key (a content hash of `trade.assessment_id`, never a random/UUID value).

Fail-closed: any gate whose real check returns `False` fails the plan at that point; gates never silently default to `True`.

## 6. Deliverable 6 — Real 41-day corpus replay

Run on the 11 real days Portfolio Construction (Series 91) actually APPROVED a trade:

- **Planned-order-count distribution: `{1: 10, 3: 1}`** -- 10 single-leg (`LONG_DIRECTIONAL`) plans, 1 three-leg (`BUTTERFLY`) plan.
- **Stage-count (dependency depth) distribution: `{1: 10, 2: 1}`.**
- **Estimated-latency distribution: `{LOW: 10, MODERATE: 1}`** -- a qualitative, stage-count-derived label, never a measured network figure.
- **Average execution complexity: 1.09 stages/plan** -- reflecting this specific corpus's real family mix (heavily `LONG_DIRECTIONAL`-dominated, per Series 89-97's own repeated finding).
- **Recovery-path frequency: 0/11** -- an honest null result, disclosed precisely: every evaluated plan in this replay is for a position on its OWN admission day (`NEWLY_OPENED`), where the thesis trivially still matches itself and margin was just confirmed sufficient by Portfolio Construction moments earlier. This measurement would only become non-trivial evaluated against a LATER day's re-validation of an already-open position (Position Lifecycle's own real 74% thesis-invalidation finding from Series 96 strongly suggests such re-validations WOULD show real gate failures) -- not evaluated in this specific replay, disclosed as a scope boundary, not a negative finding.
- Replayed twice: **execution `plan_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- the staging rules and failure/validation tables were fixed before this replay ran.

## 7. Deliverable 7 — Explainability

Every `ExecutionPlanAssessment.explanation` answers all four required questions: `why_this_sequence` (per-stage risk-defining-first reasoning), `why_this_dependency` (the real stage count and which production precedent it generalizes), `why_this_recovery_plan` (reconcile-then-reevaluate, tied to `ExecutionEngine.reconcile()`'s real concept), `why_this_validation_order` (the fixed, disclosed gate-checking priority).

## 8. Deliverable 8 — Integration

Demonstrated end-to-end on real data: `Observation -> MSI -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Position Construction -> Portfolio Construction -> Position Lifecycle -> Margin Bridge -> Execution Planning`. No broker calls, no order placement anywhere in the replay -- verified both by construction (this package never imports `bujji.broker`/`bujji.execution`) and by an AST test forbidding the literal string `place_order` from appearing anywhere in this package's source.

## 9. Recovery model

Three explicit, disclosed policies, none of which ever executes anything: `RollbackPolicy` (cancel only not-yet-placed dependent stages; never attempt to synthetically unwind an already-filled leg), `RecoveryPolicy` (reconcile real broker-reported positions, then re-evaluate via a fresh Position Lifecycle assessment, before any further order is planned), `TimeoutPolicy` (cancel the unfilled remainder, matching `ExecutionEngine._await_fill`'s own real behavior exactly).

## 10. Production integration

A future real order-placement layer would consume `ExecutionPlanAssessment.execution_steps` in order, submitting each stage's orders via the EXISTING, unmodified `ExecutionEngine.submit_and_confirm` (per stage's leg), verifying the real gate gates that this package could only mark not-evaluable (`MARKET_OPEN`/`EXECUTION_WINDOW`) using the live runtime's real clock, and re-evaluating `POSITION_STILL_VALID`/`THESIS_STILL_VALID`/`MARGIN_STILL_SUFFICIENT` via fresh Series 91/96/97 assessments before each stage -- exactly the "single execution implementation shared between replay and production, differing only in the execution backend" the user's own instruction specifies. No new order-manager code needs to be written; this package only decides the PLAN a real one would follow.

## 11. Known limitations

- Recovery-path/gate-failure scenarios have zero real-corpus exercise in this specific replay (Section 6) -- the logic is implemented and unit-tested directly (a dedicated test constructs a `THESIS_BROKEN` lifecycle and confirms both gates correctly fail), but no NEWLY_OPENED-day plan in this corpus happened to fail a gate.
- `estimated_latency` is a purely qualitative, stage-count-derived label -- no real network latency measurement exists anywhere in this replay arc.
- `MARKET_OPEN`/`EXECUTION_WINDOW` cannot be evaluated at all in this package -- a real, disclosed, still-open gap (no clock-sync code exists anywhere in this codebase).

## 12. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above, and consistent with the user's own proposed roadmap:

The execution planning logic is now real, deterministic, fail-closed, and demonstrated end-to-end (11 real plans, byte-identical on replay, correctly reusing production's own confirmed sequencing/failure/recovery precedents). The two gates this package cannot evaluate (`MARKET_OPEN`/`EXECUTION_WINDOW`) require a live clock -- exactly the kind of thing that can only be genuinely tested by observing this plan alongside REAL, live market data, without placing real orders.

**Recommended next step: Series 99 — Shadow Trading**, exactly as the user's own roadmap proposes: run this complete decision-and-planning chain against live market data, comparing intended plans (this series) against what actual market conditions would have allowed, with ZERO real orders placed -- the natural next validation step before Paper Trading, and the first opportunity to observe `MARKET_OPEN`/`EXECUTION_WINDOW` and Position Lifecycle's own real 74% thesis-invalidation dynamics play out against a live, not-yet-replayed feed.
