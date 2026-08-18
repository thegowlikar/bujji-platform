"""decision_context -- Phase 19.4.

The layer between `MarketIntelligenceSnapshot` and future strategy
engines. Answers "what is this environment suitable for," never "what
trade should I place." No order, no fill, no quantity, no position size,
no entry/exit signal anywhere in this package.
"""
from .builder import build_decision_context
from .compatibility_engine import assess_strategy_compatibility
from .models import (
    DecisionContext,
    MarketStateTransition,
    StrategyCompatibilityAssessment,
    TransitionType,
)
from .transition import detect_transition

__all__ = [
    "build_decision_context",
    "assess_strategy_compatibility",
    "detect_transition",
    "DecisionContext",
    "MarketStateTransition",
    "StrategyCompatibilityAssessment",
    "TransitionType",
]
