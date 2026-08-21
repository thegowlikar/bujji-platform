"""Position Lifecycle Runtime -- BUJJI Options OS v3, Gate F.3 Parts 4-6.

PURPOSE: for each open position group, build its current
PositionRiskSnapshot from real Portfolio Reality data, call D.4's own
`recommend_risk_action()` (the SOLE lifecycle intelligence owner --
HOLD/MONITOR/REDUCE_SIZE/ADD_HEDGE/EXIT_CONSIDERATION/BLOCK_NEW_RISK),
and mechanically advance the position's own lifecycle state to match
what already happened. This module invents NO exit condition, NO
stop-loss rule, NO profit target -- every recommendation comes from a
single, unmodified call into `position_lifecycle_intelligence.
recommend_risk_action()`.

STEP 1 OWNERSHIP FINDING (mirrors the F.1 exit-ownership decision):
`msi_dynamic_management/engine.py` (assess_strike_roll/assess_expiry_
roll/assess_delta_rebalance/assess_wing_adjustment/assess_strategy_
conversion/assess_full_exit) remains an ADVISORY intelligence module,
never called from this runtime -- D.4 decides whether any such
recommendation would be acted on, this runtime never queries it
directly. `bujji/trading_brain/exit_engine/engine.py` and `bujji/
trade/manager.py::TradeManager` remain orphaned from every path this
session has built (F.1/F.2/F.3 alike) -- neither is imported here,
verified by this module's own test suite.

POSITION LIFECYCLE STATE IS DELIBERATELY SEPARATE FROM F.0's
RuntimeStateMachine: F.0 governs the whole SESSION's lifecycle (one
instance per runtime); this tracks ONE POSITION GROUP's own progress
(one instance per position). Never imports F.0's RuntimeStateMachine.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Dict, Optional, Tuple

from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
    PositionHealthThresholds, RiskActionRecommendation, build_position_risk_snapshot, recommend_risk_action,
)
from bujji.trading_brain.portfolio_valuation.models import PortfolioValuation

from .position_reality_registry import PositionRealityRegistry

Clock = Callable[[], datetime]


class PositionLifecycleState(str, Enum):
    NEW = "NEW"
    ENTERING = "ENTERING"
    OPEN = "OPEN"
    MANAGING = "MANAGING"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"


# {OPEN, MANAGING, EXIT_PENDING} are fully connected among themselves
# (each can reach each other, plus CLOSED): D.4's own recommendation
# can legitimately swing either direction tick to tick -- e.g.
# EXIT_CONSIDERATION back to HOLD if the market recovers, or straight
# from HOLD to EXIT_CONSIDERATION on a sudden move, with no
# intermediate MANAGING step required as a formality. NEW->ENTERING
# ->OPEN remains the one-way entry sequence; CLOSED remains terminal.
_MANAGED_STATES = (
    PositionLifecycleState.OPEN, PositionLifecycleState.MANAGING, PositionLifecycleState.EXIT_PENDING,
)
_TRANSITIONS = {
    PositionLifecycleState.NEW: {PositionLifecycleState.ENTERING},
    PositionLifecycleState.ENTERING: {PositionLifecycleState.OPEN, PositionLifecycleState.CLOSED},
    **{
        state: {s for s in _MANAGED_STATES if s != state} | {PositionLifecycleState.CLOSED}
        for state in _MANAGED_STATES
    },
    PositionLifecycleState.CLOSED: set(),
}

# D.4's own action vocabulary -> the mechanical lifecycle-state
# consequence. This is bookkeeping only -- the ACTION itself is always
# D.4's, never re-derived here.
_ACTION_TO_STATE = {
    "HOLD": PositionLifecycleState.OPEN,
    "MONITOR": PositionLifecycleState.MANAGING,
    "REDUCE_SIZE": PositionLifecycleState.MANAGING,
    "ADD_HEDGE": PositionLifecycleState.MANAGING,
    "EXIT_CONSIDERATION": PositionLifecycleState.EXIT_PENDING,
    "BLOCK_NEW_RISK": PositionLifecycleState.MANAGING,
}


class IllegalPositionLifecycleTransition(Exception):
    """Raised on an undefined transition -- never silently coerced."""


class PositionLifecycleTracker:
    """One instance per position group."""

    def __init__(self, position_group_id: str, publish_fn=None) -> None:
        self._position_group_id = position_group_id
        self._state = PositionLifecycleState.NEW
        self._publish_fn = publish_fn

    @property
    def state(self) -> PositionLifecycleState:
        return self._state

    def can_transition(self, target: PositionLifecycleState) -> bool:
        return target in _TRANSITIONS[self._state]

    def transition(self, target: PositionLifecycleState, reason: str = "") -> None:
        if target == self._state:
            return
        if not self.can_transition(target):
            raise IllegalPositionLifecycleTransition(
                f"position group {self._position_group_id!r}: illegal transition "
                f"{self._state.value} -> {target.value}"
            )
        previous = self._state
        self._state = target
        if self._publish_fn is not None:
            self._publish_fn(self._position_group_id, previous, target, reason)


@dataclass(frozen=True)
class LifecycleEvaluationResult:
    position_group_id: str
    recommendation: RiskActionRecommendation
    lifecycle_state: PositionLifecycleState


class PositionLifecycleRuntime:
    """Runtime orchestration only -- calls D.4's own recommend_risk_
    action() exactly once per open position group per evaluation
    pass, and mechanically advances that group's own tracker. Never
    computes a risk figure, never decides an action itself."""

    def __init__(self, registry: PositionRealityRegistry, clock: Clock, event_bus=None) -> None:
        self._registry = registry
        self._clock = clock
        self._event_bus = event_bus
        self._trackers: Dict[str, PositionLifecycleTracker] = {}

    def _tracker_for(self, position_group_id: str) -> PositionLifecycleTracker:
        if position_group_id not in self._trackers:
            self._trackers[position_group_id] = PositionLifecycleTracker(
                position_group_id, publish_fn=self._publish_lifecycle_event,
            )
        return self._trackers[position_group_id]

    def mark_entering(self, position_group_id: str) -> None:
        tracker = self._tracker_for(position_group_id)
        tracker.transition(PositionLifecycleState.ENTERING, reason="entry_cycle_started")

    def mark_open(self, position_group_id: str) -> None:
        self._ensure_at_least_open(self._tracker_for(position_group_id))

    def mark_closed(self, position_group_id: str) -> None:
        tracker = self._tracker_for(position_group_id)
        if tracker.state != PositionLifecycleState.CLOSED:
            tracker.transition(PositionLifecycleState.CLOSED, reason="all_legs_closed")

    def lifecycle_state(self, position_group_id: str) -> PositionLifecycleState:
        return self._tracker_for(position_group_id).state

    @staticmethod
    def _ensure_at_least_open(tracker: PositionLifecycleTracker) -> None:
        """Mechanical bookkeeping only: a position group being managed
        or evaluated by definition already exists and has a confirmed
        fill, so its tracker must be at OPEN or later -- this
        cascades NEW->ENTERING->OPEN (or ENTERING->OPEN) automatically
        rather than forcing every caller to hand-drive the two
        boilerplate pre-OPEN transitions before every evaluation.
        A no-op once already at OPEN or any later state."""
        if tracker.state == PositionLifecycleState.NEW:
            tracker.transition(PositionLifecycleState.ENTERING, reason="fill_confirmed")
        if tracker.state == PositionLifecycleState.ENTERING:
            tracker.transition(PositionLifecycleState.OPEN, reason="fill_confirmed")

    async def evaluate_group(
        self, position_group_id: str, valuation: PortfolioValuation, strategy_type: str,
        capital_status: Optional[str], portfolio_status: Optional[str],
        position_health_thresholds: Optional[PositionHealthThresholds], clock: Clock,
    ) -> LifecycleEvaluationResult:
        """Builds one PositionRiskSnapshot from real, already-computed
        Portfolio Reality data (never fabricated) and calls D.4's own
        recommend_risk_action() exactly once."""
        self._ensure_at_least_open(self._tracker_for(position_group_id))
        reality = await self._registry.get_group_reality(position_group_id)

        entry_value = sum(
            leg.entry_price * leg.quantity * (1 if leg.side == "BUY" else -1) for leg in valuation.legs
        ) if valuation.legs else None
        current_value = (
            entry_value + valuation.total_unrealized_pnl
            if entry_value is not None and valuation.total_unrealized_pnl is not None else None
        )
        total_quantity = sum(leg.quantity for leg in valuation.legs)

        position_snapshot = build_position_risk_snapshot(
            position_group_id, strategy_type, entry_value, current_value, total_quantity,
            "OPEN" if reality.is_open else "CLOSED", reality.initial_risk, reality.initial_risk,
            None, clock,
        )
        recommendation = recommend_risk_action(
            position_snapshot, capital_status, portfolio_status, position_health_thresholds, clock,
        )

        tracker = self._tracker_for(position_group_id)
        target_state = _ACTION_TO_STATE[recommendation.action]
        if tracker.can_transition(target_state):
            tracker.transition(target_state, reason=f"D.4:{recommendation.action}")

        return LifecycleEvaluationResult(
            position_group_id=position_group_id, recommendation=recommendation, lifecycle_state=tracker.state,
        )

    def _publish_lifecycle_event(self, position_group_id: str, previous_state, new_state, reason: str) -> None:
        if self._event_bus is None:
            return
        from bujji.core.event_bus import Event, EventType
        self._event_bus.publish_nowait(Event(
            type=EventType.STATE_CHANGED,
            payload={"stage": f"POSITION_LIFECYCLE_{new_state.value}", "position_group_id": position_group_id,
                     "from_state": previous_state.value, "to_state": new_state.value, "reason": reason},
            timestamp=self._clock(),
        ))
