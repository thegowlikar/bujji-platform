"""Market Evidence State -- Shadow Campaign v2 Phase 4B.

The new evidence boundary between MarketState (understanding-only) and
any future Shadow Trading Brain. Pure translation: every field is
either copied directly from MarketState/MarketDirectionSummary or
mechanically combined (concatenation/dedup) from their existing
tuples -- no new market calculation, no MSI call, no broker access, no
intelligence runner access, no interpretation, no scoring, no
prediction. See docs discussion in this session's Phase 4B design
report for the full rationale (name chosen to avoid collision with
market_state_builder.MarketStateAssessment,
trading_brain.market_state.MarketStateAssessment, and
EvidenceInterpretation -- all distinct, unrelated objects).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .models import MarketState

# Fixed, deterministic order -- never derived from dict iteration order.
_UNDERSTANDING_FIELDS = ("regime", "direction", "volatility_state", "liquidity_state", "participant_positioning")


@dataclass(frozen=True)
class MarketEvidenceState:
    """ONLY these fields -- no strategy/trade/execution/capital/risk
    field exists here or ever will, enforced by
    tests/test_market_evidence_state_safety_phase4b.py."""

    # Observation context
    timestamp: str
    data_freshness: Optional[str]

    # Market understanding
    regime: Optional[str]
    direction: Optional[str]
    volatility_state: Optional[str]
    liquidity_state: Optional[str]
    participant_positioning: Optional[str]

    # Structure
    price_structure: Optional[str]
    market_structure: Optional[str]
    active_episode_ids: Tuple[str, ...]
    active_event_types: Tuple[str, ...]

    # Confidence
    overall_confidence: str
    direction_confidence: Optional[str]
    uncertainties: Tuple[str, ...]
    missing_evidence: Tuple[str, ...]

    # Provenance
    evidence_ids: Tuple[str, ...]
    source_market_state_timestamp: str


def _missing_evidence(fields_by_name: dict) -> Tuple[str, ...]:
    """Only reports which of the 5 understanding fields are None --
    never infers or explains why (per this phase's own rule)."""
    return tuple(name for name in _UNDERSTANDING_FIELDS if fields_by_name[name] is None)


def build_market_evidence_state(market_state: MarketState) -> MarketEvidenceState:
    """Pure translation of one already-built MarketState. No external
    imports beyond .models, no clock, no broker, no MSI, no
    intelligence runner -- nothing regenerated, nothing guessed."""
    direction_summary = market_state.market_direction

    direction = direction_summary.direction if direction_summary is not None else None
    direction_confidence = direction_summary.confidence if direction_summary is not None else None
    direction_evidence = direction_summary.evidence if direction_summary is not None else ()
    direction_uncertainties = direction_summary.uncertainties if direction_summary is not None else ()

    understanding_values = {
        "regime": market_state.regime,
        "direction": direction,
        "volatility_state": market_state.volatility_state,
        "liquidity_state": market_state.liquidity_state,
        "participant_positioning": market_state.participant_positioning,
    }

    # Uncertainties: plain concatenation, no dedup -- these are free-text
    # human-readable messages, never deduped anywhere else in this
    # codebase either.
    uncertainties = market_state.uncertainties + direction_uncertainties

    # Evidence ids: deduped + sorted, matching the established
    # convention already used by market_state.synthesizer._evidence_ids.
    evidence_ids = tuple(sorted(set(market_state.evidence_ids) | set(direction_evidence)))

    return MarketEvidenceState(
        timestamp=market_state.timestamp,
        data_freshness=None,  # no clock input to this pure translation -- honestly absent, never guessed
        regime=market_state.regime,
        direction=direction,
        volatility_state=market_state.volatility_state,
        liquidity_state=market_state.liquidity_state,
        participant_positioning=market_state.participant_positioning,
        price_structure=market_state.price_structure,
        market_structure=market_state.market_structure,
        active_episode_ids=market_state.active_episodes,
        active_event_types=market_state.active_events,
        overall_confidence=market_state.overall_confidence,
        direction_confidence=direction_confidence,
        uncertainties=uncertainties,
        missing_evidence=_missing_evidence(understanding_values),
        evidence_ids=evidence_ids,
        source_market_state_timestamp=market_state.timestamp,
    )
