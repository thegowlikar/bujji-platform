"""Decision-tier evidence bundle -- Phase 19.6.

Consolidates references into the evidence already assembled one layer
down (`MarketIntelligenceSnapshot.evidence_bundle`, Phase 19.3) plus any
real historical `MarketMemoryEntry` ids used this cycle -- never a new,
independently-computed evidence value. Confidence is reused, never
re-synthesized: the weakest link discipline Phase 19.3 already
established (`min()` across the six brains) is propagated here, further
reduced only when memory evidence is genuinely sparse (never inflated).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot

MIN_SAMPLES_FOR_STATISTIC = 5  # Below this, no fraction/statistic is reported at all -- see reasoning.py.


@dataclass(frozen=True)
class DecisionEvidenceBundle:
    intelligence_evidence_references: Tuple[str, ...]   # metric_names from the MIS evidence bundle, verbatim.
    intelligence_source_references: Tuple[str, ...]      # reality_snapshot references, verbatim.
    memory_evidence_references: Tuple[str, ...]           # matched MarketMemoryEntry ids, verbatim.
    confidence: float

    def to_dict(self) -> dict:
        return {
            "intelligence_evidence_references": list(self.intelligence_evidence_references),
            "intelligence_source_references": list(self.intelligence_source_references),
            "memory_evidence_references": list(self.memory_evidence_references),
            "confidence": self.confidence,
        }


def build_decision_evidence_bundle(
    snapshot: MarketIntelligenceSnapshot, matched_market_memory_ids: Tuple[str, ...],
) -> DecisionEvidenceBundle:
    intelligence_refs = tuple(item.metric_name for item in snapshot.evidence_bundle.items)
    confidence = snapshot.evidence_bundle.confidence
    if matched_market_memory_ids and len(matched_market_memory_ids) < MIN_SAMPLES_FOR_STATISTIC:
        # Real, sparse memory evidence genuinely reduces how much weight
        # this cycle's conclusions should carry -- never inflated, only
        # ever reduced, and only when memory was actually consulted.
        confidence = min(confidence, 0.5)
    return DecisionEvidenceBundle(
        intelligence_evidence_references=intelligence_refs,
        intelligence_source_references=snapshot.evidence_bundle.source_references,
        memory_evidence_references=matched_market_memory_ids,
        confidence=confidence,
    )
