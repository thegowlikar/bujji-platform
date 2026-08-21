"""Phase 20.15.1 -- explainability. Built entirely from
`MemoryInfluenceAssessment`'s own already-real fields -- the
assessment's own `explanation` string is authoritative; this module
formats around it, never re-derives it.
"""
from __future__ import annotations

from .models import MemoryInfluenceAssessment


def explain_memory_influence(assessment: MemoryInfluenceAssessment) -> str:
    lines = [f"Memory influence for {assessment.strategy_name}:"]
    if assessment.base_confidence == assessment.confidence_modifier:
        lines.append(f"  {assessment.explanation}")
    else:
        lines.append(f"  Confidence {assessment.base_confidence} -> {assessment.confidence_modifier} "
                     f"({assessment.confidence_direction})")
        lines.append(f"  {assessment.explanation}")
    if assessment.historical_outcome_summary:
        lines.append(f"  Historical outcome summary: {assessment.historical_outcome_summary}")
    if assessment.uncertainty_flags:
        lines.append(f"  Uncertainty flags: {list(assessment.uncertainty_flags)}")
    return "\n".join(lines)
