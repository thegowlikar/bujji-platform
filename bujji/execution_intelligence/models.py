"""Phase 20.18 -- pure data contracts. No broker objects, no orders,
no capital, no quantity/lot/margin vocabulary anywhere in this module.

DISCLOSED NAME COLLISION (same precedent as every prior phase in this
engagement): `ExecutionPlan` also exists in
`bujji.trading_brain.execution_planner.models` -- a real, separate
class in a separate package targeting the MSI/Trading Brain lineage's
own Capital Brain/Strategy Selector inputs. This module's
`ExecutionPlan` is package-qualified (`bujji.execution_intelligence.
models.ExecutionPlan`) and is never imported from, or interchanged
with, that class.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

# -- ExecutionResult.status --------------------------------------------
STATUS_FILLED = "FILLED"
STATUS_PARTIAL = "PARTIAL"
STATUS_REJECTED = "REJECTED"
ALL_EXECUTION_STATUSES = (STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED)

# -- ExecutionResult.simulated_fill_quality ------------------------------
QUALITY_GOOD = "GOOD"
QUALITY_DEGRADED = "DEGRADED"
QUALITY_REJECTED = "REJECTED"
ALL_FILL_QUALITIES = (QUALITY_GOOD, QUALITY_DEGRADED, QUALITY_REJECTED)


@dataclass(frozen=True)
class ExecutionIntent:
    """Cycle 1's own decision, translated into "if Bujji decided to
    act" terms. NOT an order, NOT a position, NOT a capital
    commitment -- an intent to explore what execution WOULD look like.

    `decision_id`/`direction`/`timeframe` are honestly `None` where
    Cycle 1 genuinely does not compute them: Cycle 1 classifies
    opportunities by regime/strategy family, never a directional
    entry side (BUY/SELL) or a holding-period timeframe -- both are
    Strategy/Position Construction's job, out of Cycle 1's scope, and
    never invented here.
    """

    decision_id: str            # = strategy_name (Cycle 1's own identity surrogate, see risk_context_adapter precedent).
    strategy_name: str
    direction: Optional[str]    # honestly None -- Cycle 1 never computes entry side.
    timeframe: Optional[str]    # honestly None -- Cycle 1 never computes a holding period.
    market_regime: Optional[str]
    confidence: Optional[str]
    expected_behavior: str      # a disclosed constant describing what happens next, never a market prediction.
    timestamp: datetime


@dataclass(frozen=True)
class ExecutionPlan:
    """A disclosed, ILLUSTRATIVE simulation configuration -- not a
    calibrated model of real market behavior (same "illustrative
    defaults, not calibrated" disclosure this codebase already uses
    for `CapitalSafetyThresholds()` etc.). `simulation_required` is
    the ONE field carrying real information: whether the real,
    unmodified Risk Context Adapter's assessment permits a simulation
    to run at all.
    """

    intent_id: str
    entry_assumption: str
    execution_style: str
    expected_latency: float                       # milliseconds, illustrative fixed value.
    expected_slippage_range: Tuple[float, float]    # illustrative (min, max) adverse price delta.
    simulation_required: bool


@dataclass(frozen=True)
class ExecutionResult:
    """The output of one paper-only simulated fill attempt. Never a
    real fill, never a real order acknowledgment."""

    intent_id: str
    status: str                      # ALL_EXECUTION_STATUSES
    simulated_fill_quality: str       # ALL_FILL_QUALITIES
    slippage_observed: float
    latency_observed: float
    execution_notes: str

    def __post_init__(self) -> None:
        if self.status not in ALL_EXECUTION_STATUSES:
            raise ValueError(f"status={self.status!r} not in {ALL_EXECUTION_STATUSES}")
        if self.simulated_fill_quality not in ALL_FILL_QUALITIES:
            raise ValueError(f"simulated_fill_quality={self.simulated_fill_quality!r} not in {ALL_FILL_QUALITIES}")


@dataclass(frozen=True)
class ExecutionQualityAssessment:
    """Feedback for Market Memory / future learning -- never fed back
    into `evidence_score`/`confidence` by this package itself (that
    remains Market Memory's own job, Phase 20.15.1's own precedent)."""

    quality_score: Optional[float]     # 0..100, None only when simulation never ran.
    degradation_reason: Optional[str]
    learning_tags: Tuple[str, ...]
