"""Phase 20.8 -- pure data contracts. No IO, no broker, no execution,
no quantity/lot/margin/order vocabulary anywhere in this module.

`RiskAllocationClass` follows this codebase's own established
convention (plain string constants, not `enum.Enum` -- the same
pattern `bujji.mic_v0.models` and `bujji.epistemics.uncertainty` both
already use) rather than introducing a new vocabulary style.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.opportunity_ranking import OpportunityCandidate

MAXIMUM = "MAXIMUM"
NORMAL = "NORMAL"
REDUCED = "REDUCED"
MINIMAL = "MINIMAL"
NONE_ALLOCATION = "NONE"
ALL_ALLOCATION_CLASSES = (MAXIMUM, NORMAL, REDUCED, MINIMAL, NONE_ALLOCATION)

_RANK = {NONE_ALLOCATION: 0, MINIMAL: 1, REDUCED: 2, NORMAL: 3, MAXIMUM: 4}
_BY_RANK = {v: k for k, v in _RANK.items()}


def allocation_rank(allocation_class: str) -> int:
    return _RANK[allocation_class]


def demote_allocation(allocation_class: str, steps: int = 1) -> str:
    """Drop `steps` tiers, floored at NONE -- mirrors `bujji.epistemics.
    uncertainty.demote()`'s own pattern for confidence bands, applied
    here to the (differently-shaped, 5-value) allocation vocabulary;
    that function itself isn't reusable across the two different
    cardinalities, so this is a deliberate, disclosed mirror, not an
    import."""
    return _BY_RANK[max(0, allocation_rank(allocation_class) - steps)]


@dataclass(frozen=True)
class AllocationAssessment:
    """One strategy's risk allocation recommendation -- a CLASS, never
    a quantity. `candidate` is Phase 20.7's own `OpportunityCandidate`,
    carried through unmodified; `priority_score`/`rank` are Phase
    20.7's own already-computed values, read here, never recalculated
    (`None` for excluded candidates, which were never scored)."""

    strategy_name: str
    allocation_class: str
    reasons: Tuple[str, ...]
    penalties: Tuple[str, ...]
    candidate: OpportunityCandidate
    priority_score: Optional[float]
    rank: Optional[int]

    def __post_init__(self):
        if self.allocation_class not in ALL_ALLOCATION_CLASSES:
            raise ValueError(f"unknown allocation class {self.allocation_class!r}")

    def render(self) -> str:
        lines = [
            f"{self.strategy_name}",
            f"  Risk Allocation: {self.allocation_class}"
            + (f"  (priority_score={self.priority_score:.0f}, rank=#{self.rank})" if self.priority_score is not None else ""),
        ]
        for reason in self.reasons:
            lines.append(f"  + {reason}")
        for penalty in self.penalties:
            lines.append(f"  - {penalty}")
        if not self.reasons and not self.penalties:
            lines.append("  (no reasons recorded)")
        return "\n".join(lines)
