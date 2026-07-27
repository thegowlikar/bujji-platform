"""Public composition entrypoints for the Strategy Selector.

Framing: a single-snapshot in (all upstream assessments for one
cycle), single-assessment out -- same precedent as Series 82/83/85/86/
87/88."""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment
from bujji.msi_strategy_selection_foundation.models import StrategySuitabilityAssessment

from . import config as _config
from . import engine
from .models import StrategySelectionAssessment


def select_strategy(
    suitability_assessments: Tuple[StrategySuitabilityAssessment, ...],
    psi: PriceStructureAssessment,
    mssi: MarketStructureAssessment,
    mdi: MarketDirectionAssessment,
    mppi: MarketParticipantPositioningAssessment,
    consensus: ConsensusAssessment,
    vsb: Optional[VolatilityStructureAssessment] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> StrategySelectionAssessment:
    """Batch/single-shot entrypoint."""
    return engine.select_strategy(
        suitability_assessments, psi, mssi, mdi, mppi, consensus, vsb,
        timestamp=timestamp, provenance=provenance,
    )


class StrategySelectorStream:
    """Incremental/streaming entrypoint -- delegates to the exact same
    `engine.select_strategy` function; proven byte-identical by
    tests/test_msi_strategy_selector.py::test_batch_vs_streaming_parity."""

    def __init__(self, *, provenance: str = _config.DEFAULT_PROVENANCE) -> None:
        self._provenance = provenance

    def process(
        self,
        suitability_assessments: Tuple[StrategySuitabilityAssessment, ...],
        psi: PriceStructureAssessment,
        mssi: MarketStructureAssessment,
        mdi: MarketDirectionAssessment,
        mppi: MarketParticipantPositioningAssessment,
        consensus: ConsensusAssessment,
        vsb: Optional[VolatilityStructureAssessment] = None,
        *, timestamp: str,
    ) -> StrategySelectionAssessment:
        return engine.select_strategy(
            suitability_assessments, psi, mssi, mdi, mppi, consensus, vsb,
            timestamp=timestamp, provenance=self._provenance,
        )
