"""Phase 20.23 -- pure data contracts. No IO, no broker, no execution,
no intelligence-computation logic anywhere in this module -- this
package consumes the real `bujji.intelligence.*` brains' own already-
computed Reading objects, never recomputes one.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class MarketUnderstandingContext:
    """A composed VIEW over already-real intelligence brain outputs --
    answers only "what is happening in the market," never "what
    should Bujji do." Never a signal, never an opportunity, never a
    confidence value fed back into any strategy score.

    `market_state` is `RegimeReading.regime.value` verbatim when the
    regime brain ran with `DataQuality.SUFFICIENT`, else `"UNKNOWN"`
    -- never inferred from any other brain's output."""

    market_state: str
    supporting_factors: Tuple[str, ...]
    uncertainties: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    explanation: str
