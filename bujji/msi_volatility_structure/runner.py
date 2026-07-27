"""Public composition entrypoints for Volatility Structure Bridge.

Framing: a single-snapshot in, single-assessment out (same precedent
as Series 82/83/85/86/87 -- no growing sequence to walk incrementally
for this package's real input shape)."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import config as _config
from . import engine
from .models import VolatilityStructureAssessment


def assess_volatility(
    spot: float, strike: float, t_years: float,
    ce_premium: Optional[float], pe_premium: Optional[float],
    closes_with_ts: Sequence[Tuple[str, float]],
    *, timestamp: str,
    risk_free_rate: float = _config.DEFAULT_RISK_FREE_RATE,
    provenance: str = _config.DEFAULT_PROVENANCE,
) -> VolatilityStructureAssessment:
    """Batch/single-shot entrypoint."""
    return engine.assess_volatility_structure(
        spot, strike, t_years, ce_premium, pe_premium, closes_with_ts,
        timestamp=timestamp, risk_free_rate=risk_free_rate, provenance=provenance,
    )


class VolatilityStructureStream:
    """Incremental/streaming entrypoint -- delegates to the exact same
    `engine.assess_volatility_structure` function; proven byte-identical
    by tests/test_msi_volatility_structure_bridge.py::test_batch_vs_streaming_parity."""

    def __init__(self, *, provenance: str = _config.DEFAULT_PROVENANCE) -> None:
        self._provenance = provenance

    def process(
        self, spot: float, strike: float, t_years: float,
        ce_premium: Optional[float], pe_premium: Optional[float],
        closes_with_ts: Sequence[Tuple[str, float]], *, timestamp: str,
    ) -> VolatilityStructureAssessment:
        return engine.assess_volatility_structure(
            spot, strike, t_years, ce_premium, pe_premium, closes_with_ts,
            timestamp=timestamp, provenance=self._provenance,
        )
