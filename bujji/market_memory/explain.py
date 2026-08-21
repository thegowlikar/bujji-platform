"""Phase 20.15 -- explainability. "What memory contributed" -- built
entirely from `MemoryContext`'s own already-real fields, never
inventing a justification `retrieval.py` didn't actually compute.
"""
from __future__ import annotations

from .retrieval import MemoryContext


def explain_memory_context(context: MemoryContext) -> str:
    lines = [
        f"Memory context for {context.target_memory_id}:",
        f"  Similar historical conditions found: {context.similar_count}",
    ]
    if context.similar_count == 0:
        lines.append("  No similar historical conditions in memory -- honest absence, not a fabricated match.")
        return "\n".join(lines)

    lines.append(f"  Known outcomes: {context.known_outcome_count}  "
                 f"Not yet observed: {context.not_yet_observed_count}")
    if context.outcome_distribution:
        lines.append(f"  Outcome distribution (of known outcomes): {context.outcome_distribution}")
    lines.append("  Similarity breakdown:")
    for s in context.similarities:
        lines.append(f"    {s.render()}")
    lines.append("  This is historical EVIDENCE only -- it does not modify evidence_score, "
                 "qualification, or allocation.")
    return "\n".join(lines)
