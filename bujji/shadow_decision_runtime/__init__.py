"""bujji.shadow_decision_runtime -- Phase 20.11 (Bujji Trading
Intelligence Roadmap v1.2).

"Observe what Bujji would decide if it were allowed to decide." Bujji
must NOT act. This package produces Decision Observation Records only
-- never an order, position, or paper fill.

NAMING / COLLISION AUDIT (this phase's own mandatory Step 1, run before
any code was written): none of `bujji/shadow_decision/`,
`bujji/decision_runtime/`, `bujji/intelligence_runtime/`, or the
literal requested `bujji/shadow_decision_runtime/` exist prior to this
phase -- no path collision.

A broad audit of every "shadow runtime", "decision recorder",
"intelligence recorder", "observation store", "session recorder",
"event recorder", and "audit trail" module in this repository found
real, sophisticated systems everywhere -- but every one of them falls
into one of two buckets, neither reusable here:

  (C, wrong domain -- REAL shadow TRADING) `bujji.shadow_observatory.
  session_store.SessionStore` persists `orders.jsonl`/`executions.jsonl`/
  `positions.jsonl` for one real shadow TRADING session -- exactly the
  forbidden vocabulary this phase must never write. `bujji.journal.
  decision_journal.DecisionJournal` persists `bujji.core.models.
  DecisionSnapshot`, joined against `TradeJournal` by `decision_id` --
  again real trading. Both are genuine, real trading-adjacent recorders;
  neither is repurposed here.

  (C, wrong lineage -- the older Phase 19 Intelligence Foundation)
  `bujji.msi_decision_auditor` (Series 99), `bujji.market_state.
  intelligence_cycle_recorder`, `bujji.shadow_runtime.live_intelligence_
  cycle`/`daily_intelligence_artifact`/`cycle_artifact` (Phase 19.10-19.13)
  are all real, working recorders -- but every one of them composes the
  PRE-MIC-v0 lineage (MarketRealitySnapshot -> MarketIntelligenceSnapshot
  -> DecisionContext -> DecisionIntelligenceSnapshot -> MarketPhenomena
  Assessment -> MarketStateGraph), the same lineage already disclosed as
  a non-reusable precedent in Phase 20.10's own audit
  (`bujji.decision_intelligence`, `bujji.shadow_runtime.
  intelligence_pipeline_adapter`). None of them has ever seen Cycle 1's
  own chain (MIC v0 -> Strategy Intelligence -> Opportunity Intelligence
  -> Opportunity Ranking -> Capital Intelligence -> Portfolio
  Intelligence -> Decision Orchestration) and none is imported here.

This package is therefore new, under its own requested name, observing
ONLY Cycle 1's own chain -- Phase 20.1 (MIC v0) through Phase 20.10
(Decision Orchestration) -- and recalculates none of it.

No broker import. No execution surface. No order/position/lot-sizing/
fill-price/broker-state vocabulary anywhere in this package --
enforced structurally by
`tests/test_shadow_decision_runtime/test_safety_boundary.py`.
"""
from __future__ import annotations

from .explain import explain_observation
from .models import DecisionObservation, SessionDecisionSummary
from .recorder import ShadowDecisionLog, build_session_summary, record_decision
from .runner import run_shadow_cycle

__all__ = [
    "DecisionObservation",
    "SessionDecisionSummary",
    "ShadowDecisionLog",
    "build_session_summary",
    "record_decision",
    "run_shadow_cycle",
    "explain_observation",
]
