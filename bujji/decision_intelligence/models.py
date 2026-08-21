"""Decision Intelligence -- Phase 19.6. Pure models, no IO, no broker,
no execution.

`DecisionIntelligenceSnapshot` reasons over already-validated layers
(`MarketIntelligenceSnapshot` Phase 19.3, `DecisionContext` Phase 19.4,
`MarketMemoryEntry`/similarity Phase 19.5). It answers "what decisions
are rational, what risks exist, what evidence supports them, and what
would invalidate them" -- and structurally CANNOT answer "buy/sell what,
how much, at what price": no field on this object is shaped like an
order, a quantity, a price, or a CE/PE instrument selection. Verified by
`test_no_execution_vocabulary_in_decision_intelligence`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional, Tuple


class DecisionPosture(str, Enum):
    """Environmental states, never trades -- expands
    `MarketPosture` (Phase 19.3, "what kind of environment is this")
    into a decision-adjacent but still action-free vocabulary ("what
    should Bujji's attention be doing right now")."""

    OBSERVE = "OBSERVE"
    WAIT_FOR_CONFIRMATION = "WAIT_FOR_CONFIRMATION"
    FAVOR_PREMIUM_ENVIRONMENT = "FAVOR_PREMIUM_ENVIRONMENT"
    FAVOR_DIRECTIONAL_ENVIRONMENT = "FAVOR_DIRECTIONAL_ENVIRONMENT"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    INSUFFICIENT_INFORMATION = "INSUFFICIENT_INFORMATION"


@dataclass(frozen=True)
class EnvironmentAssessment:
    """A plain, mechanically composed label plus the exact evidence
    references it was built from -- never free-form generated text."""

    state: str
    supporting_evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"state": self.state, "supporting_evidence": list(self.supporting_evidence)}


@dataclass(frozen=True)
class StrategyFamilyAssessment:
    """Carries `DecisionContext`'s own already-computed compatibility
    forward -- never recomputed, never reinterpreted. This layer only
    explains and contextualizes what Phase 19.4 already concluded."""

    compatible_strategy_families: Tuple[str, ...]
    blocked_strategy_families: Tuple[str, ...]
    reasoning: str

    def to_dict(self) -> dict:
        return {
            "compatible_strategy_families": list(self.compatible_strategy_families),
            "blocked_strategy_families": list(self.blocked_strategy_families),
            "reasoning": self.reasoning,
        }


@dataclass(frozen=True)
class MemoryContext:
    """Historical context -- observation, never a prediction. `statistic`
    is None unless at least `MIN_SAMPLES_FOR_STATISTIC` real matched
    entries with a KNOWN outcome observation exist; when populated it is
    a plain fraction of REAL samples ("3 of 4 matched states..."), never
    a synthesized percentage-with-false-precision."""

    matched_count: int
    matched_market_memory_ids: Tuple[str, ...]
    confidence_note: str
    statistic: Optional[str]  # e.g. "3 of 4 matched states showed volatility expansion afterward" -- or None.

    def to_dict(self) -> dict:
        return {
            "matched_count": self.matched_count,
            "matched_market_memory_ids": list(self.matched_market_memory_ids),
            "confidence_note": self.confidence_note,
            "statistic": self.statistic,
        }


@dataclass(frozen=True)
class RiskObservation:
    description: str
    supporting_evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"description": self.description, "supporting_evidence": list(self.supporting_evidence)}


@dataclass(frozen=True)
class OpportunityObservation:
    description: str
    supporting_evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {"description": self.description, "supporting_evidence": list(self.supporting_evidence)}


@dataclass(frozen=True)
class UncertaintyObservation:
    description: str
    reason: str

    def to_dict(self) -> dict:
        return {"description": self.description, "reason": self.reason}


@dataclass(frozen=True)
class ContradictionObservation:
    """The `mil_next`-derived concept (Phase 19.1.2's evaluation, concept
    reused, code never imported): two real, evidence-backed conclusions
    that pull in opposite directions -- never resolved automatically,
    only surfaced."""

    description: str
    dimensions_involved: Tuple[str, ...]
    supporting_evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "description": self.description,
            "dimensions_involved": list(self.dimensions_involved),
            "supporting_evidence": list(self.supporting_evidence),
        }


@dataclass(frozen=True)
class DecisionIntelligenceSnapshot:
    """"Given the current market state, what decisions are rational, what
    risks exist, what evidence supports them, and what conditions would
    invalidate them" -- reasoning only, never an order.

    IDENTITY: `decision_intelligence_id` follows `MarketIntelligenceSnapshot`'s
    own precedent (Phase 19.3) -- a stored, content-derived fingerprint,
    set once at build time, never the bare word "snapshot_id".
    `decision_context_id` is likewise a CONTENT FINGERPRINT of the real
    `DecisionContext` this snapshot reasoned from (`DecisionContext`
    itself, Phase 19.4, carries no stored id of its own -- the same
    "computed identity, not a second stored field on an upstream object"
    discipline `MarketRealitySnapshot.fingerprint()` already established
    for Reality, applied here instead of retrofitting a new field onto
    Phase 19.4's already-shipped, frozen model).
    """

    decision_intelligence_id: str
    created_at: datetime                          # audit metadata -- excluded from the fingerprint.
    market_intelligence_snapshot_id: str
    decision_context_id: str

    environment_assessment: EnvironmentAssessment
    strategy_family_assessment: StrategyFamilyAssessment
    memory_context: MemoryContext
    risk_observations: Tuple[RiskObservation, ...]
    opportunity_observations: Tuple[OpportunityObservation, ...]
    uncertainty_observations: Tuple[UncertaintyObservation, ...]
    contradictions: Tuple[ContradictionObservation, ...]
    recommended_posture: DecisionPosture
    invalidation_conditions: Tuple[str, ...]
    evidence_bundle: Dict[str, Any]                # DecisionEvidenceBundle.to_dict() -- see evidence.py.

    def fingerprint_payload(self) -> Dict[str, Any]:
        """Excludes `created_at` and `decision_intelligence_id` itself --
        the same treatment `MarketIntelligenceSnapshot.fingerprint_payload()`
        already gives its own audit metadata (Phase 19.3)."""
        return {
            "market_intelligence_snapshot_id": self.market_intelligence_snapshot_id,
            "decision_context_id": self.decision_context_id,
            "environment_assessment": self.environment_assessment.to_dict(),
            "strategy_family_assessment": self.strategy_family_assessment.to_dict(),
            "memory_context": self.memory_context.to_dict(),
            "risk_observations": [r.to_dict() for r in self.risk_observations],
            "opportunity_observations": [o.to_dict() for o in self.opportunity_observations],
            "uncertainty_observations": [u.to_dict() for u in self.uncertainty_observations],
            "contradictions": [c.to_dict() for c in self.contradictions],
            "recommended_posture": self.recommended_posture.value,
            "invalidation_conditions": list(self.invalidation_conditions),
            "evidence_bundle": self.evidence_bundle,
        }

    def fingerprint(self) -> str:
        """Reuses `replay_engine.engine.fingerprint_state()` verbatim --
        the same mechanism every identity in this project's Reality/
        Intelligence tiers already uses (Phase 18.3 / Phase 19.3), never
        a new hashing scheme."""
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        d = self.fingerprint_payload()
        d["decision_intelligence_id"] = self.decision_intelligence_id
        d["created_at"] = self.created_at.isoformat()
        return d
