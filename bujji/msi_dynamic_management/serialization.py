"""bujji.msi_dynamic_management.serialization — Series 109. Dict round-trip."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from .models import DecisionAssessment, TransitionAssessment, DynamicManagementBoard

_MODELS = {"DecisionAssessment": DecisionAssessment, "TransitionAssessment": TransitionAssessment}


def to_dict(assessment: Any) -> Dict[str, Any]:
    d = asdict(assessment)
    d["_type"] = type(assessment).__name__
    return d


def from_dict(d: Dict[str, Any]) -> Any:
    """Best-effort reconstruction for flat, JSON-safe fields -- nested
    dataclass fields (Explanation) round-trip as plain dicts, matching
    this project's own established serialization convention (see
    msi_strategy_optimization/serialization.py)."""
    cls = _MODELS[d["_type"]]
    fields = {k: v for k, v in d.items() if k != "_type"}
    return cls(**fields)
