# Phase 20.17.1 — Risk Context Adapter

**Bujji OS v1.0 Roadmap, Cycle-1 lineage.** Fixes the architectural mismatch Phase 20.17 discovered at the Interface Map's "Decision Brain → Risk Governor Bridge" row: Cycle 1 asks *"can this new opportunity be safely admitted?"*; the real Risk Governor's D.2 stage answers *"what is the risk state of an existing portfolio/book?"* — a lifecycle-stage mismatch, not evidence the opportunity is unsafe.

---

## 1. Audit findings (Step 1)

| Component | Classification | Disposition |
|---|---|---|
| `bujji.trading_brain.risk_governor.capital_safety_governor` (D.1) | **A) Reusable directly** | The one real governor stage that is genuinely pre-trade-compatible — it evaluates a *proposed* trade's effect on capital, not an existing book. Called via the same real `evaluate_trade_capital_safety()` Phase 20.17 already uses. |
| `bujji.trading_brain.risk_governor.portfolio_risk_aggregator` (D.2) | **C) Wrong lifecycle stage for this phase** | Confirmed (again) that `aggregate_portfolio_risk()` marks a flat/zero-position book `RISK_INVALID` — it needs a real `MarginSnapshot`, which needs a real broker margin query against a real position. **Never called** by this adapter; the resulting gap is named directly (`NOT_READY_FOR_CAPITAL_APPROVAL`) instead of forced. |
| `bujji.trading_brain.risk_governor.strategy_risk_adapter` (Gate E.1) | **B) Reusable pattern only** | A real, tested adapter — but targets a *different* domain: a `TradeConstructionAssessment` from `msi_trade_construction` (real option-leg proposal with strikes/contracts). Cycle 1's `FinalDecision`/`AllocationAssessment` carry no legs. Not imported; its "explicit caller-supplied risk figures, never invented" discipline is mirrored. |
| `bujji.trading_brain.risk_governor.governor_context_builder` (Gate E.2) | **B) Reusable pattern only** | Re-reading its own Part 1 findings confirmed the exact same structural fact this phase relies on: with zero active position groups, its own consistency check (`if active_ids and margin_snapshot is None: fail`) is *silently skipped* — meaning even this real, already-tested builder cannot produce a genuinely valid D.2 context for a flat book either. This is a codebase-wide gap, not one specific to Cycle 1 — confirms Phase 20.17's finding was not a Cycle-1 oversight. |
| `bujji.risk_governor_bridge` (Phase 20.17) | **A) Reusable directly** | `NOTIONAL_PROBE_MARGIN`/`NOTIONAL_PROBE_MAX_LOSS` imported, never redefined — both phases' probes stay numerically identical by construction, never duplicated. |
| `bujji.decision_orchestration.FinalDecision`, `bujji.capital_intelligence.AllocationAssessment`, `bujji.opportunity_ranking.RankingResult`, `bujji.opportunity_portfolio.PortfolioDecision` | **A) Reusable directly** | This adapter's inputs. Never recomputed, never modified. |
| `bujji.journal.position_group_journal` (Gate A) | **C) Wrong domain for this phase** | The real, durable position registry — exists, but Cycle 1 writes nothing into it (no real positions exist). Confirmed present, not touched. |
| `bujji.msi_trade_construction` | **C) Wrong domain** | Real option-leg construction for the MSI/Trading Brain lineage — a different "what to trade" engine than Cycle 1's own evidence-based Opportunity Intelligence. Not imported. |

## 2. Interface mismatch discovered (confirmed, not new)

Phase 20.17 found: with `position_groups=[]`, `aggregate_portfolio_risk()` returns `total_margin_required=None`, and `classify_portfolio_risk()` reports `RISK_INVALID`. Auditing Gate E.2's `governor_context_builder.py` this phase confirmed this is **structural, not Cycle-1-specific**: even the real, already-tested Gate E builder cannot assemble a valid D.2 context without an already-open position to query real margin against. There is no code path anywhere in this codebase, today, that produces a valid D.2 result for a flat book. This phase's design (never call D.2 for a pre-trade review; name the gap directly) is the correct response to a confirmed, codebase-wide fact.

## 3. Adapter design

```
Decision Brain (FinalDecision) + Capital Allocation (AllocationAssessment)
        ↓
build_risk_context_request()  -- pure translation, zero calculation
        ↓
RiskContextRequest
        ↓
evaluate_risk_context()
        ├─ decision_state not worth review (NO_OPPORTUNITY/BLOCKED)  → NOT_EVALUATED
        ├─ no capital_snapshot supplied                              → UNAVAILABLE_RISK_CONTEXT
        ├─ D.1 real capital-safety governor rejects (real finding)   → RESTRICTED
        └─ D.1 clears, D.2 deliberately not evaluated                → NOT_READY_FOR_CAPITAL_APPROVAL
        ↓
RiskContextAssessment (never an approval to trade)
```

D.2 is never invoked — confirmed structurally by an AST-based test scanning for real calls/imports of `aggregate_portfolio_risk`/`classify_portfolio_risk` (not a bare substring, which would false-positive on this package's own disclosure docstrings).

## 4. Files created

- `bujji/risk_context_adapter/{__init__,models,adapter,explain}.py`
- `tests/test_risk_context_adapter.py` (14 tests)
- `scripts/run_phase20_17_1_validation.py`

**No files modified.** `bujji.trading_brain.risk_governor.*`, `bujji.decision_orchestration`, `bujji.capital_intelligence`, `bujji.risk_governor_bridge` all confirmed untouched by mtime.

## 5. Validation scenarios (real evidence, reused verbatim from Phase 20.5)

**Scenario 1 — Trend Following strong opportunity** (`evidence_score=78.62`, `confidence=HIGH`, `EXECUTABLE_CANDIDATE`, healthy capital):
```
Risk context status: NOT_READY_FOR_CAPITAL_APPROVAL
D.1 capital safety clears (SAFE). D.2 portfolio risk was not evaluated -- no live
margin/position context exists yet. Risk Governor not rejected; this opportunity
requires portfolio context before capital admission can be assessed.
```
Not a fake approval — exactly the honest outcome the phase brief specified.

**Scenario 2 — Mean Reversion failed evidence** (`evidence_score=0.0`, `NO_OPPORTUNITY`):
```
Risk context status: NOT_EVALUATED
decision_state='NO_OPPORTUNITY' was never worth reviewing...
```
`NO_OPPORTUNITY` remains `NO_OPPORTUNITY` — the adapter never re-opens a rejected decision.

**Scenario 3 — Extreme risk restriction** (exhausted capital, breached daily-loss limit):
```
Risk context status: RESTRICTED
D.1 capital safety identifies a real restriction -- ... Blocking reasons:
ACCOUNT_ALREADY_BLOCKED:DAILY_LOSS_LIMIT_BREACHED.
```
A genuine restriction, surfaced from the real, unmodified D.1 governor — not fabricated, not hidden.

`evidence_score` (78.62) logged and confirmed identical before/after all three scenarios.

## 6. Tests (14, all passing)

1. Correct translation: `FinalDecision`+`AllocationAssessment` → `RiskContextRequest` (strategy, allocation class, confidence, regime all mapped; `expected_exposure`/`expected_loss_boundary` honestly `None`)
2. No fabrication: no `MarginSnapshot(`/`PositionGroupState(`/`PositionRiskSnapshot(`/`.place_order(` anywhere in the package
3. Risk Governor preservation (package boundary check)
4–5. Missing context honesty: no `capital_snapshot` → `UNAVAILABLE_RISK_CONTEXT`; healthy capital + no portfolio context → `NOT_READY_FOR_CAPITAL_APPROVAL`, never `BLOCKED`
6. Opportunity rejection preservation: `NO_OPPORTUNITY`/`BLOCKED` stay `NOT_EVALUATED` (parametrized)
7. Extreme risk → `RESTRICTED`, real blockers surfaced
8–9. Decision independence: neither `RiskContextRequest` nor `RiskContextAssessment` carries `evidence_score`/`effective_score`/`priority_score`/`rank`; `evidence_score` proven byte-identical (78.62) before/after
10. Explainability: every assessment has a non-empty explanation naming the strategy and status
11–12. Safety boundary: zero forbidden broker call patterns, zero `bujji.broker`/`bujji.execution` imports
13. D.2 never called — AST-verified against real call/import syntax, not docstring prose

## 7. Regression

Full suite: **6,381 passed, 0 failed** (6,367 baseline from Phase 20.17 + 14 new; clean run, no environmental flakes this time). `bujji/risk_context_adapter/`'s own 14 tests: 14/14 passing, both standalone and inside the full suite.

## 8. Safety verification

`grep`/`ast`-based tests confirm zero forbidden broker call patterns (`place_order`/`modify_order`/`cancel_order`/`get_open_positions`/`get_order`), zero `bujji.broker`/`bujji.execution` imports, and zero real calls to `aggregate_portfolio_risk`/`classify_portfolio_risk` anywhere in `bujji/risk_context_adapter/`. `bujji.trading_brain.risk_governor.*` confirmed byte-identical by mtime — this phase modifies nothing in the Risk Governor or Capital Management Engine.

## 9. Updated roadmap position

```
Decision Brain → Risk Context Adapter → Risk Governor Bridge → Execution Intelligence
       ✅              ✅ (this phase)         ✅ (20.17, D.1-D.3)        ⏳ (out of scope by design)
```

Bujji can now honestly express *why* a real, evidence-qualified opportunity is not yet capital-approved — a structural "needs portfolio context" gap, not a fabricated rejection or a fake approval. The real next dependency for reaching `READY_FOR_REVIEW`/`STATUS_ADMITTED` on live data is a genuine (even if minimal) live `MarginSnapshot`/Position Group Journal wiring — confirmed, by this phase's own audit of Gate E.2, to be a real, codebase-wide gap and not something either this adapter or Phase 20.17 could honestly paper over.
