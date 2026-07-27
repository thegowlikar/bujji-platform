"""Public composition entrypoints for Strategy Selection Foundation.

Framing: a single (mdi, mssi, consensus, optional vsb) input snapshot
-> a tuple of per-family assessments (Deliverable 5: independent,
unordered by preference). Same single-snapshot framing precedent as
Series 82/83/85/88. `vsb` is optional and backward-compatible -- every
pre-existing caller passing only (mdi, mssi, consensus) still works
identically.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_market_direction.models import MarketDirectionAssessment
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_volatility_structure.models import VolatilityStructureAssessment

from . import config as _config
from . import engine
from .models import StrategySuitabilityAssessment


def assess_all_strategy_suitability(
    mdi: MarketDirectionAssessment,
    mssi: MarketStructureAssessment,
    consensus: ConsensusAssessment,
    vsb: Optional[VolatilityStructureAssessment] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> Tuple[StrategySuitabilityAssessment, ...]:
    """Batch/single-shot entrypoint."""
    return engine.assess_all_families(mdi, mssi, consensus, vsb, timestamp=timestamp, provenance=provenance)


class StrategySuitabilityStream:
    """Incremental/streaming entrypoint -- delegates to the exact same
    `engine.assess_all_families` function; proven byte-identical by
    tests/test_msi_strategy_selection_foundation.py::test_batch_vs_streaming_parity."""

    def __init__(self, *, provenance: str = _config.DEFAULT_PROVENANCE) -> None:
        self._provenance = provenance

    def process(
        self, mdi: MarketDirectionAssessment, mssi: MarketStructureAssessment, consensus: ConsensusAssessment,
        vsb: Optional[VolatilityStructureAssessment] = None, *, timestamp: str,
    ) -> Tuple[StrategySuitabilityAssessment, ...]:
        return engine.assess_all_families(mdi, mssi, consensus, vsb, timestamp=timestamp, provenance=self._provenance)
