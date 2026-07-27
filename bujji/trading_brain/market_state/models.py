"""Market State Builder models — frozen, immutable assessment record.

Nothing here decides a trade, a strategy, a position size, or a risk
allocation. `MarketStateAssessment` is a pure description of what
today's translated intelligence, taken together, coherently implies
about the market's structure -- with full transparency about
agreement, contradiction, and honest gaps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class MarketStateAssessment:
    assessment_id: str
    market_state: str
    market_phase: str
    market_character: str
    market_conviction: str
    confidence: str
    supporting_evidence: Tuple[str, ...]
    contradicting_evidence: Tuple[str, ...]
    reasoning_trace: str
    interpretation_id: str
    timestamp: str
    version: str
