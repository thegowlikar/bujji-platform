"""Portfolio & Risk Construction models — Series 91. Frozen dataclasses
throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple, Union


@dataclass(frozen=True)
class HeldLeg:
    """One leg's contribution to the portfolio, frozen at ENTRY-TIME
    Greeks (Section on Deliverable 4's limitations: this is a static
    aggregation, never mark-to-market repriced -- full portfolio
    risk/repricing is Trade Management's job, out of scope here)."""
    option_type: str
    strike: float
    expiry: str
    side: str
    ratio: int
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]


@dataclass(frozen=True)
class AdmittedTrade:
    """One previously-APPROVED trade, as carried in `PortfolioState`."""
    assessment_id: str
    strategy_family: str
    underlying: str
    position_close_date: str   # max(leg.expiry for leg in legs) -- the date this trade fully rolls off the book.
    legs: Tuple[HeldLeg, ...]
    approved_lots: int
    capital_required: float
    admitted_date: str


@dataclass(frozen=True)
class PortfolioState:
    """Threaded explicitly by the caller across days (mirrors how the
    Series 88/89/90 corpus scripts thread `prev_chain`/`prev_closes`) --
    this package never holds hidden internal state itself."""
    admitted_trades: Tuple[AdmittedTrade, ...] = ()

    def active(self, as_of_date: str) -> Tuple[AdmittedTrade, ...]:
        """Trades not yet fully rolled off the book as of `as_of_date`.
        Pruning an EXPIRED leg is a definitional fact (it is simply no
        longer part of the live book), not a trade-management decision
        -- no rolling/adjustment logic is involved."""
        return tuple(t for t in self.admitted_trades if t.position_close_date >= as_of_date)


@dataclass(frozen=True)
class ConcentrationReading:
    dimension: str
    key: str
    count_after: int
    limit: int


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_approved: Tuple[str, ...]
    why_rejected: Tuple[str, ...]
    dominant_constraint: Optional[str]
    what_would_change_for_approval: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class PortfolioConstructionAssessment:
    assessment_id: str
    timestamp: str
    proposed_trade_assessment_id: str
    strategy_family: str
    approval_state: str                       # taxonomy.ALL_APPROVAL_STATES
    rejection_reasons: Tuple[str, ...]         # taxonomy.ALL_REJECTION_REASONS / ALL_DEFER_REASONS
    required_margin: Optional[float]           # Always None -- see Series 90's own finding; no
                                                # deterministic/replay-safe SPAN source exists.
    estimated_margin: Optional[float]          # config-sourced ESTIMATED-tier figure x approved_lots.
    portfolio_delta_after: Optional[float]
    portfolio_gamma_after: Optional[float]
    portfolio_theta_after: Optional[float]
    portfolio_vega_after: Optional[float]
    concentration_after: Tuple[ConcentrationReading, ...]
    capital_required: Optional[float]
    capital_available: Optional[float]
    risk_budget_used: Optional[float]          # fraction of REPLAY_ASSUMED_TOTAL_CAPITAL committed, portfolio-wide, after this trade.
    position_size_lots: Union[int, str]        # int lots, or taxonomy.SIZE_UNKNOWN -- never guessed.
    confidence: str                            # taxonomy.ALL_CONFIDENCE_LEVELS -- confidence IN THIS DECISION, not the input selection confidence.
    explanation: Explanation
    provenance: str
    schema_version: str
