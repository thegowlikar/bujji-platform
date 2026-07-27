"""Decision Auditor & Learning Observatory models — Series 99. Frozen
dataclasses throughout (house convention) -- Deliverable 2's "no
mutable fields" requirement is enforced structurally by `frozen=True`
on every dataclass here, not merely by convention."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.msi_execution_planning.models import ExecutionPlanAssessment
from bujji.msi_margin_bridge.models import MarginEstimate
from bujji.msi_portfolio_construction.models import PortfolioConstructionAssessment
from bujji.msi_position_construction.models import PositionConstructionAssessment
from bujji.msi_trade_thesis.models import TradeThesisAssessment


@dataclass(frozen=True)
class DecisionExplanation:
    assessment_id: str
    why: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class DecisionRecord:
    decision_id: str
    timestamp: str
    date: str
    observation_ids: Tuple[str, ...]
    episode_ids: Tuple[str, ...]
    market_direction: str
    consensus: str
    volatility_state: str
    trade_thesis: TradeThesisAssessment
    strategy_family: Optional[str]
    position_construction: Optional[PositionConstructionAssessment]
    portfolio_decision: Optional[PortfolioConstructionAssessment]
    lifecycle_state: Optional[str]
    margin_assessment: Optional[MarginEstimate]
    execution_plan: Optional[ExecutionPlanAssessment]
    confidence: str
    decision_outcome: str   # taxonomy.ALL_DECISION_OUTCOMES -- TRADE_APPROVED or NO_TRADE.
    explanation: DecisionExplanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class OutcomeExplanation:
    assessment_id: str
    what_actually_happened: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class OutcomeRecord:
    outcome_id: str
    timestamp: str
    date: str
    close_price: Optional[float]
    session_high: Optional[float]
    session_low: Optional[float]
    realised_movement_pct: Optional[float]
    realised_volatility: Optional[float]
    realised_direction: str            # taxonomy.ALL_REALISED_DIRECTIONS
    thesis_survival: str                # "SURVIVED" | "INVALIDATED" | taxonomy.SURVIVAL_UNKNOWN
    execution_feasibility: str          # taxonomy.ALL_FEASIBILITY_STATES
    explanation: OutcomeExplanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class DecisionOutcomePair:
    pair_id: str
    decision: DecisionRecord
    outcome: OutcomeRecord
    provenance: str
    schema_version: str
