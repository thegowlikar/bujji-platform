"""bujji.microstructure_intelligence.models — Phase 20.27.

Pure data contracts. No IO, no broker, no execution, no classification
logic anywhere in this module -- classification lives in
`classifier.py`, matching `bujji.intelligence.*`'s established "models
carry no logic" discipline (same convention `bujji.market_microstructure.
models.MinuteObservation` itself follows).

`DataQuality` is reused verbatim from `bujji.intelligence.models` --
never redefined here, matching `bujji.market_microstructure.models`'
own precedent of reusing `bujji.market_timeseries.models`' KIND_*
constants rather than declaring parallel copies.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Tuple

from bujji.intelligence.models import DataQuality  # noqa: F401 -- re-exported for callers

SCHEMA_VERSION = "1.0.0"


class MicrostructureState(str, Enum):
    """Bounded vocabulary for 1-minute market texture. `UNKNOWN` is the
    only state reachable when data is insufficient or invalid -- every
    other state requires `DataQuality.SUFFICIENT` (see classifier.py)."""

    UNKNOWN = "UNKNOWN"
    QUIET = "QUIET"
    NORMAL = "NORMAL"
    EXPANSION = "EXPANSION"
    CONTRACTION = "CONTRACTION"
    ABSORPTION = "ABSORPTION"
    LIQUIDITY_STRESS = "LIQUIDITY_STRESS"
    FALSE_BREAKOUT = "FALSE_BREAKOUT"


@dataclass(frozen=True)
class MicrostructureReading:
    """Microstructure Intelligence output for one instrument, derived
    from a chronological list of already-closed `MinuteObservation`s.

    Immutable, matching every `bujji.intelligence.*` Reading and
    `bujji.market_microstructure.models.MinuteObservation` itself.
    Deliberately no generic "score" field (explicit instruction) --
    `confidence` (0.0-1.0, same convention as `RegimeReading`/
    `StructureReading`) is the only scalar strength signal.

    `window_start`/`window_end` are derived from the input observations
    themselves (earliest window_start / latest window_end considered),
    not from an injected clock -- this classifier is a pure function of
    its `observations` argument, so no `IntelligenceContext` dependency
    is introduced (disclosed design choice, see classifier.py docstring)."""

    instrument: Optional[str]
    session_date: Optional[str]
    state: MicrostructureState
    confidence: float
    data_quality: DataQuality
    reasons: Tuple[str, ...]
    supporting_metrics: Dict[str, Any] = field(default_factory=dict)
    observations_used: int = 0
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "instrument": self.instrument,
            "session_date": self.session_date,
            "state": self.state.value,
            "confidence": self.confidence,
            "data_quality": self.data_quality.value,
            "reasons": list(self.reasons),
            "supporting_metrics": dict(self.supporting_metrics),
            "observations_used": self.observations_used,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "schema_version": self.schema_version,
        }
