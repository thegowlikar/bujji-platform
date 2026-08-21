"""Intelligence Completeness Engine — models. Phase 9, Upgrade 2.

Pure, frozen dataclasses. This package is a READ-ONLY analytical
reporting layer over already-persisted `intelligence_cycle.jsonl`
records -- it consumes dicts, produces dicts/dataclasses, and is never
imported by, or fed back into, any decision-making code (ShadowSessionRunner,
IntelligenceCycleRecorder, msi_strategy_selection_foundation,
msi_strategy_selector, msi_trade_intent, or anything further downstream).
Nothing here can influence what Bujji does -- only what Bujji is able to
say about what it knows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Tuple

STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNKNOWN = "UNKNOWN"

ALL_STATUSES = (STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNKNOWN)


@dataclass(frozen=True)
class DomainCompleteness:
    """One domain's understanding-quality read for one cycle.

    `status` is never a judgment about whether a trade should happen --
    it only describes how much real evidence exists for this domain
    right now, exactly as the domain's own engine already reported it.
    """

    domain: str
    status: str
    reason: str
    raw_confidence: Optional[str] = None


@dataclass(frozen=True)
class CycleCompletenessReport:
    """One cycle's full completeness read -- every tracked domain, plus
    the two rollup scores. Both scores are pure functions of the
    `domains` tuple; neither reads anything about whether a strategy
    was selected or a trade was intended."""

    timestamp: str
    domains: Tuple[DomainCompleteness, ...]
    completeness_score: float
    honesty_score: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "domains": {
                d.domain: {"status": d.status, "reason": d.reason, "raw_confidence": d.raw_confidence}
                for d in self.domains
            },
            "completeness_score": self.completeness_score,
            "honesty_score": self.honesty_score,
        }
