"""Public composition entrypoints for Market Participant Positioning
Intelligence.

Framing decision (mirrors Series 85's own resolved precedent): the
real input shape is a single (current_chain, optional previous_chain)
pair -> a single assessment, not a growing sequence — there is no
sequence to walk incrementally. The meaningful parity property is: the
same (current_chain, previous_chain) pair fed through the batch
entrypoint and the streaming entrypoint produces byte-identical
results. Both entrypoints delegate to the identical
`engine.assess_participant_positioning` function.
"""
from __future__ import annotations

from typing import Optional

from . import config as _config
from . import engine
from .engine import ChainSnapshot
from .models import MarketParticipantPositioningAssessment


def assess_positioning(
    current_chain: ChainSnapshot,
    previous_chain: Optional[ChainSnapshot] = None,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> MarketParticipantPositioningAssessment:
    """Batch/single-shot entrypoint."""
    return engine.assess_participant_positioning(current_chain, previous_chain, timestamp=timestamp, provenance=provenance)


class ParticipantPositioningStream:
    """Incremental/streaming entrypoint — delegates to the exact same
    `engine.assess_participant_positioning` function as
    `assess_positioning` above; proven byte-identical by
    tests/test_msi_participant_positioning_intelligence.py::test_batch_vs_streaming_parity."""

    def __init__(self, *, provenance: str = _config.DEFAULT_PROVENANCE) -> None:
        self._provenance = provenance
        self._last_chain: Optional[ChainSnapshot] = None

    def process(self, current_chain: ChainSnapshot, *, timestamp: str) -> MarketParticipantPositioningAssessment:
        """Convenience streaming mode: automatically carries the
        previously-processed chain forward as `previous_chain`. Callers
        needing explicit control should use `assess_positioning` directly."""
        result = engine.assess_participant_positioning(
            current_chain, self._last_chain, timestamp=timestamp, provenance=self._provenance,
        )
        self._last_chain = current_chain
        return result
