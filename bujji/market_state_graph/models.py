"""Market State Graph -- Phase 19.8. Pure models, no IO, no broker, no
execution, no strategy vocabulary.

`MarketStateNode` answers "where are we in the market's evolution" --
one node per real cycle, linked to its predecessor by
`previous_state_id`, with an optional `transition` describing exactly
how conditions changed (never a prediction of what changes next).

Named `MarketStateNode`, not `MarketState` -- `bujji.market_state.MarketState`
already exists (a different, older MSI-pipeline concept, Phase 19.8's
own audit finding) and this phase deliberately avoids colliding with
it, both in class name and in package name (`market_state_graph`, not
a submodule of `market_state`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Same NONE/LOW/MODERATE/HIGH scale used project-wide (msi_strategy_selection_foundation,
# market_phenomena) -- reused, not reinvented.
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)

EVENT_MARKET_STATE_NODE_RECORDED = "MARKET_STATE_NODE_RECORDED"


@dataclass(frozen=True)
class StateTransitionEdge:
    """"Conditions changed from A to B" -- never "will happen next."
    Observation confidence (what we know happened) and interpretation
    confidence (what we think the transition means) are two SEPARATE
    fields, never combined into one number -- per this phase's own
    explicit instruction."""

    transition_type: str             # transitions.ALL_STATE_TRANSITION_TYPES
    from_state_id: Optional[str]
    to_state_id: str
    observation_confidence: str      # ALL_CONFIDENCE_LEVELS -- how solid is the underlying measurement.
    interpretation_confidence: str   # ALL_CONFIDENCE_LEVELS -- how strongly the evidence supports THIS label.
    evidence: Tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "transition_type": self.transition_type,
            "from_state_id": self.from_state_id, "to_state_id": self.to_state_id,
            "observation_confidence": self.observation_confidence,
            "interpretation_confidence": self.interpretation_confidence,
            "evidence": list(self.evidence),
        }

    def fingerprint_payload(self) -> dict:
        """Excludes `to_state_id` -- it is always the CONTAINING node's
        own `state_id` by construction (an edge always points to the
        node it is attached to), so hashing it would make
        `MarketStateNode.fingerprint()` depend on a value only known
        AFTER the hash is computed. Same "exclude the self-referential/
        audit-only field" treatment `created_at` already gets everywhere
        else in this project."""
        return {
            "transition_type": self.transition_type, "from_state_id": self.from_state_id,
            "observation_confidence": self.observation_confidence,
            "interpretation_confidence": self.interpretation_confidence,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True)
class MarketStateNode:
    """One real cycle's position in the market's evolution. Every field
    is copied verbatim from an already-validated upstream layer
    (MarketIntelligenceSnapshot Phase 19.3, DecisionIntelligenceSnapshot
    Phase 19.6, MarketPhenomenaAssessment Phase 19.7) -- this object
    never recomputes a metric, only links and sequences what already
    exists."""

    state_id: str
    timestamp: str                    # isoformat -- the snapshot's own as_of_time, never wall-clock.
    regime: Optional[str]
    phenomena: Tuple[str, ...]
    volatility_state: Optional[str]
    liquidity_state: Optional[str]
    event_state: str                  # "EVENT_RISK" or "NORMAL" -- from market_phenomena's own EVENT_RISK detection.
    decision_posture: str
    evidence_bundle: Dict[str, Any]
    previous_state_id: Optional[str]
    transition: Optional[StateTransitionEdge]

    def fingerprint_payload(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp, "regime": self.regime, "phenomena": list(self.phenomena),
            "volatility_state": self.volatility_state, "liquidity_state": self.liquidity_state,
            "event_state": self.event_state, "decision_posture": self.decision_posture,
            "evidence_bundle": self.evidence_bundle, "previous_state_id": self.previous_state_id,
            "transition": self.transition.fingerprint_payload() if self.transition else None,
        }

    def fingerprint(self) -> str:
        """Reuses `replay_engine.engine.fingerprint_state()` verbatim --
        the same mechanism every identity in this project's Reality/
        Intelligence/Decision/Phenomena tiers already uses (Phase 18.3 /
        19.3 / 19.6 / 19.7), never a new hashing scheme."""
        from bujji.replay_engine.engine import fingerprint_state
        return fingerprint_state(self.fingerprint_payload())

    def to_dict(self) -> dict:
        """Full serialization for persistence -- unlike `fingerprint_payload()`,
        includes `transition.to_state_id` (needed for a real round-trip
        via `from_dict()`; excluded only from the HASH, never from
        storage)."""
        return {
            "state_id": self.state_id, "timestamp": self.timestamp, "regime": self.regime,
            "phenomena": list(self.phenomena), "volatility_state": self.volatility_state,
            "liquidity_state": self.liquidity_state, "event_state": self.event_state,
            "decision_posture": self.decision_posture, "evidence_bundle": self.evidence_bundle,
            "previous_state_id": self.previous_state_id,
            "transition": self.transition.to_dict() if self.transition else None,
        }

    @staticmethod
    def from_dict(d: dict) -> "MarketStateNode":
        transition = d.get("transition")
        return MarketStateNode(
            state_id=d["state_id"], timestamp=d["timestamp"], regime=d.get("regime"),
            phenomena=tuple(d.get("phenomena", ())), volatility_state=d.get("volatility_state"),
            liquidity_state=d.get("liquidity_state"), event_state=d["event_state"],
            decision_posture=d["decision_posture"], evidence_bundle=d.get("evidence_bundle", {}),
            previous_state_id=d.get("previous_state_id"),
            transition=StateTransitionEdge(**{**transition, "evidence": tuple(transition["evidence"])}) if transition else None,
        )
