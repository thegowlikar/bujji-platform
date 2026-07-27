"""JSON round-trip for MarketStateAssessment."""
from __future__ import annotations

from typing import Any, Dict

from .models import MarketStateAssessment


def assessment_to_dict(a: MarketStateAssessment) -> Dict[str, Any]:
    return {
        "assessment_id": a.assessment_id,
        "market_state": a.market_state,
        "market_phase": a.market_phase,
        "market_character": a.market_character,
        "market_conviction": a.market_conviction,
        "confidence": a.confidence,
        "supporting_evidence": list(a.supporting_evidence),
        "contradicting_evidence": list(a.contradicting_evidence),
        "reasoning_trace": a.reasoning_trace,
        "interpretation_id": a.interpretation_id,
        "timestamp": a.timestamp,
        "version": a.version,
    }


def assessment_from_dict(d: Dict[str, Any]) -> MarketStateAssessment:
    return MarketStateAssessment(
        assessment_id=d["assessment_id"],
        market_state=d["market_state"],
        market_phase=d["market_phase"],
        market_character=d["market_character"],
        market_conviction=d["market_conviction"],
        confidence=d["confidence"],
        supporting_evidence=tuple(d["supporting_evidence"]),
        contradicting_evidence=tuple(d["contradicting_evidence"]),
        reasoning_trace=d["reasoning_trace"],
        interpretation_id=d["interpretation_id"],
        timestamp=d["timestamp"],
        version=d["version"],
    )
