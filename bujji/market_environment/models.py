"""Market Environment Intelligence -- Phase 19.9. Pure models, no IO,
no broker, no execution, no strategy selection, no order vocabulary.

`MarketEnvironmentAssessment` answers "what type of trading environment
exists right now" -- the bridge between market understanding (Phase
19.3-19.8) and a future strategy-selection layer (Phase 20+). Never
chooses a strategy, never generates a trade -- only classifies the
environment into one of five named, evidence-backed categories.

Named `MarketEnvironmentAssessment`, not `EnvironmentAssessment` --
`bujji.decision_intelligence.models.EnvironmentAssessment` (Phase 19.6)
already exists as a different, narrower object (a plain mechanical
state label). This phase's own audit
(`docs/PHASE_19_9_MARKET_ENVIRONMENT_INTELLIGENCE_AUDIT.md`) confirmed
the distinction and the naming choice deliberately avoids colliding
with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class EnvironmentType(str, Enum):
    """Only evidence-backed categories -- never BUY/SELL, never a
    specific strategy. "Environment supports premium selling," never
    "sell put." """

    PREMIUM_SELLING_FAVOURABLE = "PREMIUM_SELLING_FAVOURABLE"
    PREMIUM_SELLING_UNFAVOURABLE = "PREMIUM_SELLING_UNFAVOURABLE"
    TREND_FOLLOWING_FAVOURABLE = "TREND_FOLLOWING_FAVOURABLE"
    MEAN_REVERSION_FAVOURABLE = "MEAN_REVERSION_FAVOURABLE"
    STAND_ASIDE = "STAND_ASIDE"


# Same NONE/LOW/MODERATE/HIGH scale used project-wide -- reused, not reinvented.
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)


@dataclass(frozen=True)
class MarketEnvironmentAssessment:
    """"Environment supports premium selling" -- never "sell NIFTY
    24500 CE." `confidence` here is ENVIRONMENT confidence ("how sure
    are we this classification applies"), a SEPARATE field from the
    OBSERVATION confidence already carried inside `evidence_bundle`
    (Phase 19.6's own min-across-brains confidence) -- the two are
    never merged into one number, per this phase's own explicit
    instruction.
    """

    environment_id: str
    created_at: datetime                  # audit metadata -- excluded from the fingerprint.
    market_state_id: str
    environment_type: EnvironmentType
    confidence: str                        # ALL_CONFIDENCE_LEVELS -- ENVIRONMENT confidence, never observation confidence.
    supporting_conditions: Tuple[str, ...]
    blocking_conditions: Tuple[str, ...]
    historical_similarity: Dict[str, Any]  # decision_intelligence.memory_context.to_dict(), verbatim -- Phase 19.5/19.6's own observation-only discipline.
    decision_posture: str
    evidence_bundle: Dict[str, Any]

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "market_state_id": self.market_state_id, "environment_type": self.environment_type.value,
            "confidence": self.confidence,
            "supporting_conditions": list(self.supporting_conditions),
            "blocking_conditions": list(self.blocking_conditions),
            "historical_similarity": self.historical_similarity,
            "decision_posture": self.decision_posture, "evidence_bundle": self.evidence_bundle,
        }

    def fingerprint(self) -> str:
        """Reuses `replay_engine.engine.fingerprint_state()` verbatim --
        the same mechanism every identity in this project's Reality/
        Intelligence/Decision/Phenomena/State-Graph tiers already uses
        (Phase 18.3 / 19.3 / 19.6 / 19.7 / 19.8), never a new hashing
        scheme."""
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["environment_id"] = self.environment_id
        d["created_at"] = self.created_at.isoformat()
        return d
