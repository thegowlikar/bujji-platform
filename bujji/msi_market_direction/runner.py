"""Public composition entrypoints for Market Direction Intelligence.

---------------------------------------------------------------------
Framing decision (mirrors Series 82/83's own resolved precedent):
---------------------------------------------------------------------
Unlike Series 78/79 (which reason over a growing SEQUENCE of episodes,
genuinely needing a batch-vs-incremental distinction), this sprint's
real input shape is a single, already-complete (PriceStructureAssessment,
MarketStructureAssessment) PAIR -> a single MarketDirectionAssessment
result. There is no sequence to walk incrementally. The meaningful
parity property here, exactly as Series 82/83 concluded for their own
analogous shape, is: "the same (psi, mssi) pair fed through the batch
entrypoint and the streaming entrypoint produces byte-identical
results" — not sequence-threading parity. Both entrypoints below
delegate to the identical `engine.determine_market_direction` function.
"""
from __future__ import annotations

from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_price_structure.models import PriceStructureAssessment

from . import config as _config
from . import engine
from .models import MarketDirectionAssessment


def assess_market_direction(
    psi: PriceStructureAssessment,
    mssi: MarketStructureAssessment,
    *,
    timestamp: str,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> MarketDirectionAssessment:
    """Batch/single-shot entrypoint."""
    return engine.determine_market_direction(psi, mssi, timestamp=timestamp, provenance=provenance)


class MarketDirectionStream:
    """Incremental/streaming entrypoint — structurally trivial for this
    package's single-pair-in/single-result-out shape, provided so a
    live producer can call one consistent object-oriented API alongside
    Series 74/75/76's own streaming classes. Delegates to the exact
    same `engine.determine_market_direction` function as
    `assess_market_direction` above — proven byte-identical by
    tests/test_msi_market_direction_intelligence.py::test_batch_vs_streaming_parity.
    """

    def __init__(self, *, provenance: str = _config.DEFAULT_PROVENANCE) -> None:
        self._provenance = provenance

    def process(
        self,
        psi: PriceStructureAssessment,
        mssi: MarketStructureAssessment,
        *,
        timestamp: str,
    ) -> MarketDirectionAssessment:
        return engine.determine_market_direction(psi, mssi, timestamp=timestamp, provenance=self._provenance)
