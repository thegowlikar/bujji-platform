"""Position Intelligence — Thesis Monitoring. Pure models, no IO, no
execution, no broker.

Given a hypothetical position entered from a real ShadowTradeCandidate
(Phase 14), and a LATER real intelligence_cycle record, answers: "is the
reason I entered this hypothetical trade still valid?" Never HOLDs,
EXITs, ADJUSTs, or otherwise acts -- this layer only observes and
reports. Per the mission's own explicit instruction: "Do NOT
automatically close the position merely because the thesis changed
unless an explicit existing exit rule says so. The first version should
primarily observe and report."
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

THESIS_INTACT = "THESIS_INTACT"
THESIS_WEAKENING = "THESIS_WEAKENING"
THESIS_INVALIDATED = "THESIS_INVALIDATED"
THESIS_UNKNOWN = "THESIS_UNKNOWN"

ALL_THESIS_STATUSES = (THESIS_INTACT, THESIS_WEAKENING, THESIS_INVALIDATED, THESIS_UNKNOWN)

CHECK_CONSISTENT = "CONSISTENT"
CHECK_DEVIATED = "DEVIATED"
CHECK_UNKNOWN = "UNKNOWN"

RECOMMEND_HOLD = "HOLD"
RECOMMEND_EXIT = "EXIT"
RECOMMEND_ADJUST = "ADJUST"
RECOMMEND_UNKNOWN = "UNKNOWN"

ALL_RECOMMENDATIONS = (RECOMMEND_HOLD, RECOMMEND_EXIT, RECOMMEND_ADJUST, RECOMMEND_UNKNOWN)


@dataclass(frozen=True)
class PositionEntrySnapshot:
    """The entry-time record of what Bujji believed, captured directly
    from a real ShadowTradeCandidate -- never re-derived, never guessed.

    Phase 15F additive fields: `entry_greeks`/`entry_premium_behaviour`
    -- the SAME real intelligence_cycle record's own "greeks"/
    "premium_behaviour" dicts (Phase 15E), captured at entry time
    verbatim, never re-derived. Both default to None so every
    pre-Phase-15F caller/candidate hydrates safely as UNKNOWN, never a
    fabricated zero -- `ShadowTradeCandidate` itself is NOT modified;
    these are captured by `build_entry_snapshot`'s new optional
    parameter, keeping the protected Phase 14 model untouched."""

    candidate_id: str
    strategy_family: str
    entry_timestamp: str
    entry_regime: Optional[str]
    entry_direction: Optional[str]
    entry_volatility_regime: Optional[str]
    entry_consensus_state: Optional[str]
    entry_liquidity_tightness: Optional[str]
    entry_greeks: Optional[dict] = None
    entry_premium_behaviour: Optional[dict] = None

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "strategy_family": self.strategy_family,
            "entry_timestamp": self.entry_timestamp, "entry_regime": self.entry_regime,
            "entry_direction": self.entry_direction, "entry_volatility_regime": self.entry_volatility_regime,
            "entry_consensus_state": self.entry_consensus_state,
            "entry_liquidity_tightness": self.entry_liquidity_tightness,
            "entry_greeks": self.entry_greeks, "entry_premium_behaviour": self.entry_premium_behaviour,
        }


@dataclass(frozen=True)
class ThesisCheck:
    dimension: str
    entry_value: Optional[str]
    current_value: Optional[str]
    status: str
    reason: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "entry_value": self.entry_value,
            "current_value": self.current_value, "status": self.status, "reason": self.reason,
        }


@dataclass(frozen=True)
class ThesisEvaluation:
    candidate_id: str
    evaluation_timestamp: str
    thesis_status: str
    checks: Tuple[ThesisCheck, ...]
    recommendation: str
    recommendation_reason: str
    # Phase 15F additive field: how much of this evaluation is actually
    # grounded in resolved (non-UNKNOWN) evidence -- HIGH/MODERATE/LOW/
    # NONE, purely a function of resolved-check fraction. Default None
    # (not computed) for any caller constructing this dataclass directly
    # without going through evaluate_thesis().
    evidence_confidence: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "candidate_id": self.candidate_id, "evaluation_timestamp": self.evaluation_timestamp,
            "thesis_status": self.thesis_status, "checks": [c.to_dict() for c in self.checks],
            "recommendation": self.recommendation, "recommendation_reason": self.recommendation_reason,
            "evidence_confidence": self.evidence_confidence,
        }
