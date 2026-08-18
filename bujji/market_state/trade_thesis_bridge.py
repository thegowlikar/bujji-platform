"""Trade Thesis Bridge -- Shadow Trading Brain, Phase 5D.

Thin wrapper around the EXISTING, unmodified
msi_trade_thesis.engine.derive_trade_thesis() -- confirmed by Phase 5C's
investigation to already accept the real psi/mssi/mdi/mppi/vsb/consensus
objects directly, with no adapter required. This bridge exists for the
same reason direction_bridge.py (Phase 3D) does: honest None-safety at
the call site (derive_trade_thesis's psi/mssi/mdi/mppi parameters are
not None-safe internally -- only vsb/consensus are, via `if vsb`/
`if consensus` guards inside the engine), and a single, documented,
testable integration point rather than scattering direct engine calls.

No parallel pipeline is created here. There is currently no live runner
that already holds psi/mssi/mdi/mppi/vsb/ConsensusAssessment together in
one place -- Phase 5B's Domain View Adapter and this bridge are both
standalone, tested functions, not yet wired into ShadowSessionRunner.
This module makes the next honest step available once that wiring
decision is made; it does not make that decision itself.
"""
from __future__ import annotations

from typing import Optional

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_trade_thesis.engine import derive_trade_thesis
from bujji.msi_trade_thesis.models import TradeThesisAssessment
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment


def build_trade_thesis(
    psi: Optional[PriceStructureAssessment],
    mssi: Optional[MarketStructureAssessment],
    mdi: Optional[MarketDirectionAssessment],
    mppi: Optional[MarketParticipantPositioningAssessment],
    vsb: Optional[VolatilityStructureAssessment],
    consensus: Optional[ConsensusAssessment],
    timestamp: str,
) -> Optional[TradeThesisAssessment]:
    """None (never fabricated) when any of psi/mssi/mdi/mppi is
    honestly absent -- derive_trade_thesis() itself requires these
    four as real objects (only vsb/consensus are internally None-safe
    inside the unmodified engine). vsb and consensus may each
    independently be None -- passed through unchanged."""
    if psi is None or mssi is None or mdi is None or mppi is None:
        return None
    return derive_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, timestamp=timestamp)
