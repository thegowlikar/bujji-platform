"""bujji.msi_position_recomposition.serialization — Series 110. Dict round-trip."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict

from .models import RecompositionAssessment

_MODELS = {"RecompositionAssessment": RecompositionAssessment}


def to_dict(assessment: Any) -> Dict[str, Any]:
    d = asdict(assessment)
    d["_type"] = type(assessment).__name__
    return d


def from_dict(d: Dict[str, Any]) -> Any:
    cls = _MODELS[d["_type"]]
    fields = {k: v for k, v in d.items() if k != "_type"}
    return cls(**fields)
