"""Position Construction Intelligence models — Series 95. Frozen
dataclasses throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.msi_strategy_expression.models import StrategyExpressionAssessment
from bujji.msi_strategy_selector.models import StrategySelectionAssessment
from bujji.msi_trade_thesis.models import TradeThesisAssessment


@dataclass(frozen=True)
class ExpiryPlan:
    rule: str            # taxonomy.EXPIRY_RULE_* or EXPIRY_PLAN_NONE.
    min_dte: Optional[int]
    max_dte: Optional[int]
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class StrikePlan:
    target_delta: Optional[float]   # Reused directly from Series 90's FAMILY_DELTA_TARGETS.
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class WingPlan:
    plan: str             # taxonomy.WING_PLAN_*
    width_source: Optional[str]     # "expected_move" | "configured_fallback" | None.
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_construction_style: Tuple[str, ...]
    why_this_expiry_philosophy: Tuple[str, ...]
    why_this_strike_philosophy: Tuple[str, ...]
    evidence_that_drove_the_design: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class PositionConstructionAssessment:
    assessment_id: str
    timestamp: str
    selected_strategy_family: Optional[str]
    construction_type: str                  # taxonomy.ALL_CONSTRUCTION_TYPES
    expiry_plan: ExpiryPlan
    strike_plan: StrikePlan
    wing_plan: WingPlan
    risk_profile: str                       # taxonomy.RISK_*
    payoff_profile: str                     # taxonomy.PAYOFF_*
    adjustment_readiness: str               # taxonomy.ADJUSTMENT_*
    expected_delta: str                     # taxonomy.SIGN_* -- qualitative sign only, never a numeric magnitude.
    expected_gamma: str
    expected_theta: str
    expected_vega: str
    supporting_assessment_ids: Tuple[str, ...]
    explanation: Explanation
    provenance: str
    schema_version: str
