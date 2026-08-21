"""Phase 20.9 -- pure data contracts. No IO, no broker, no execution,
no quantity/capital/margin/order vocabulary anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.capital_intelligence import AllocationAssessment

COMPATIBLE = "COMPATIBLE"
NEUTRAL = "NEUTRAL"
CONFLICTING = "CONFLICTING"
EXCLUSIVE = "EXCLUSIVE"
ALL_CONFLICT_STATES = (COMPATIBLE, NEUTRAL, CONFLICTING, EXCLUSIVE)

SELECT_PRIMARY = "SELECT_PRIMARY"
ALLOW_MULTIPLE = "ALLOW_MULTIPLE"
REDUCE_CONFLICT = "REDUCE_CONFLICT"
NO_SELECTION = "NO_SELECTION"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ALL_PORTFOLIO_DECISIONS = (SELECT_PRIMARY, ALLOW_MULTIPLE, REDUCE_CONFLICT, NO_SELECTION, INSUFFICIENT_EVIDENCE)


@dataclass(frozen=True)
class CorrelationAssessment:
    """A DECLARATIVE (never statistically computed) assessment of
    whether two strategies' market-behavior ASSUMPTIONS align --
    derived entirely from each strategy's own already-declared
    `market_conditions_required`/`incompatible_conditions` (Phase
    20.3). This is deliberately NOT a price-correlation engine: no
    historical price series is read here, no coefficient is
    calculated -- Step 1's own audit found no correlation engine
    anywhere in this codebase, and this phase's own boundary forbids
    building an optimizer. "Correlation" here means "declared
    assumption overlap," disclosed as such."""

    strategy_a: str
    strategy_b: str
    shared_favorable_regimes: Tuple[str, ...]
    opposing_regimes: Tuple[str, ...]     # regime(s) favorable to one, incompatible with the other.
    basis: str                             # human-readable citation of the declared sets compared.


@dataclass(frozen=True)
class OpportunityConflict:
    """One pairwise conflict finding between two candidates."""

    strategy_a: str
    strategy_b: str
    conflict_state: str
    detail: str
    correlation: CorrelationAssessment

    def __post_init__(self):
        if self.conflict_state not in ALL_CONFLICT_STATES:
            raise ValueError(f"unknown conflict state {self.conflict_state!r}")


@dataclass(frozen=True)
class PortfolioDecision:
    """The portfolio-level verdict across all candidates. `primary`:
    the single dominant strategy name when `decision` is
    `SELECT_PRIMARY`, else `None`. `kept`: every strategy name allowed
    to coexist (populated for `ALLOW_MULTIPLE`/`REDUCE_CONFLICT`, a
    single-element tuple for `SELECT_PRIMARY`, empty otherwise).
    `excluded`: every strategy name NOT kept, each with its own reason
    inside `reasons`."""

    decision: str
    primary: Optional[str]
    kept: Tuple[str, ...]
    excluded: Tuple[str, ...]
    reasons: Tuple[str, ...]
    conflicts: Tuple[OpportunityConflict, ...]
    candidates: Tuple[AllocationAssessment, ...]

    def __post_init__(self):
        if self.decision not in ALL_PORTFOLIO_DECISIONS:
            raise ValueError(f"unknown portfolio decision {self.decision!r}")
