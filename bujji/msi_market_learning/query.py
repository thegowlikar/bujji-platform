"""MLE query helpers — Series 100, Phase 1.0. Pure, read-only lookups,
mirroring every prior MSI package's query.py convention. This is the
real "Trading Knowledge Base" read surface -- a human/engineer reads the
knowledge base through these functions, never through direct file access."""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from . import taxonomy
from .models import KnowledgeCandidate


def by_id(candidates: Sequence[KnowledgeCandidate], candidate_id: str) -> Optional[KnowledgeCandidate]:
    for c in candidates:
        if c.candidate_id == candidate_id:
            return c
    return None


def by_lifecycle_stage(candidates: Sequence[KnowledgeCandidate], stage: str) -> Tuple[KnowledgeCandidate, ...]:
    return tuple(c for c in candidates if c.implementation_status == stage)


def by_evidence_tier(candidates: Sequence[KnowledgeCandidate], tier: str) -> Tuple[KnowledgeCandidate, ...]:
    return tuple(c for c in candidates if c.evidence_strength == tier)


def engineering_candidates(candidates: Sequence[KnowledgeCandidate]) -> Tuple[KnowledgeCandidate, ...]:
    """The real, disclosed surface a human reviews: every candidate that
    has reached ENGINEERING_CANDIDATE tier -- i.e. crossed the 200-real-
    occurrence threshold (config.py). This function recommends nothing;
    it only lists what real evidence has accumulated."""
    return by_evidence_tier(candidates, taxonomy.TIER_ENGINEERING_CANDIDATE)


def trace_ancestry(candidate: KnowledgeCandidate) -> Tuple[str, ...]:
    """Real traceability chain per the spec's Traceability section:
    Rule <- Knowledge Object <- Evidence <- Historical Decisions. Returns
    the real supporting_evidence ids (Series 99 decision_id/outcome_id/
    pair_id references) a human can follow back to the exact historical
    decisions that produced this candidate."""
    return candidate.supporting_evidence
