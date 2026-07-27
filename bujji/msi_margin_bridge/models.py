"""Margin Bridge & Capital Fidelity models — Series 97. Frozen
dataclasses throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_estimate_exists: Tuple[str, ...]
    why_exact_margin_is_or_isnt_available: Tuple[str, ...]
    assumptions_required: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class MarginEstimate:
    assessment_id: str
    timestamp: str
    methodology: str            # taxonomy.ALL_METHODOLOGIES
    estimated_margin: Optional[float]   # PER LOT -- lot sizing remains Portfolio Construction's job.
    confidence: str              # taxonomy.ALL_CONFIDENCE_LEVELS
    data_source: str             # taxonomy.ALL_DATA_SOURCES
    replay_safe: bool            # True iff computing THIS estimate involved no live/network call.
    explanation: Explanation
    provenance: str
    schema_version: str
