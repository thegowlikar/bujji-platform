"""Dynamic Risk Controller & Position Lifecycle Intelligence — BUJJI
Options OS v3, Numeric Risk Governor Gate D.4.

PURPOSE: D.1 answers "is the account safe right now." D.2 answers
"what is the true whole-book risk right now." D.3 answers "how much
additional risk can this portfolio safely accept." None of them answer
the question this module exists for: "once a position exists, how
should it be continuously evaluated -- hold, reduce, hedge, adjust, or
exit?" This is risk INTELLIGENCE, never execution -- every output here
is a recommendation, and nothing in this file places, modifies, or
cancels an order, or calls a broker.

NOT A DUPLICATE OF THE EXISTING EXIT ENGINE -- VERIFIED BEFORE WRITING
ANYTHING: bujji/trading_brain/exit_engine/engine.py already exists, a
genuinely separate, earlier-generation module. Its evaluate() consumes
a PortfolioValuation + a raw `positions: List[dict]` (NOT Gate A's
PositionGroupState -- a different generation's data shapes entirely,
confirmed by reading it directly) and produces a single BINARY
should_exit decision via four fixed threshold rules (max loss, profit
target, hard time exit, and an explicitly-documented placeholder
"strategy exit" rule that its own docstring states is "Always False.
Not a bug"). It does no health classification, no portfolio-context
escalation, no multi-action recommendation (HOLD/REDUCE/HEDGE/EXIT),
and no risk-change-over-time detection. This module is genuinely
different in kind, not a competing reimplementation -- and never
imports exit_engine, PortfolioValuation, or its `positions: List[dict]`
shape (verified via test). Both modules coexist in this codebase's
established multi-generation, sibling-isolated pattern.

GREEKS/IMPLIED VOLATILITY, DELIBERATELY NOT MODELED: no vega, delta,
gamma, theta, or implied-volatility field exists anywhere in this
codebase's data model (confirmed repeatedly across every prior Gate B/
C/D phase this session). "Volatility stress" and single-position
"hedge deterioration" detection, both named in this phase's own brief,
are therefore NOT implemented as distinct signals here -- doing so
would mean either inventing data this system cannot source, or
fabricating a proxy that silently misrepresents a real Greek exposure
as something it isn't. Hedge deterioration specifically is also an
inherently PORTFOLIO-level (multi-position) concept, not a single-
position one -- it is addressed at the portfolio-interaction layer
(Part 5/recommend_risk_action's own portfolio-context parameters), not
invented here as a fake per-position field.

DEPENDENCY DIRECTION: this module imports SAFETY_BLOCKED/SAFETY_
RESTRICTED (D.1) and RISK_HIGH_RISK/RISK_CONCENTRATED (D.2) as plain
status-string constants for portfolio-context escalation -- it never
imports or calls evaluate_trade_capital_safety/classify_capital_safety/
aggregate_portfolio_risk/classify_portfolio_risk (the actual decision
functions), matching the one-way dependency discipline D.2 and D.3
already established. capital_check.assess_capital() remains completely
untouched and unimported, as it has been by every prior phase."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from bujji.trading_brain.risk_governor.capital_safety_governor import SAFETY_BLOCKED, SAFETY_RESTRICTED
from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import RISK_CONCENTRATED, RISK_HIGH_RISK

Clock = Callable[[], datetime]

HEALTH_HEALTHY = "HEALTHY"
HEALTH_WATCH = "WATCH"
HEALTH_STRESSED = "STRESSED"
HEALTH_CRITICAL = "CRITICAL"
HEALTH_INVALID = "INVALID"

SEVERITY_NONE = "NONE"
SEVERITY_MINOR = "MINOR"
SEVERITY_MATERIAL = "MATERIAL"
SEVERITY_SEVERE = "SEVERE"

ACTION_HOLD = "HOLD"
ACTION_MONITOR = "MONITOR"
ACTION_REDUCE_SIZE = "REDUCE_SIZE"
ACTION_ADD_HEDGE = "ADD_HEDGE"
ACTION_EXIT_CONSIDERATION = "EXIT_CONSIDERATION"
ACTION_BLOCK_NEW_RISK = "BLOCK_NEW_RISK"

EVENT_POSITION_OPENED = "POSITION_OPENED"
EVENT_RISK_INCREASED = "RISK_INCREASED"
EVENT_RISK_REDUCED = "RISK_REDUCED"
EVENT_HEDGE_ADDED = "HEDGE_ADDED"
EVENT_LOSS_ACCELERATED = "LOSS_ACCELERATED"
EVENT_EXIT_RECOMMENDED = "EXIT_RECOMMENDED"


class IllegalPositionRiskInputError(Exception):
    """Raised on negative observed values or an impossible lifecycle
    state -- never silently normalized into a plausible-looking
    snapshot."""


class DuplicateRiskObservationEventError(Exception):
    """Raised when an event_id already exists in the store -- risk
    observation history is immutable and append-only."""


_VALID_LIFECYCLE_STATES = ("CONSTRUCTED", "OPEN", "PARTIALLY_OPEN", "CLOSED")


# --------------------------------------------------------------------- #
# Part 1 -- Position Risk Observation Model
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionRiskSnapshot:
    # Identity
    position_group_id: str
    strategy_type: str
    timestamp: datetime

    # Position
    entry_value: Optional[float]        # net premium paid(+)/received(-) at entry, per the book's own sign convention
    current_value: Optional[float]        # current mark-to-market value, same convention
    quantity: int
    lifecycle_state: str

    # Risk
    initial_risk: Optional[float]          # max_loss AT ENTRY (Gate B's DefinedRiskAssessment.max_loss)
    current_risk: Optional[float]           # max_loss NOW (may differ if quantity changed)
    unrealized_pnl: Optional[float]          # current_value - entry_value
    max_loss_remaining: Optional[float]       # initial_risk + unrealized_pnl -- see build_position_risk_snapshot's own docstring
    margin_consumed: Optional[float]

    # Greeks/implied-vol fields are deliberately absent -- see module docstring.


def build_position_risk_snapshot(
    position_group_id: str,
    strategy_type: str,
    entry_value: Optional[float],
    current_value: Optional[float],
    quantity: int,
    lifecycle_state: str,
    initial_risk: Optional[float],
    current_risk: Optional[float],
    margin_consumed: Optional[float],
    clock: Clock,
) -> PositionRiskSnapshot:
    """max_loss_remaining = initial_risk + unrealized_pnl (no branching
    needed): if unrealized_pnl is negative (a loss so far), this
    correctly subtracts the realized-so-far loss from the entry-
    relative theoretical maximum; if positive (a gain so far), it
    correctly ADDS headroom, since the current mark is further from
    the entry-relative worst-case floor than entry itself was. Can go
    negative if losses have already exceeded the theoretical
    initial_risk -- a real, visible breach signal, never clamped,
    mirroring D.3's own "raw figure can go negative" discipline for
    available_risk_budget."""
    if quantity < 0:
        raise IllegalPositionRiskInputError(f"quantity must be non-negative, got {quantity!r}")
    if lifecycle_state not in _VALID_LIFECYCLE_STATES:
        raise IllegalPositionRiskInputError(f"impossible lifecycle_state {lifecycle_state!r}")
    for name, value in (
        ("initial_risk", initial_risk), ("current_risk", current_risk), ("margin_consumed", margin_consumed),
    ):
        if value is not None and value < 0:
            raise IllegalPositionRiskInputError(f"{name} must be non-negative, got {value!r}")

    unrealized_pnl = (
        current_value - entry_value if current_value is not None and entry_value is not None else None
    )
    max_loss_remaining = (
        initial_risk + unrealized_pnl if initial_risk is not None and unrealized_pnl is not None else None
    )

    return PositionRiskSnapshot(
        position_group_id=position_group_id, strategy_type=strategy_type, timestamp=clock(),
        entry_value=entry_value, current_value=current_value, quantity=quantity, lifecycle_state=lifecycle_state,
        initial_risk=initial_risk, current_risk=current_risk, unrealized_pnl=unrealized_pnl,
        max_loss_remaining=max_loss_remaining, margin_consumed=margin_consumed,
    )


# --------------------------------------------------------------------- #
# Part 2 -- Risk Change Detection Engine
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskChangeReport:
    risk_delta: Optional[float]
    margin_delta: Optional[float]
    pnl_delta: Optional[float]
    risk_increasing: Optional[bool]
    margin_expanding: Optional[bool]
    loss_accelerating: Optional[bool]     # pnl got worse AND is already negative
    severity: str                          # NONE | MINOR | MATERIAL | SEVERE
    explanation: str


@dataclass(frozen=True)
class RiskChangeThresholds:
    """Illustrative defaults, explicitly configurable, expressed as a
    fraction of the PREVIOUS snapshot's initial_risk (the position's
    own natural scale) -- matching this whole Governor's established
    discipline of never hardcoding an absolute rupee threshold."""

    minor_risk_delta_fraction: float = 0.05
    material_risk_delta_fraction: float = 0.15
    severe_risk_delta_fraction: float = 0.30


def evaluate_position_risk_change(
    previous_snapshot: PositionRiskSnapshot, current_snapshot: PositionRiskSnapshot,
    thresholds: Optional[RiskChangeThresholds] = None,
) -> RiskChangeReport:
    active_thresholds = thresholds or RiskChangeThresholds()

    risk_delta = (
        current_snapshot.current_risk - previous_snapshot.current_risk
        if current_snapshot.current_risk is not None and previous_snapshot.current_risk is not None else None
    )
    margin_delta = (
        current_snapshot.margin_consumed - previous_snapshot.margin_consumed
        if current_snapshot.margin_consumed is not None and previous_snapshot.margin_consumed is not None else None
    )
    pnl_delta = (
        current_snapshot.unrealized_pnl - previous_snapshot.unrealized_pnl
        if current_snapshot.unrealized_pnl is not None and previous_snapshot.unrealized_pnl is not None else None
    )

    risk_increasing = risk_delta > 0 if risk_delta is not None else None
    margin_expanding = margin_delta > 0 if margin_delta is not None else None
    loss_accelerating = (
        pnl_delta < 0 and current_snapshot.unrealized_pnl is not None and current_snapshot.unrealized_pnl < 0
        if pnl_delta is not None else None
    )

    scale = previous_snapshot.initial_risk
    severity = SEVERITY_NONE
    if risk_delta is not None and scale is not None and scale > 0:
        risk_delta_fraction = abs(risk_delta) / scale
        if risk_delta > 0:
            if risk_delta_fraction >= active_thresholds.severe_risk_delta_fraction:
                severity = SEVERITY_SEVERE
            elif risk_delta_fraction >= active_thresholds.material_risk_delta_fraction:
                severity = SEVERITY_MATERIAL
            elif risk_delta_fraction >= active_thresholds.minor_risk_delta_fraction:
                severity = SEVERITY_MINOR

    explanation_parts = []
    if risk_delta is not None:
        explanation_parts.append(f"Risk changed by {risk_delta:+.2f}.")
    if margin_delta is not None:
        explanation_parts.append(f"Margin changed by {margin_delta:+.2f}.")
    if pnl_delta is not None:
        explanation_parts.append(f"P&L changed by {pnl_delta:+.2f}.")
    if loss_accelerating:
        explanation_parts.append("Loss is accelerating.")
    explanation_parts.append(f"Severity: {severity}.")

    return RiskChangeReport(
        risk_delta=risk_delta, margin_delta=margin_delta, pnl_delta=pnl_delta,
        risk_increasing=risk_increasing, margin_expanding=margin_expanding, loss_accelerating=loss_accelerating,
        severity=severity, explanation=" ".join(explanation_parts),
    )


# --------------------------------------------------------------------- #
# Part 3 -- Position Health Classification
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionHealthThresholds:
    watch_loss_fraction: float = 0.30
    stressed_loss_fraction: float = 0.60
    critical_loss_fraction: float = 0.85


def classify_position_health(
    snapshot: PositionRiskSnapshot, thresholds: Optional[PositionHealthThresholds] = None,
) -> Tuple[str, Tuple[str, ...]]:
    """Priority order, highest severity first:
      1. INVALID -- missing initial_risk/max_loss_remaining/current_risk,
         or initial_risk is exactly 0 (undefined denominator).
         current_risk is required even though this function's own
         loss_used_fraction arithmetic does not consume it directly --
         a missing current_risk means the position's live risk figure
         (which reflects any quantity change since entry) is unknown,
         and this Governor's established discipline throughout D.1-D.3
         is that missing risk data must never be silently treated as
         safe. Found via adversarial audit before this phase's report
         was written: an earlier draft classified a position HEALTHY
         with current_risk=None purely because that field happened to
         be unused by the loss_used_fraction formula itself.
      2. CRITICAL -- loss_used_fraction >= critical_loss_fraction.
      3. STRESSED -- >= stressed_loss_fraction.
      4. WATCH -- >= watch_loss_fraction.
      5. HEALTHY -- otherwise."""
    active_thresholds = thresholds or PositionHealthThresholds()

    if (
        snapshot.initial_risk is None or snapshot.max_loss_remaining is None or snapshot.current_risk is None
        or snapshot.initial_risk <= 0
    ):
        return HEALTH_INVALID, ("INSUFFICIENT_POSITION_RISK_DATA",)

    loss_used_fraction = 1.0 - (snapshot.max_loss_remaining / snapshot.initial_risk)

    if loss_used_fraction >= active_thresholds.critical_loss_fraction:
        return HEALTH_CRITICAL, ("LOSS_TOLERANCE_NEARLY_EXHAUSTED",)
    if loss_used_fraction >= active_thresholds.stressed_loss_fraction:
        return HEALTH_STRESSED, ("MATERIAL_LOSS_TOLERANCE_CONSUMED",)
    if loss_used_fraction >= active_thresholds.watch_loss_fraction:
        return HEALTH_WATCH, ("MINOR_DETERIORATION",)
    return HEALTH_HEALTHY, ()


# --------------------------------------------------------------------- #
# Part 4/5 -- Risk Action Recommendation Engine, portfolio-aware
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskActionRecommendation:
    action: str
    health_status: str
    reasons: Tuple[str, ...]
    explanation: str
    evaluated_at: datetime


def recommend_risk_action(
    position_snapshot: PositionRiskSnapshot,
    capital_safety_status: Optional[str],       # D.1's own classification result, caller-supplied
    portfolio_risk_status: Optional[str],         # D.2's own classification result, caller-supplied
    thresholds: Optional[PositionHealthThresholds],
    clock: Clock,
) -> RiskActionRecommendation:
    """Never executes anything -- output is a recommendation only.
    Priority order, matching Part 5's own worked examples (a position's
    own loss, otherwise unremarkable, is escalated when the ACCOUNT or
    PORTFOLIO context is already stressed):
      1. capital_safety_status == BLOCKED -> BLOCK_NEW_RISK, unconditionally.
      2. health == INVALID -> BLOCK_NEW_RISK (missing data, fail closed).
      3. health == CRITICAL -> EXIT_CONSIDERATION.
      4. health == STRESSED:
           - portfolio context also stressed (HIGH_RISK/CONCENTRATED
             portfolio, or RESTRICTED account) -> REDUCE_SIZE (the
             position alone might be tolerable, but not on top of an
             already-strained account/portfolio).
           - otherwise -> ADD_HEDGE (the position itself needs
             attention, but the account has room to hedge rather than
             reduce outright).
      5. health == WATCH -> MONITOR.
      6. health == HEALTHY -> HOLD."""
    as_of = clock()
    health_status, health_reasons = classify_position_health(position_snapshot, thresholds)

    if capital_safety_status == SAFETY_BLOCKED:
        return RiskActionRecommendation(
            action=ACTION_BLOCK_NEW_RISK, health_status=health_status,
            reasons=("ACCOUNT_CAPITAL_STATUS_BLOCKED",),
            explanation="Account capital status is BLOCKED. No additional risk permitted on this position.",
            evaluated_at=as_of,
        )

    if health_status == HEALTH_INVALID:
        return RiskActionRecommendation(
            action=ACTION_BLOCK_NEW_RISK, health_status=health_status, reasons=health_reasons,
            explanation="Position risk data is insufficient to evaluate. No additional risk permitted.",
            evaluated_at=as_of,
        )

    if health_status == HEALTH_CRITICAL:
        return RiskActionRecommendation(
            action=ACTION_EXIT_CONSIDERATION, health_status=health_status, reasons=health_reasons,
            explanation="Position reached critical risk state. Remaining loss tolerance nearly exhausted.",
            evaluated_at=as_of,
        )

    if health_status == HEALTH_STRESSED:
        portfolio_context_stressed = (
            portfolio_risk_status in (RISK_HIGH_RISK, RISK_CONCENTRATED) or capital_safety_status == SAFETY_RESTRICTED
        )
        if portfolio_context_stressed:
            return RiskActionRecommendation(
                action=ACTION_REDUCE_SIZE, health_status=health_status,
                reasons=health_reasons + ("PORTFOLIO_OR_ACCOUNT_CONTEXT_ALSO_STRESSED",),
                explanation=(
                    "Position risk is materially elevated, and the portfolio/account context is also "
                    "stressed. Size reduction recommended rather than holding as-is."
                ),
                evaluated_at=as_of,
            )
        return RiskActionRecommendation(
            action=ACTION_ADD_HEDGE, health_status=health_status, reasons=health_reasons,
            explanation="Position risk is materially elevated, but the account/portfolio has room. Hedging recommended.",
            evaluated_at=as_of,
        )

    if health_status == HEALTH_WATCH:
        return RiskActionRecommendation(
            action=ACTION_MONITOR, health_status=health_status, reasons=health_reasons,
            explanation="Position shows minor deterioration. No action required; continue monitoring.",
            evaluated_at=as_of,
        )

    return RiskActionRecommendation(
        action=ACTION_HOLD, health_status=health_status, reasons=(),
        explanation="Position risk unchanged/healthy. No safety thresholds breached.",
        evaluated_at=as_of,
    )


# --------------------------------------------------------------------- #
# Part 6 -- Scenario-Based Risk Evolution. Reuses
# build_position_risk_snapshot itself (called twice, before/after)
# rather than a separate delta-arithmetic engine -- mirrors D.2/D.3's
# own established scenario pattern.
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PositionScenarioResult:
    before: PositionRiskSnapshot
    after: PositionRiskSnapshot
    risk_impact: str                # "INCREASED" | "REDUCED" | "UNCHANGED" | "UNAVAILABLE"


def simulate_position_scenario(
    position_group_id: str, strategy_type: str, lifecycle_state: str,
    before_entry_value: Optional[float], before_current_value: Optional[float], before_quantity: int,
    before_initial_risk: Optional[float], before_current_risk: Optional[float], before_margin_consumed: Optional[float],
    after_entry_value: Optional[float], after_current_value: Optional[float], after_quantity: int,
    after_initial_risk: Optional[float], after_current_risk: Optional[float], after_margin_consumed: Optional[float],
    clock: Clock,
) -> PositionScenarioResult:
    """Supports price movement, premium expansion/contraction, hedge
    removal/addition, and quantity change -- ALL expressed simply as a
    different set of "after" values than "before" (a changed
    current_value = price/premium movement; a changed current_risk =
    hedge added/removed or a defined-risk formula recomputed; a
    changed quantity = a size change). No execution, no broker call,
    no market prediction of any kind -- purely a measurement of the
    delta between two caller-supplied observations."""
    before = build_position_risk_snapshot(
        position_group_id, strategy_type, before_entry_value, before_current_value, before_quantity,
        lifecycle_state, before_initial_risk, before_current_risk, before_margin_consumed, clock=clock,
    )
    after = build_position_risk_snapshot(
        position_group_id, strategy_type, after_entry_value, after_current_value, after_quantity,
        lifecycle_state, after_initial_risk, after_current_risk, after_margin_consumed, clock=clock,
    )

    if before.current_risk is None or after.current_risk is None:
        risk_impact = "UNAVAILABLE"
    elif after.current_risk > before.current_risk:
        risk_impact = "INCREASED"
    elif after.current_risk < before.current_risk:
        risk_impact = "REDUCED"
    else:
        risk_impact = "UNCHANGED"

    return PositionScenarioResult(before=before, after=after, risk_impact=risk_impact)


# --------------------------------------------------------------------- #
# Part 7 -- Risk Memory Foundation. Mirrors the established append-only
# journal convention already reused in Gate C.5's MarginCalibrationStore
# (itself mirroring TradeConstructionJournal) -- same shape, same
# no-update/no-delete discipline.
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class RiskObservationEvent:
    event_id: str
    event_type: str          # POSITION_OPENED | RISK_INCREASED | HEDGE_ADDED | LOSS_ACCELERATED | RISK_REDUCED | EXIT_RECOMMENDED
    timestamp: datetime
    position_group_id: str
    previous_state: Optional[str]
    new_state: str
    reason: str


class RiskObservationStore:
    """Append-only, in-memory. No update/delete method exists at all --
    only record_event() and read-only accessors."""

    def __init__(self) -> None:
        self._events_by_id: Dict[str, RiskObservationEvent] = {}

    def record_event(self, event: RiskObservationEvent) -> None:
        if event.event_id in self._events_by_id:
            raise DuplicateRiskObservationEventError(
                f"event_id {event.event_id!r} already recorded -- risk observation history is immutable"
            )
        self._events_by_id[event.event_id] = event

    def events(self) -> Tuple[RiskObservationEvent, ...]:
        return tuple(self._events_by_id.values())

    def events_for_position(self, position_group_id: str) -> Tuple[RiskObservationEvent, ...]:
        return tuple(e for e in self.events() if e.position_group_id == position_group_id)

    def __len__(self) -> int:
        return len(self._events_by_id)
