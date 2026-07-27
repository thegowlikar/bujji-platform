"""Strategy Expression Engine models — Series 93. Frozen dataclasses
throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from bujji.msi_trade_thesis.models import TradeThesisAssessment


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_expression: Tuple[str, ...]
    why_families_compatible: Tuple[str, ...]
    why_families_incompatible: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class StrategyExpressionAssessment:
    assessment_id: str
    timestamp: str
    thesis: TradeThesisAssessment                # Direct reuse of Series 92's real type -- downstream-consumption exception.
    desired_direction: str                       # taxonomy.DIRECTION_*
    desired_volatility_exposure: str              # taxonomy.VOLATILITY_EXPOSURE_*
    desired_risk_profile: str                     # taxonomy.RISK_PROFILE_*
    desired_time_decay: str                       # taxonomy.TIME_DECAY_*
    desired_convexity: str                        # taxonomy.CONVEXITY_*
    required_characteristics: Tuple[str, ...]     # taxonomy.ALL_CHARACTERISTICS subset.
    forbidden_characteristics: Tuple[str, ...]    # taxonomy.ALL_CHARACTERISTICS subset.
    compatible_strategy_families: Tuple[str, ...]  # subset of SSF's real 13 families.
    incompatible_strategy_families: Tuple[str, ...]
    explanation: Explanation
    provenance: str
    schema_version: str
