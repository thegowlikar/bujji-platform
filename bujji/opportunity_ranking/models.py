"""Phase 20.7 -- pure data contracts. No IO, no broker, no execution,
no trade-sizing or order vocabulary anywhere in this module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.opportunity_intelligence import OpportunityAssessment

PRIORITY_HIGH = "HIGH"
PRIORITY_MEDIUM = "MEDIUM"
PRIORITY_LOW = "LOW"
ALL_PRIORITIES = (PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW)


@dataclass(frozen=True)
class OpportunityCandidate:
    """One strategy's current opportunity -- a thin wrapper around
    Phase 20.6's own `OpportunityAssessment`. Adds no new fields;
    exists so this package's public API names its own input type
    rather than exposing Phase 20.6's exact type at every call site."""

    assessment: OpportunityAssessment

    @property
    def strategy_name(self) -> str:
        return self.assessment.strategy_name

    @property
    def qualification_state(self) -> str:
        return self.assessment.decision.state


@dataclass(frozen=True)
class RankedOpportunity:
    """One INCLUDED candidate, with its assigned priority. `priority_
    score` is the numeric ordering key (see `scorer.py`); `priority`
    is a friendly tier label derived from it. `reasons`/`penalties`
    are human-readable, never structural -- ordering is decided by
    `priority_score` alone, not by string content."""

    strategy_name: str
    priority: str
    priority_score: float
    reasons: Tuple[str, ...]
    penalties: Tuple[str, ...]
    candidate: OpportunityCandidate


@dataclass(frozen=True)
class ExcludedOpportunity:
    """One EXCLUDED candidate (BLOCKED or INSUFFICIENT_EVIDENCE) --
    never assigned a priority or an ordering position among other
    excluded candidates; `reason` explains why it was excluded."""

    strategy_name: str
    reason: str
    candidate: OpportunityCandidate


@dataclass(frozen=True)
class RankingResult:
    """`ranked`: sorted, highest `priority_score` first. `excluded`:
    every BLOCKED/INSUFFICIENT_EVIDENCE candidate, in input order --
    never reordered, since excluded candidates have no priority to
    order by."""

    ranked: Tuple[RankedOpportunity, ...]
    excluded: Tuple[ExcludedOpportunity, ...]

    def render(self) -> str:
        lines = ["Ranking:", ""]
        for i, r in enumerate(self.ranked, start=1):
            lines.append(f"{i}. {r.strategy_name}")
            lines.append(f"   Priority: {r.priority}  Score: {r.priority_score:.0f}")
            for reason in r.reasons:
                lines.append(f"   + {reason}")
            for penalty in r.penalties:
                lines.append(f"   - {penalty}")
            lines.append("")
        if self.excluded:
            lines.append("Excluded:")
            for e in self.excluded:
                lines.append(f"  {e.strategy_name} -- {e.reason}")
        return "\n".join(lines)
