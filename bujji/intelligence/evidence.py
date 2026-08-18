"""IntelligenceEvidence — Phase 19.2.2.

Wraps, never replaces, each brain's own already-real `evidence: dict[str, Any]`
values (Phase 19.2.1's own confirmed finding: these are already real,
machine-readable numbers, e.g. `{"efficiency_ratio": 0.62}` -- never
free-floating prose). The wrap adds exactly the one thing Phase 19.2.1
found missing: a lineage pointer back to the Reality-tier source,
without inventing a new value or discarding the original number.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

from .context import IntelligenceContext


@dataclass(frozen=True)
class IntelligenceEvidence:
    metric_name: str
    value: Any
    source_reference: Optional[str]        # = context.reality_snapshot_reference, when known.
    observation_references: Tuple[str, ...]  # real HistoricalObservation.observation_id values, when known.
    observed_at: datetime                     # = context.as_of_time -- NEVER wall-clock (Phase 19.2.1's own finding).

    def to_dict(self) -> dict:
        return {
            "metric_name": self.metric_name, "value": self.value,
            "source_reference": self.source_reference,
            "observation_references": list(self.observation_references),
            "observed_at": self.observed_at.isoformat(),
        }


def wrap_evidence(
    raw_evidence: Mapping[str, Any], *, context: IntelligenceContext,
    observation_references: Tuple[str, ...] = (),
) -> Dict[str, IntelligenceEvidence]:
    """Pure, mechanical wrap -- every key/value pair in `raw_evidence`
    becomes one `IntelligenceEvidence`, value copied verbatim (NEVER
    recomputed, NEVER fabricated -- this function has no access to
    anything it could use to invent a number). `observation_references`
    is shared across every metric in one call, matching how a single
    brain call already operates over one shared input window (e.g. one
    candle list) -- a per-metric breakdown would require the brain
    itself to track which candle contributed to which number, a real,
    larger change explicitly out of THIS phase's own minimal scope."""
    return {
        name: IntelligenceEvidence(
            metric_name=name, value=value,
            source_reference=context.reality_snapshot_reference,
            observation_references=observation_references,
            observed_at=context.as_of_time,
        )
        for name, value in raw_evidence.items()
    }
