"""Market Phenomena Recognition -- Phase 19.7. Pure models, no IO, no
broker, no execution, no strategy vocabulary.

Converts "market measurements" (the six brain Readings, already
composed into `MarketIntelligenceSnapshot`/`DecisionIntelligenceSnapshot`,
Phase 19.3/19.6) into "market situation understanding" -- what kind of
event/process is currently unfolding. Never a strategy, never a trade,
never a prediction of what happens next (that distinction belongs to
Phase 19.5's Outcome Memory, which records what actually happened
later as a separate, factual fact).

Vocabulary reused, per this phase's own audit
(`docs/PHASE_19_7_MARKET_PHENOMENA_RECOGNITION_AUDIT.md`): the
`VOLATILITY_EXPANSION`/`VOLATILITY_COMPRESSION` type strings are the
exact same values `bujji.msi_market_phenomena.taxonomy` already uses
for the same real-world concept, detected here from a different,
already-validated evidence source. Code is not shared -- different
input pipeline -- see the audit doc for why.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

PHENOMENON_VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
PHENOMENON_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
PHENOMENON_LIQUIDITY_STRESS = "LIQUIDITY_STRESS"
PHENOMENON_EVENT_RISK = "EVENT_RISK"
PHENOMENON_REGIME_TRANSITION = "REGIME_TRANSITION"

ALL_PHENOMENON_TYPES = (
    PHENOMENON_VOLATILITY_COMPRESSION, PHENOMENON_VOLATILITY_EXPANSION,
    PHENOMENON_LIQUIDITY_STRESS, PHENOMENON_EVENT_RISK, PHENOMENON_REGIME_TRANSITION,
)

# Same NONE/LOW/MODERATE/HIGH scale used project-wide (e.g.
# msi_strategy_selection_foundation.taxonomy.CONFIDENCE_*) -- reused,
# not reinvented.
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)


@dataclass(frozen=True)
class PhenomenonEvidenceItem:
    """One real, named piece of evidence -- never free-form text. `value`
    is copied verbatim from the Reading that produced it, `source` names
    exactly which Reading (`"RegimeReading"`, `"VolatilityReading"`,
    `"LiquidityReading"`, `"EventReading"`) -- matches this phase's own
    worked example exactly."""

    metric: str
    value: Any
    source: str

    def to_dict(self) -> dict:
        return {"metric": self.metric, "value": self.value, "source": self.source}


@dataclass(frozen=True)
class MarketPhenomenonAssessment:
    """One real, causally-detected phenomenon. Never a recommendation,
    never a strategy hint, never BUY/SELL/entry/exit -- only an
    objective, evidence-cited description of what real Intelligence-
    layer evidence showed."""

    phenomenon_id: str
    phenomenon_type: str          # one of ALL_PHENOMENON_TYPES.
    confidence: str                # one of ALL_CONFIDENCE_LEVELS.
    state: str                     # a plain, mechanically composed description -- never free text.
    supporting_evidence: Tuple[PhenomenonEvidenceItem, ...]
    contradicting_evidence: Tuple[PhenomenonEvidenceItem, ...]
    intelligence_snapshot_id: str
    decision_intelligence_id: Optional[str]

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "phenomenon_type": self.phenomenon_type, "confidence": self.confidence, "state": self.state,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "intelligence_snapshot_id": self.intelligence_snapshot_id,
            "decision_intelligence_id": self.decision_intelligence_id,
        }

    def fingerprint(self) -> str:
        """Reuses `replay_engine.engine.fingerprint_state()` verbatim --
        the same mechanism every identity in this project's Reality/
        Intelligence/Decision tiers already uses (Phase 18.3 / 19.3 /
        19.6), never a new hashing scheme."""
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["phenomenon_id"] = self.phenomenon_id
        return d


@dataclass(frozen=True)
class MarketPhenomenaAssessment:
    """The one report-level object this phase produces for one real
    cycle -- mirrors `msi_market_phenomena.MarketPhenomenaReport`'s own
    discipline: `not_detected` is disclosed every time, with a reason,
    never silently omitted."""

    assessment_id: str
    created_at: datetime                             # audit metadata -- excluded from the fingerprint.
    intelligence_snapshot_id: str
    decision_intelligence_id: Optional[str]
    phenomena: Tuple[MarketPhenomenonAssessment, ...]
    not_detected: Tuple[str, ...]                     # ALL_PHENOMENON_TYPES minus phenomena actually detected.
    not_detected_reason: str

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "intelligence_snapshot_id": self.intelligence_snapshot_id,
            "decision_intelligence_id": self.decision_intelligence_id,
            "phenomena": [p.fingerprint_payload() for p in self.phenomena],
            "not_detected": list(self.not_detected),
            "not_detected_reason": self.not_detected_reason,
        }

    def fingerprint(self) -> str:
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["assessment_id"] = self.assessment_id
        d["created_at"] = self.created_at.isoformat()
        d["phenomena"] = [p.to_dict() for p in self.phenomena]
        return d
