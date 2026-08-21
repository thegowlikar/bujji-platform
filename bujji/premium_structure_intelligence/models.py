"""bujji.premium_structure_intelligence.models — Phase 20.31.

Frozen dataclasses throughout (house convention). Pure data contracts,
no logic -- construction lives in engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class CandidateEvaluation:
    """One candidate structure's own real evaluation -- always computed
    for every candidate, independent of which one ultimately wins, so
    the full comparison is inspectable, not just the winner's reasons."""

    structure: str                  # taxonomy.ALL_STRUCTURE_TYPES (excluding NO_TRADE)
    matched: bool                   # did this structure's own preferred-condition set hold?
    satisfied_conditions: Tuple[str, ...]
    unsatisfied_conditions: Tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_structure: Tuple[str, ...]
    why_not_alternatives: Tuple[str, ...]
    evidence_that_mattered_most: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class StructureSelectionAssessment:
    """The one new decision this phase adds: WHICH of the three
    structures msi_trade_construction can genuinely build today should
    be used, given premium-selling is already known to be suitable.

    Never a numeric score field (matching microstructure_intelligence's
    own Phase 20.27 precedent and this phase's own explicit
    instruction) -- `confidence` (a band, taxonomy.ALL_CONFIDENCE_LEVELS)
    is the only scalar strength signal."""

    assessment_id: str
    timestamp: str
    selected_structure: str         # taxonomy.ALL_STRUCTURE_TYPES
    confidence: str                 # taxonomy.ALL_CONFIDENCE_LEVELS
    data_quality: str               # taxonomy.ALL_DATA_QUALITY_VALUES
    reasons: Tuple[str, ...]
    rejected_structures: Tuple[CandidateEvaluation, ...]
    supporting_metrics: Dict[str, Any] = field(default_factory=dict)
    supporting_assessment_ids: Tuple[str, ...] = ()
    explanation: Optional[Explanation] = None
    provenance: str = "bujji.premium_structure_intelligence.engine.select_structure"
    schema_version: str = "1.0.0"

    def to_dict(self) -> dict:
        return {
            "assessment_id": self.assessment_id, "timestamp": self.timestamp,
            "selected_structure": self.selected_structure, "confidence": self.confidence,
            "data_quality": self.data_quality, "reasons": list(self.reasons),
            "rejected_structures": [
                {"structure": c.structure, "matched": c.matched,
                 "satisfied_conditions": list(c.satisfied_conditions),
                 "unsatisfied_conditions": list(c.unsatisfied_conditions)}
                for c in self.rejected_structures
            ],
            "supporting_metrics": dict(self.supporting_metrics),
            "supporting_assessment_ids": list(self.supporting_assessment_ids),
            "provenance": self.provenance, "schema_version": self.schema_version,
        }
