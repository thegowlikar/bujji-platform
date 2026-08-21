"""Phase 20.15 -- pure data contracts. No IO, no broker, no execution,
no strategy-scoring/tuning logic anywhere in this module.

Three record types, one shared `memory_id` linking them:

    MarketMemoryRecord   -- what the market conditions were.
    DecisionMemoryRecord -- what Bujji decided, given those conditions.
    OutcomeMemoryRecord  -- what the market conditions looked like
                             later (`status=NOT_YET_OBSERVED` until a
                             real later cycle is actually supplied --
                             never guessed, never P&L, never a trade
                             result; Cycle 1 has no positions).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple

SCHEMA_VERSION = "1.0.0"

EVENT_MARKET_MEMORY_RECORDED = "MARKET_MEMORY_RECORDED"
EVENT_DECISION_MEMORY_RECORDED = "DECISION_MEMORY_RECORDED"
EVENT_OUTCOME_MEMORY_RECORDED = "OUTCOME_MEMORY_RECORDED"

STATUS_KNOWN = "KNOWN"                     # a real later observation exists.
STATUS_NOT_YET_OBSERVED = "NOT_YET_OBSERVED"  # honest absence -- not enough time has passed yet.
ALL_OUTCOME_STATUSES = (STATUS_KNOWN, STATUS_NOT_YET_OBSERVED)


def memory_id_for(observation_id: str) -> str:
    """Deterministic, collision-resistant, replay-safe -- same MD5-based
    convention `bujji.market_understanding.memory_models.
    market_memory_id_for()` (Phase 19.5) and `bujji.outcome_memory.
    models.memory_id_for()` (Phase 15N) already established, mirrored
    here rather than imported (both are keyed to a different lineage's
    own identity, per this phase's own Step 1 audit)."""
    return "MKTMEM-" + hashlib.md5(observation_id.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class MarketMemoryRecord:
    """"What the market conditions were" -- read DIRECTLY from Phase
    20.1's own `MarketState.to_dict()` (via Phase 20.11's
    `DecisionObservation.market_state`), never recomputed."""

    memory_id: str
    as_of_time: str
    market_regime: str
    volatility_state: str
    risk_state: str
    data_quality: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id, "as_of_time": self.as_of_time,
            "market_regime": self.market_regime, "volatility_state": self.volatility_state,
            "risk_state": self.risk_state, "data_quality": self.data_quality,
            "evidence": list(self.evidence),
        }

    @staticmethod
    def from_dict(d: dict) -> "MarketMemoryRecord":
        return MarketMemoryRecord(
            memory_id=d["memory_id"], as_of_time=d["as_of_time"], market_regime=d["market_regime"],
            volatility_state=d["volatility_state"], risk_state=d["risk_state"],
            data_quality=d["data_quality"], evidence=tuple(d.get("evidence", ())),
        )


@dataclass(frozen=True)
class DecisionMemoryRecord:
    """"What Bujji decided, given those conditions" -- read DIRECTLY
    from Phase 20.11's `DecisionObservation`, never recomputed. No
    `qualification_state` field: `DecisionObservation` itself does not
    carry that value forward from Phase 20.6 (only `decision_state`,
    `allocation_class`, `confidence`) -- reconstructing it from
    `reason_codes` text would be a fabrication this package's own
    charter forbids, so it is honestly omitted rather than guessed."""

    memory_id: str
    as_of_time: str
    candidate_strategy: Optional[str]
    decision_state: str
    confidence: Optional[str]
    priority_score: Optional[float]
    allocation_class: Optional[str]
    reason_codes: Tuple[str, ...]
    uncertainty: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id, "as_of_time": self.as_of_time,
            "candidate_strategy": self.candidate_strategy, "decision_state": self.decision_state,
            "confidence": self.confidence, "priority_score": self.priority_score,
            "allocation_class": self.allocation_class,
            "reason_codes": list(self.reason_codes), "uncertainty": list(self.uncertainty),
        }

    @staticmethod
    def from_dict(d: dict) -> "DecisionMemoryRecord":
        return DecisionMemoryRecord(
            memory_id=d["memory_id"], as_of_time=d["as_of_time"],
            candidate_strategy=d.get("candidate_strategy"), decision_state=d["decision_state"],
            confidence=d.get("confidence"), priority_score=d.get("priority_score"),
            allocation_class=d.get("allocation_class"),
            reason_codes=tuple(d.get("reason_codes", ())), uncertainty=tuple(d.get("uncertainty", ())),
        )


@dataclass(frozen=True)
class OutcomeMemoryRecord:
    """"What happened afterwards" -- a later real `MarketMemoryRecord`/
    `DecisionMemoryRecord` pair for the SAME candidate strategy,
    factually compared against the original. Never a prediction, never
    P&L, never a trade result -- Cycle 1 has no positions. `status`
    stays `NOT_YET_OBSERVED` until a real later cycle is actually
    supplied to `record_outcome()` -- never guessed or interpolated."""

    memory_id: str                          # links back to the ORIGINAL MarketMemoryRecord/DecisionMemoryRecord.
    status: str                             # ALL_OUTCOME_STATUSES
    observed_at: Optional[str] = None
    regime_after: Optional[str] = None
    volatility_state_after: Optional[str] = None
    decision_state_after: Optional[str] = None
    regime_unchanged: Optional[bool] = None   # factual comparison, never a judgement of "correctness."

    def __post_init__(self) -> None:
        if self.status not in ALL_OUTCOME_STATUSES:
            raise ValueError(f"status={self.status!r} not in {ALL_OUTCOME_STATUSES}")
        if self.status == STATUS_NOT_YET_OBSERVED and self.observed_at is not None:
            raise ValueError("NOT_YET_OBSERVED cannot carry a real observed_at -- honest absence only")
        if self.status == STATUS_KNOWN and self.observed_at is None:
            raise ValueError("KNOWN requires a real observed_at -- never fabricated")

    def to_dict(self) -> dict:
        return {
            "memory_id": self.memory_id, "status": self.status, "observed_at": self.observed_at,
            "regime_after": self.regime_after, "volatility_state_after": self.volatility_state_after,
            "decision_state_after": self.decision_state_after, "regime_unchanged": self.regime_unchanged,
        }

    @staticmethod
    def from_dict(d: dict) -> "OutcomeMemoryRecord":
        return OutcomeMemoryRecord(
            memory_id=d["memory_id"], status=d["status"], observed_at=d.get("observed_at"),
            regime_after=d.get("regime_after"), volatility_state_after=d.get("volatility_state_after"),
            decision_state_after=d.get("decision_state_after"), regime_unchanged=d.get("regime_unchanged"),
        )
