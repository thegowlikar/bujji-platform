"""market_environment -- Phase 19.9.

The bridge between market understanding (Phase 19.3-19.8) and a future
strategy-selection layer. Answers "what type of trading environment
exists right now" -- never chooses a strategy, never generates a trade.
See `models.MarketEnvironmentAssessment` and
`engine.build_market_environment_assessment()`.
"""
from .classify import classify_environment
from .engine import build_market_environment_assessment
from .models import (
    ALL_CONFIDENCE_LEVELS,
    EnvironmentType,
    MarketEnvironmentAssessment,
)

__all__ = [
    "classify_environment", "build_market_environment_assessment",
    "ALL_CONFIDENCE_LEVELS", "EnvironmentType", "MarketEnvironmentAssessment",
]
