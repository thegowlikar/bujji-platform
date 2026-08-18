"""market_phenomena -- Phase 19.7.

Converts market measurements (MarketIntelligenceSnapshot, Phase 19.3)
into market situation understanding -- what kind of event/process is
currently unfolding. Never a strategy, never a trade, never a
prediction. See `models.MarketPhenomenaAssessment` and
`engine.build_market_phenomena_assessment()`.
"""
from .detectors import PHENOMENON_DETECTORS
from .engine import build_market_phenomena_assessment
from .models import (
    ALL_CONFIDENCE_LEVELS,
    ALL_PHENOMENON_TYPES,
    PHENOMENON_EVENT_RISK,
    PHENOMENON_LIQUIDITY_STRESS,
    PHENOMENON_REGIME_TRANSITION,
    PHENOMENON_VOLATILITY_COMPRESSION,
    PHENOMENON_VOLATILITY_EXPANSION,
    MarketPhenomenaAssessment,
    MarketPhenomenonAssessment,
    PhenomenonEvidenceItem,
)

__all__ = [
    "build_market_phenomena_assessment", "PHENOMENON_DETECTORS",
    "ALL_CONFIDENCE_LEVELS", "ALL_PHENOMENON_TYPES",
    "PHENOMENON_EVENT_RISK", "PHENOMENON_LIQUIDITY_STRESS", "PHENOMENON_REGIME_TRANSITION",
    "PHENOMENON_VOLATILITY_COMPRESSION", "PHENOMENON_VOLATILITY_EXPANSION",
    "MarketPhenomenaAssessment", "MarketPhenomenonAssessment", "PhenomenonEvidenceItem",
]
