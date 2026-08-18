"""Decision Context Foundation -- Phase 19.4.

Turns a `MarketIntelligenceSnapshot` into "what does this environment
allow / forbid," never "what trade should I place." No order, no fill,
no quantity, no position, no entry/exit signal appears anywhere in this
package -- structurally verified by
`test_no_execution_vocabulary_anywhere_in_decision_context`.

Three objects:
- `StrategyCompatibilityAssessment` -- environmental fit per strategy
  family, reusing `msi_strategy_selection_foundation.taxonomy`'s own
  already-real family names (never invented new ones).
- `MarketStateTransition` -- how posture changed between two snapshots.
- `DecisionContext` -- the bridge object a future strategy engine reads.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot, MarketPosture


class TransitionType(str, Enum):
    COMPRESSION_TO_EXPANSION = "COMPRESSION_TO_EXPANSION"
    RANGE_TO_TREND = "RANGE_TO_TREND"
    TREND_TO_RANGE = "TREND_TO_RANGE"
    NORMAL_TO_EVENT_RISK = "NORMAL_TO_EVENT_RISK"
    UNKNOWN = "UNKNOWN"  # No named transition matched -- includes "no posture change" and any other pairing.


@dataclass(frozen=True)
class StrategyCompatibilityAssessment:
    """Environmental compatibility only -- never an entry signal, never a
    ranking, never a "best" family. A family absent from both tuples is
    `UNASSESSED`: this `MarketIntelligenceSnapshot` genuinely carries no
    evidence to classify it either way (e.g. no direction signal exists
    anywhere in the six in-scope brains, so LONG_DIRECTIONAL/
    SHORT_DIRECTIONAL are always unassessed here) -- never silently
    forced to SUITABLE/UNSUITABLE, matching
    `msi_strategy_selection_foundation`'s own established
    INSUFFICIENT_EVIDENCE discipline."""

    compatible_strategy_families: Tuple[str, ...]
    incompatible_strategy_families: Tuple[str, ...]
    unassessed_strategy_families: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]
    blocking_evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "compatible_strategy_families": list(self.compatible_strategy_families),
            "incompatible_strategy_families": list(self.incompatible_strategy_families),
            "unassessed_strategy_families": list(self.unassessed_strategy_families),
            "supporting_evidence": list(self.supporting_evidence),
            "blocking_evidence": list(self.blocking_evidence),
        }


@dataclass(frozen=True)
class MarketStateTransition:
    """How posture changed between two snapshots -- built from two
    already-real `MarketIntelligenceSnapshot`s, never a new measurement.
    `previous_intelligence_snapshot`/`current_intelligence_snapshot` hold
    the actual snapshot objects (not copies) so a caller can always trace
    a transition back to the exact evidence each side stood on."""

    previous_intelligence_snapshot: MarketIntelligenceSnapshot
    current_intelligence_snapshot: MarketIntelligenceSnapshot
    transition_type: TransitionType
    transition_reason: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "previous_intelligence_snapshot_id": self.previous_intelligence_snapshot.intelligence_snapshot_id,
            "current_intelligence_snapshot_id": self.current_intelligence_snapshot.intelligence_snapshot_id,
            "transition_type": self.transition_type.value,
            "transition_reason": self.transition_reason,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class DecisionContext:
    """The bridge object a future strategy engine reads. Deliberately
    references its source snapshot by id (`intelligence_snapshot_reference`),
    not by embedding the object -- same "reference, don't duplicate
    identity" pattern `IntelligenceContext.reality_snapshot_reference`
    already established in Phase 19.2.2.

    Still: NO order, NO quantity, NO execution. This object answers "what
    is the environment suitable for," never "what should I do.\""""

    intelligence_snapshot_reference: str
    compatible_strategy_families: Tuple[str, ...]
    blocked_strategy_families: Tuple[str, ...]
    confidence: float
    reasoning: str
    evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "intelligence_snapshot_reference": self.intelligence_snapshot_reference,
            "compatible_strategy_families": list(self.compatible_strategy_families),
            "blocked_strategy_families": list(self.blocked_strategy_families),
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "evidence": list(self.evidence),
        }
