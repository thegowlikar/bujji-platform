"""Cycle Evidence — Phase 5, Nervous System Integration.

A single, small, frozen data carrier. Deliberately NOT defined inside
`intelligence_cycle_recorder.py` itself -- that file has its own
structural safety test (`test_no_new_dataclass_defined_recorder_stays_
a_thin_orchestrator`) enforcing it stay a thin orchestrator with no
new dataclasses. This module exists purely so that guard can keep
passing unmodified while still giving `IntelligenceCycleRecorder.
last_evidence` something real to return.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class CycleEvidence:
    """The real objects one `record_cycle()` call already built,
    bundled for a caller that needs the OBJECTS (e.g. `bujji.
    market_thesis.assess()`'s own real signature) rather than
    `record_cycle()`'s JSONL-serialized dict. Every field here is the
    exact same object already placed into that dict via `_to_dict()`
    -- never a second computation, never a re-derivation."""

    timestamp: str
    psi: Any                  # PriceStructureAssessment
    mssi: Any                  # MarketStructureAssessment
    mdi: Optional[Any]         # MarketDirectionAssessment
    mppi: Any                   # MarketParticipantPositioningAssessment
    vsb: Any                     # VolatilityStructureAssessment
    consensus: Any               # ConsensusAssessment
    liquidity: Any                # bujji.intelligence.models.LiquidityReading
    premium_behaviour: Any         # PremiumBehaviourReading
