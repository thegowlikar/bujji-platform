"""decision_intelligence -- Phase 19.6.

The governed reasoning layer above MarketRealitySnapshot,
MarketIntelligenceSnapshot, DecisionContext, and Market Understanding
Memory. Answers "what decisions are rational, what risks exist, what
evidence supports them, what would invalidate them" -- never "buy/sell
what, how much, at what price." See `models.DecisionIntelligenceSnapshot`
and `engine.build_decision_intelligence_snapshot()`.
"""
from .engine import build_decision_intelligence_snapshot
from .evidence import DecisionEvidenceBundle, build_decision_evidence_bundle
from .models import (
    ContradictionObservation,
    DecisionIntelligenceSnapshot,
    DecisionPosture,
    EnvironmentAssessment,
    MemoryContext,
    OpportunityObservation,
    RiskObservation,
    StrategyFamilyAssessment,
    UncertaintyObservation,
)

__all__ = [
    "build_decision_intelligence_snapshot",
    "DecisionEvidenceBundle", "build_decision_evidence_bundle",
    "ContradictionObservation", "DecisionIntelligenceSnapshot", "DecisionPosture",
    "EnvironmentAssessment", "MemoryContext", "OpportunityObservation",
    "RiskObservation", "StrategyFamilyAssessment", "UncertaintyObservation",
]
