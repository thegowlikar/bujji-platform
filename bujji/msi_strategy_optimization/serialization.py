"""bujji.msi_strategy_optimization.serialization — Series 108. Dict round-trip."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from .models import (
    StrategyOptimizationAssessment, StrikeOptimizationAssessment, ExpiryOptimizationAssessment,
    RollAssessment, AdjustmentPlanAssessment, ConversionAssessment,
)

_MODELS = {
    "StrategyOptimizationAssessment": StrategyOptimizationAssessment,
    "StrikeOptimizationAssessment": StrikeOptimizationAssessment,
    "ExpiryOptimizationAssessment": ExpiryOptimizationAssessment,
    "RollAssessment": RollAssessment,
    "AdjustmentPlanAssessment": AdjustmentPlanAssessment,
    "ConversionAssessment": ConversionAssessment,
}


def to_dict(assessment: Any) -> Dict[str, Any]:
    d = asdict(assessment)
    d["_type"] = type(assessment).__name__
    return d


def from_dict(d: Dict[str, Any]) -> Any:
    """Best-effort reconstruction for the flat, JSON-safe fields only --
    nested dataclass fields (Explanation, StrikeCandidate, ExpiryCandidate)
    round-trip as plain dicts via `asdict`/`dict`, matching this
    project's own established serialization convention (see
    msi_trade_construction/serialization.py) rather than a full nested
    rehydration this package does not yet need."""
    cls = _MODELS[d["_type"]]
    fields = {k: v for k, v in d.items() if k != "_type"}
    return cls(**fields)
