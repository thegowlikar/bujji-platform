"""market_intelligence_snapshot -- Phase 19.3.

The composition layer over the already-validated Intelligence Core
(Phase 19.0-19.2.3). See `models.MarketIntelligenceSnapshot` and
`builder.build_market_intelligence_snapshot()`.
"""
from .builder import build_market_intelligence_snapshot
from .models import (
    ContradictionScore,
    IntelligenceEvidenceBundle,
    MarketIntelligenceSnapshot,
    MarketPosture,
    MarketThesis,
)

__all__ = [
    "build_market_intelligence_snapshot",
    "ContradictionScore",
    "IntelligenceEvidenceBundle",
    "MarketIntelligenceSnapshot",
    "MarketPosture",
    "MarketThesis",
]
