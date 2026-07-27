"""Position Lifecycle Intelligence models — Series 96. Frozen
dataclasses throughout (house convention)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ThesisInvalidation:
    entry_thesis_type: str
    current_thesis_type: str
    compatible: bool
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class AdjustmentPolicy:
    triggers: Tuple[str, ...]              # taxonomy trigger labels this construction type declares.
    actionable_triggers_today: Tuple[str, ...]  # subset actually evaluable with real replay data.
    monitoring_only_triggers: Tuple[str, ...]   # subset disclosed as monitoring-only (no real data source).
    fired: Tuple[str, ...]                 # actionable triggers that actually fired today.
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class ProfitPolicy:
    harvest_rule: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class LossPolicy:
    accept_loss_rule: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class ExpiryPolicy:
    rule: str
    dte_remaining: Optional[int]
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class EmergencyPolicy:
    trigger: str
    reasoning: Tuple[str, ...]


@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_adjustment_policy: Tuple[str, ...]
    why_this_profit_policy: Tuple[str, ...]
    why_this_invalidation_rule: Tuple[str, ...]
    why_this_emergency_policy: Tuple[str, ...]
    schema_version: str


@dataclass(frozen=True)
class PositionLifecycleAssessment:
    lifecycle_id: str
    timestamp: str
    strategy_family: Optional[str]
    construction_type: str
    position_state: str                    # taxonomy.ALL_POSITION_STATES
    expected_lifetime: str                 # taxonomy.ALL_EXPECTED_LIFETIMES
    monitoring_requirements: Tuple[str, ...]
    adjustment_policy: AdjustmentPolicy
    profit_policy: ProfitPolicy
    loss_policy: LossPolicy
    expiry_policy: ExpiryPolicy
    emergency_policy: EmergencyPolicy
    thesis_invalidation: ThesisInvalidation
    supporting_assessment_ids: Tuple[str, ...]
    explanation: Explanation
    provenance: str
    schema_version: str
