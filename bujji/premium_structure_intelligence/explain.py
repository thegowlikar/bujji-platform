"""bujji.premium_structure_intelligence.explain — Phase 20.31.

Human-readable rendering. No new logic -- reads fields already
computed by engine.py, same convention as
`bujji.mic_context_bridge.explain.explain_market_understanding_context`
and `bujji.microstructure_intelligence.explain.explain_microstructure_reading`.
"""
from __future__ import annotations

from .models import StructureSelectionAssessment


def explain_structure_selection(assessment: StructureSelectionAssessment) -> str:
    lines = [f"STRUCTURE: {assessment.selected_structure}."]
    lines.extend(assessment.reasons)

    rejected = [c for c in assessment.rejected_structures if not c.matched]
    if rejected:
        lines.append("Rejected alternatives:")
        for c in rejected:
            lines.append(f"  - {c.structure}: {'; '.join(c.unsatisfied_conditions)}")

    lines.append(f"Confidence: {assessment.confidence}.")
    lines.append(f"Data quality: {assessment.data_quality}.")
    return " ".join(lines) if len(lines) <= 3 else "\n".join(lines)
