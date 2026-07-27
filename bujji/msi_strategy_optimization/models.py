"""bujji.msi_strategy_optimization.models — Series 108. Frozen dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why: Tuple[str, ...]
    evidence_used: Tuple[str, ...]
    dominant_constraints: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class StrategyOptimizationAssessment:
    assessment_id: str
    strategy_family: str
    objectives: Tuple[str, ...]
    target_delta: Optional[float]
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class StrikeCandidate:
    strike: float
    option_type: str
    delta: Optional[float]
    implied_volatility: Optional[float]


@dataclass(frozen=True)
class StrikeOptimizationAssessment:
    assessment_id: str
    strategy_family: str
    short_strike: Optional[float]
    long_strike: Optional[float]
    wing_width: Optional[float]
    strike_spacing: Optional[float]
    skew_adjustment: str                     # taxonomy: CALL_SKEW / PUT_SKEW / NEUTRAL / UNKNOWN
    delta_targets: Tuple[Tuple[str, float], ...]   # (leg_label, target_delta) pairs
    achieved_deltas: Tuple[Tuple[str, Optional[float]], ...]
    candidates_considered: Tuple[StrikeCandidate, ...]
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class ExpiryCandidate:
    bucket: str
    expiry: Optional[str]
    dte: Optional[int]
    theta_estimate: Optional[float]
    gamma_estimate: Optional[float]
    expected_move: Optional[float]
    why_considered_or_rejected: str


@dataclass(frozen=True)
class ExpiryOptimizationAssessment:
    assessment_id: str
    dominant_expiry: Optional[str]
    dominant_bucket: Optional[str]
    candidates: Tuple[ExpiryCandidate, ...]
    event_calendar_available: bool
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class RollAssessment:
    assessment_id: str
    roll_strike: bool
    roll_strike_reasoning: Tuple[str, ...]
    roll_expiry: bool
    roll_expiry_reasoning: Tuple[str, ...]
    roll_whole_strategy: bool
    roll_whole_strategy_reasoning: Tuple[str, ...]
    hold: bool
    hold_reasoning: Tuple[str, ...]
    exit: bool
    exit_reasoning: Tuple[str, ...]
    recommended_action: str                   # taxonomy.ALL_ROLL_ACTIONS, priority-resolved
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class AdjustmentPlanAssessment:
    assessment_id: str
    action: str                                # taxonomy.ALL_ADJUSTMENT_ACTIONS
    evidence_cited: Tuple[str, ...]
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str


@dataclass(frozen=True)
class ConversionAssessment:
    assessment_id: str
    recommended: bool
    from_family: str
    to_family: Optional[str]
    reasoning: Tuple[str, ...]
    timestamp: str
    explanation: Explanation
    provenance: str
    schema_version: str
