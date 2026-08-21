"""Market Direction Bridge -- Shadow Campaign v2 Phase 3D.

Wires real PriceStructureAssessment + MarketStructureAssessment (from
market_state_builder's MarketStateAssessment) into the EXISTING,
unmodified msi_market_direction.engine.determine_market_direction().
No reconciliation logic is reproduced here -- agreement -> a confident
direction, disagreement -> MIXED/CONFIDENCE_LOW (a genuine market state,
never averaged away), insufficient evidence -> UNKNOWN/CONFIDENCE_NONE
are all determine_market_direction()'s own, already-tested behavior.
"""
from __future__ import annotations

from typing import Optional

from bujji.market_state_builder.assessment_bridge import MarketStateAssessment
from bujji.msi_market_direction.engine import determine_market_direction
from bujji.msi_market_direction.models import MarketDirectionAssessment


def build_market_direction(
    market_state_assessment: MarketStateAssessment, timestamp: str,
) -> Optional[MarketDirectionAssessment]:
    """None (never fabricated) when either PriceStructureAssessment or
    MarketStructureAssessment is honestly absent this cycle -- both are
    real, required inputs to determine_market_direction()."""
    psi = market_state_assessment.price_structure
    mssi = market_state_assessment.market_structure
    if psi is None or mssi is None:
        return None
    # MPPI is OPTIONAL here, unlike psi/mssi: an absent positioning assessment
    # contributes an UNKNOWN opinion rather than blocking the direction read.
    # Requiring it would make direction unavailable on any cycle with a thin
    # chain, which is strictly worse than the two-lens answer we had before.
    return determine_market_direction(
        psi, mssi, market_state_assessment.participant_positioning,
        futures_basis=getattr(market_state_assessment, "futures_basis", None),
        previous_futures_basis=getattr(
            market_state_assessment, "previous_futures_basis", None),
        timestamp=timestamp)
