"""Phase 20.20 -- Shadow Result Composition & Pipeline Observability.

Interface Map row: closes "Broker Boundary -> Paper Execution ->
Shadow Result -> Learning Update" -- the exact gap Phase 20.18's own
report ("the real next dependency ... a Shadow Result composition
step ... left for a future phase") and Phase 20.19's own report
("the real next dependency for closing the Execution Feedback ->
Memory arrow is a Shadow Result composition step") both explicitly
flagged as missing.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_20_SHADOW_RESULT_OBSERVABILITY_REPORT.md
for the full audit):

- `bujji.state_persistence.store.EventStore` (Phase 15B) -- A)
  reusable directly. Same append-only primitive `bujji.market_memory`
  (Phase 20.15) already uses; no new persistence mechanism introduced.
  Restart recovery is deliberately NOT a separate mechanism --
  `read_all_shadow_results()` IS the hydration path (event-derived
  reconstruction, Phase 15B/19.x's own established principle).
- `bujji.live_shadow_runner.health` (Phase 20.13) -- B) reusable
  PATTERN only. Its own "independent dimensions, every field computed
  from real recorded state, never estimated" discipline is mirrored in
  `health.py`; its own feed/intelligence/runtime SCOPE (pre-Decision
  Brain) does not cover Risk Context/Execution Intelligence/Broker
  Boundary, which did not exist when it was built -- this phase adds
  the missing coverage in a NEW module rather than modifying that one.
- `bujji.production_runtime.runtime.ShadowResult` (Engineering Series
  54, MSI/Trading Brain lineage) -- C) wrong domain, DISCLOSED NAME
  COLLISION. A real, separate dataclass composing a completely
  different pipeline's outputs (evidence_interpreter/market_state/
  strategy_selector/risk_brain/capital_brain/execution_planner/
  execution_engine/order_construction/broker_adapter). This module
  names its own record `ShadowResultRecord` (not the bare `ShadowResult`
  name) specifically to avoid ANY ambiguity with that real, existing
  class -- stronger than package-qualification alone.
- `bujji.live_shadow_runner.continuity` (Phase 20.14) -- B) reusable
  pattern only. Its five-way session-classification discipline
  (`STATUS_SESSION_COMPLETE`/`INCOMPLETE`/etc.) is a session-lifecycle
  concept, one level above this package's own per-cycle record scope;
  not imported, not duplicated.
- `bujji.dashboard.server` (legacy ORB-VWAP lineage) -- C) wrong
  domain. A real, live HTTP dashboard wired to that lineage's own
  `RuntimeStatus`/`TradeJournal` -- a different runtime, a different
  data model, and a live network service this phase's own scope (a
  persisted record + a text/dict health report) does not need. Its
  "operator visibility via a rendered status view" GOAL is honored via
  `explain.py`'s own plain-text rendering; no HTTP server is added.
- `bujji.decision_orchestration.FinalDecision` (20.10),
  `bujji.risk_context_adapter.RiskContextAssessment` (20.17.1),
  `bujji.execution_intelligence.{ExecutionIntent,ExecutionPlan}`
  (20.18), `bujji.broker_boundary.{BrokerResponse,ReconciliationResult}`
  (20.19) -- A) reusable directly, as this package's sole inputs.
  Never recomputed, never modified.

This package NEVER computes evidence/confidence/risk/execution
outcomes itself -- `ShadowResultRecord` only ever copies already-real
values from the upstream stage that produced them, or honestly `None`
when a stage was never reached. No broker import, no order-placement
vocabulary, no live network service anywhere in this package.
"""
from .builder import build_shadow_result_record
from .explain import explain_pipeline_health, explain_shadow_result
from .health import (
    RUNTIME_DEGRADED, RUNTIME_EMPTY, RUNTIME_HEALTHY,
    PipelineHealthReport, PipelineStageHealth, ReconciliationHealth, compute_pipeline_health,
)
from .models import (
    ALL_PIPELINE_STAGES, EVENT_SHADOW_RESULT_RECORDED, SCHEMA_VERSION,
    STAGE_BROKER_ROUTED, STAGE_DECISION_ONLY, STAGE_EXECUTION_PLANNED, STAGE_RECONCILED, STAGE_RISK_EVALUATED,
    ShadowResultRecord, shadow_result_id_for,
)
from .store import read_all_shadow_results, record_shadow_result

__all__ = [
    "build_shadow_result_record", "record_shadow_result", "read_all_shadow_results",
    "compute_pipeline_health", "explain_shadow_result", "explain_pipeline_health",
    "ShadowResultRecord", "shadow_result_id_for",
    "PipelineHealthReport", "PipelineStageHealth", "ReconciliationHealth",
    "ALL_PIPELINE_STAGES", "STAGE_DECISION_ONLY", "STAGE_RISK_EVALUATED", "STAGE_EXECUTION_PLANNED",
    "STAGE_BROKER_ROUTED", "STAGE_RECONCILED",
    "RUNTIME_HEALTHY", "RUNTIME_DEGRADED", "RUNTIME_EMPTY",
    "EVENT_SHADOW_RESULT_RECORDED", "SCHEMA_VERSION",
]
