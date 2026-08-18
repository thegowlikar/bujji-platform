"""Phase 20.11 -- pure data contracts. No IO, no broker, no execution,
no order/position/lot-sizing/fill-price/broker-state vocabulary
anywhere in this module -- this is intelligence memory only, never a
trade record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from bujji.decision_orchestration import ALL_DECISION_STATES
from bujji.epistemics.uncertainty import ALL_CONFIDENCE


@dataclass(frozen=True)
class DecisionObservation:
    """"What did Bujji believe at this moment?" One evaluation cycle,
    for one candidate strategy. Every field is read from an
    already-computed upstream layer (Phase 20.1/20.5/20.8/20.9/20.10)
    -- nothing here is derived or recalculated; this is a snapshot,
    not a new judgement."""

    observation_id: str
    timestamp: str                      # ISO 8601, caller-supplied -- never wall-clock.
    market_state: Dict[str, object]      # bujji.mic_v0.models.MarketState.to_dict(), verbatim.
    candidate_strategy: Optional[str]
    decision_state: str                  # bujji.decision_orchestration.ALL_DECISION_STATES
    priority_score: Optional[float]      # Phase 20.7's own value, carried through Phase 20.8.
    allocation_class: Optional[str]      # Phase 20.8's own RiskAllocationClass.
    confidence: Optional[str]            # Phase 20.5's own epistemics confidence label, or None if unavailable.
    reason_codes: Tuple[str, ...]        # Phase 20.10's own FinalDecision.positive + .negative, verbatim.
    uncertainty: Tuple[str, ...]         # Phase 20.10's own FinalDecision.unknown, verbatim.
    data_quality: str                    # Phase 20.1's own MarketState.data_quality.

    def __post_init__(self) -> None:
        if not self.observation_id:
            raise ValueError("observation_id cannot be empty")
        if not self.timestamp:
            raise ValueError("timestamp cannot be empty")
        if self.decision_state not in ALL_DECISION_STATES:
            raise ValueError(f"decision_state={self.decision_state!r} not in {ALL_DECISION_STATES}")
        if self.confidence is not None and self.confidence not in ALL_CONFIDENCE:
            raise ValueError(f"confidence={self.confidence!r} not in {ALL_CONFIDENCE}")

    def to_dict(self) -> dict:
        return {
            "observation_id": self.observation_id,
            "timestamp": self.timestamp,
            "market_state": self.market_state,
            "candidate_strategy": self.candidate_strategy,
            "decision_state": self.decision_state,
            "priority_score": self.priority_score,
            "allocation_class": self.allocation_class,
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "uncertainty": list(self.uncertainty),
            "data_quality": self.data_quality,
        }


@dataclass(frozen=True)
class SessionDecisionSummary:
    """One session's aggregate -- counts and distributions only, never
    a re-judgement of any individual observation."""

    session_date: str
    number_of_cycles: int
    candidate_count: int
    decision_distribution: Dict[str, int]
    highest_confidence_decision: Optional[str]   # candidate_strategy of the highest-confidence observation, if any.
    blocked_count: int
    watch_count: int
    no_opportunity_count: int
    uncertainty_summary: Dict[str, int]           # distinct uncertainty reason -> occurrence count.

    def render(self) -> str:
        lines = [
            f"Session {self.session_date}: {self.number_of_cycles} cycles, "
            f"{self.candidate_count} distinct candidates",
            f"  Decision distribution: {self.decision_distribution}",
            f"  BLOCKED={self.blocked_count} WATCH={self.watch_count} NO_OPPORTUNITY={self.no_opportunity_count}",
            f"  Highest-confidence decision: {self.highest_confidence_decision or 'none'}",
        ]
        if self.uncertainty_summary:
            lines.append("  Uncertainty summary:")
            for reason, count in self.uncertainty_summary.items():
                lines.append(f"    ({count}x) {reason}")
        return "\n".join(lines)
