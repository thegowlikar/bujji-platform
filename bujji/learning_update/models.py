"""Phase 20.21 -- pure data contracts. No IO, no broker, no execution,
no strategy-scoring/tuning logic anywhere in this module.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

SCHEMA_VERSION = "1.0.0"
EVENT_LEARNING_UPDATE_RECORDED = "LEARNING_UPDATE_RECORDED"

# -- LearningUpdateRecord.learning_classification -------------------------
CONFIRMED_PATTERN = "CONFIRMED_PATTERN"          # decision + execution + reconciliation all real and favorable.
FAILED_PATTERN = "FAILED_PATTERN"                # a real, reconciled simulated fill was REJECTED.
INSUFFICIENT_RESULT = "INSUFFICIENT_RESULT"      # no decision, no risk clearance, or no real execution outcome to learn from.
UNAVAILABLE_DATA = "UNAVAILABLE_DATA"            # risk cleared but the pipeline never reached reconciliation.
CONFLICTING_SIGNAL = "CONFLICTING_SIGNAL"        # a real reconciliation mismatch -- the result cannot be trusted.
ALL_LEARNING_CLASSIFICATIONS = (
    CONFIRMED_PATTERN, FAILED_PATTERN, INSUFFICIENT_RESULT, UNAVAILABLE_DATA, CONFLICTING_SIGNAL,
)


def update_id_for(source_shadow_result_id: str) -> str:
    """Deterministic, collision-resistant, replay-safe -- same MD5-based
    convention `bujji.market_memory.models.memory_id_for()` (Phase
    20.15) and `bujji.shadow_result.models.shadow_result_id_for()`
    (Phase 20.20) already established, mirrored here rather than
    imported (a different lineage's own identity key). One
    `ShadowResultRecord` produces at most one `LearningUpdateRecord` --
    keying on `source_shadow_result_id` alone (not a compound key) is
    therefore correct and gives natural write-idempotency."""
    return "LEARNUPD-" + hashlib.md5(source_shadow_result_id.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class LearningUpdateRecord:
    """A durable, disclosed learning SIGNAL derived from one completed
    `ShadowResultRecord` -- never a strategy, never a re-scored
    opportunity, never a modified evidence/qualification/ranking/
    allocation/risk figure. This record is memory EVIDENCE only; a
    future intelligence cycle decides what (if anything) to do with
    it -- this module never decides on its own behalf.

    Every field except `learning_classification`/`created_at` is a
    value copied verbatim from the source `ShadowResultRecord` -- this
    class performs zero calculation on market, strategy, or execution
    data."""

    update_id: str
    source_shadow_result_id: str
    strategy_family: str
    market_context_signature: Optional[str]
    decision_state: str
    risk_context_state: Optional[str]
    execution_outcome: Optional[str]
    reconciliation_state: Optional[str]
    learning_classification: str
    created_at: str

    def __post_init__(self) -> None:
        if self.learning_classification not in ALL_LEARNING_CLASSIFICATIONS:
            raise ValueError(
                f"learning_classification={self.learning_classification!r} not in {ALL_LEARNING_CLASSIFICATIONS}"
            )

    def to_dict(self) -> dict:
        return {
            "update_id": self.update_id, "source_shadow_result_id": self.source_shadow_result_id,
            "strategy_family": self.strategy_family, "market_context_signature": self.market_context_signature,
            "decision_state": self.decision_state, "risk_context_state": self.risk_context_state,
            "execution_outcome": self.execution_outcome, "reconciliation_state": self.reconciliation_state,
            "learning_classification": self.learning_classification, "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "LearningUpdateRecord":
        return LearningUpdateRecord(
            update_id=d["update_id"], source_shadow_result_id=d["source_shadow_result_id"],
            strategy_family=d["strategy_family"], market_context_signature=d.get("market_context_signature"),
            decision_state=d["decision_state"], risk_context_state=d.get("risk_context_state"),
            execution_outcome=d.get("execution_outcome"), reconciliation_state=d.get("reconciliation_state"),
            learning_classification=d["learning_classification"], created_at=d["created_at"],
        )
