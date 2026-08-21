"""Phase 20.20 -- pure data contracts. No IO, no broker, no execution,
no strategy-scoring/tuning logic anywhere in this module.

DISCLOSED NAME COLLISION: `bujji.production_runtime.runtime.ShadowResult`
already exists -- a real, separate dataclass in the MSI/Trading Brain
lineage (Engineering Series 54), composing evidence_interpreter/
market_state/strategy_selector/risk_brain/capital_brain/
execution_planner/execution_engine/order_construction/broker_adapter
outputs for a COMPLETELY different pipeline. To avoid any ambiguity
(rather than merely relying on package-qualification, as every prior
disclosed collision in this engagement has done), this module names
its own record `ShadowResultRecord` -- never imported from, or
interchanged with, `production_runtime.runtime.ShadowResult`.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

SCHEMA_VERSION = "1.0.0"
EVENT_SHADOW_RESULT_RECORDED = "SHADOW_RESULT_RECORDED"

# -- ShadowResultRecord.pipeline_stage_reached ---------------------------
# The furthest real stage this cycle's decision actually reached --
# never a stage that was skipped or never evaluated.
STAGE_DECISION_ONLY = "DECISION_ONLY"                # rejected/not-evaluated before Risk Context.
STAGE_RISK_EVALUATED = "RISK_EVALUATED"              # Risk Context Adapter ran; may still be RESTRICTED/UNAVAILABLE.
STAGE_EXECUTION_PLANNED = "EXECUTION_PLANNED"        # ExecutionIntent + ExecutionPlan built.
STAGE_BROKER_ROUTED = "STAGE_BROKER_ROUTED"          # a BrokerResponse was obtained.
STAGE_RECONCILED = "STAGE_RECONCILED"                # a ReconciliationResult was obtained.
ALL_PIPELINE_STAGES = (
    STAGE_DECISION_ONLY, STAGE_RISK_EVALUATED, STAGE_EXECUTION_PLANNED, STAGE_BROKER_ROUTED, STAGE_RECONCILED,
)


def shadow_result_id_for(strategy_name: str, timestamp: str) -> str:
    """Deterministic, collision-resistant, replay-safe -- same MD5-based
    convention `bujji.market_memory.models.memory_id_for()` (Phase
    20.15) already established, mirrored here rather than imported
    (a different lineage's own identity key)."""
    return "SHDRES-" + hashlib.md5(f"{strategy_name}:{timestamp}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class ShadowResultRecord:
    """One composed, persisted record per Cycle-1 decision cycle,
    spanning Decision -> Risk Context -> Execution Intent -> Paper
    Request -> Fill Simulation -> Reconciliation. Every field is
    either a real, already-computed value copied verbatim from an
    upstream stage's own output, or honestly `None` when that stage
    was never reached -- never fabricated, never recomputed.

    This record NEVER carries `evidence_score`/`effective_score`/
    `priority_score`/`rank`/`allocation_class` -- those remain owned
    by their originating stages; recomputing or duplicating them here
    would risk silent drift from the real values."""

    record_id: str
    session_id: str
    strategy_name: str
    timestamp: str

    decision_state: str
    pipeline_stage_reached: str

    risk_context_status: Optional[str]
    execution_intent_created: bool
    execution_simulation_required: Optional[bool]
    broker_response_status: Optional[str]
    broker_adapter_name: Optional[str]
    execution_result_status: Optional[str]          # FILLED/PARTIAL/REJECTED, from ExecutionResult, if any.
    reconciliation_consistency: Optional[str]

    def __post_init__(self) -> None:
        if self.pipeline_stage_reached not in ALL_PIPELINE_STAGES:
            raise ValueError(f"pipeline_stage_reached={self.pipeline_stage_reached!r} not in {ALL_PIPELINE_STAGES}")

    def to_dict(self) -> dict:
        return {
            "record_id": self.record_id, "session_id": self.session_id, "strategy_name": self.strategy_name,
            "timestamp": self.timestamp, "decision_state": self.decision_state,
            "pipeline_stage_reached": self.pipeline_stage_reached,
            "risk_context_status": self.risk_context_status,
            "execution_intent_created": self.execution_intent_created,
            "execution_simulation_required": self.execution_simulation_required,
            "broker_response_status": self.broker_response_status,
            "broker_adapter_name": self.broker_adapter_name,
            "execution_result_status": self.execution_result_status,
            "reconciliation_consistency": self.reconciliation_consistency,
        }

    @staticmethod
    def from_dict(d: dict) -> "ShadowResultRecord":
        return ShadowResultRecord(
            record_id=d["record_id"], session_id=d["session_id"], strategy_name=d["strategy_name"],
            timestamp=d["timestamp"], decision_state=d["decision_state"],
            pipeline_stage_reached=d["pipeline_stage_reached"],
            risk_context_status=d.get("risk_context_status"),
            execution_intent_created=d["execution_intent_created"],
            execution_simulation_required=d.get("execution_simulation_required"),
            broker_response_status=d.get("broker_response_status"),
            broker_adapter_name=d.get("broker_adapter_name"),
            execution_result_status=d.get("execution_result_status"),
            reconciliation_consistency=d.get("reconciliation_consistency"),
        )
