"""Portfolio Risk Aggregation Engine — BUJJI Options OS v3, Numeric Risk
Governor Gate D.2.

PURPOSE: D.1 (capital_safety_governor.py) intentionally depended on a
caller-provided `used_margin`/`open_risk` figure for the WHOLE book --
it never aggregated one itself. This module builds that missing
aggregation layer: "given the complete portfolio state, what is the
true account-level risk?" Its output becomes the trusted input source
for D.1, never the reverse -- this module has no reference to
CapitalSafetySnapshot's ADMISSION logic and never imports
evaluate_trade_capital_safety; only the one-way adapter in Part 5
(portfolio_risk_to_capital_safety_input) constructs a D.1 snapshot
from this module's own output.

WHAT ALREADY EXISTS, VERIFIED BEFORE WRITING ANYTHING (do not
duplicate): Gate A's PositionGroupState/LegState (position_group_fold.
py) carry NO contract data and NO signed direction -- confirmed by
reading the fold logic directly, same finding already established
earlier in this Governor's own history (whole_book_margin_provider.py
's own docstring). SimulatedMarginProvider (Gate C.1) and
MarginExplanation (Gate C.2) already aggregate margin, notional, long/
short exposure, and per-LEG contribution across an arbitrary book --
this module does NOT recompute any of that; it consumes an already-
built MarginSnapshot/MarginExplanation as input.

THE ONE REAL GAP THIS MODULE FILLS: MarginExplanation.contributing_legs
is keyed by `symbol`, not `position_group_id` -- there is no existing
way to map a leg's risk contribution back to which POSITION GROUP it
belongs to, so per-position/per-strategy concentration cannot be
derived from MarginExplanation alone. Rather than inventing a fragile
symbol-based correlation (option symbols are not guaranteed unique
across position groups in every scenario), this module reuses the
EXACT existing precedent portfolio_limits.py already established:
`exposure_by_position_group_id: Dict[str, float]`, a caller-supplied
map from position_group_id to that group's own risk figure. Here it's
named `risk_by_position_group_id`. Strategy-level concentration needs
no such map -- PositionGroupState.strategy_id already exists directly
on the state object.

SCOPE: aggregates over OPEN and PARTIALLY_OPEN lifecycle states only
(matching this phase's own explicit instruction), ignoring CLOSED.
CONSTRUCTED (not-yet-filled, pending) positions are handled SEPARATELY
via aggregate_reserved_risk() -- kept deliberately distinct from the
primary open-book aggregation, since D.1's own CapitalSafetySnapshot
also keeps open_risk and reserved_risk as two separate fields, and
conflating "already open" with "proposed but not yet filled" would
misrepresent what is actually at risk right now versus what is merely
pending.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CONSTRUCTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    PositionGroupState,
    net_quantity,
)
from bujji.trading_brain.risk_governor.simulated_margin_provider import MarginExplanation
from bujji.trading_brain.risk_governor.whole_book_margin_provider import MarginSnapshot

Clock = Callable[[], datetime]

_OPEN_LIFECYCLE_STATES = (LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN)


class IllegalPortfolioRiskAggregationError(Exception):
    """Raised on malformed active positions, missing critical metadata,
    or impossible quantities -- never silently excludes a position from
    aggregation, since that would understate true portfolio risk (the
    same discipline already established for
    project_whole_book_to_margin_legs in Gate C)."""


# --------------------------------------------------------------------- #
# Part 1 -- Portfolio Risk Snapshot
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    """OBSERVED fields are directly countable from position_groups
    alone. DERIVED fields require the caller-supplied margin_snapshot/
    margin_explanation/risk_by_position_group_id -- any of those that
    were not supplied leave the corresponding derived field explicitly
    None, never silently 0.0."""

    # Observed
    number_of_positions: int
    active_position_groups: Tuple[str, ...]
    total_quantity: int
    timestamp: datetime

    # Derived
    total_margin_required: Optional[float]
    total_max_loss: Optional[float]
    total_notional_exposure: Optional[float]
    total_short_exposure: Optional[float]
    total_long_exposure: Optional[float]
    largest_position_concentration: Optional[float]
    strategy_concentration: Optional[Dict[str, float]]


# --------------------------------------------------------------------- #
# Part 2 -- Whole Book Aggregation Engine
# --------------------------------------------------------------------- #

def _active_open_groups(position_groups: List[PositionGroupState]) -> List[PositionGroupState]:
    return [g for g in position_groups if g.lifecycle_state in _OPEN_LIFECYCLE_STATES]


def aggregate_portfolio_risk(
    position_groups: List[PositionGroupState],
    margin_snapshot: Optional[MarginSnapshot],
    margin_explanation: Optional[MarginExplanation],
    risk_by_position_group_id: Optional[Dict[str, float]],
    clock: Clock,
) -> PortfolioRiskSnapshot:
    """Aggregates across all OPEN/PARTIALLY_OPEN position groups;
    CLOSED (and every other lifecycle state, including CONSTRUCTED --
    see module docstring) is ignored entirely by this function.

    Fails closed (raises IllegalPortfolioRiskAggregationError) on:
      - an active group with zero legs (malformed)
      - an active leg with a negative net_quantity (impossible -- would
        mean more was reduced than was ever filled)
      - an active group with strategy_id is None (missing critical
        metadata needed for strategy-level concentration)

    Never silently drops a malformed group from the count -- an
    understated number_of_positions would itself misrepresent
    portfolio risk."""
    active_groups = _active_open_groups(position_groups)

    total_quantity = 0
    for group in active_groups:
        if not group.legs:
            raise IllegalPortfolioRiskAggregationError(
                f"active position group {group.position_group_id!r} has no legs -- malformed"
            )
        if group.strategy_id is None:
            raise IllegalPortfolioRiskAggregationError(
                f"active position group {group.position_group_id!r} is missing strategy_id"
            )
        for coid, leg in group.legs.items():
            qty = net_quantity(leg)
            if qty < 0:
                raise IllegalPortfolioRiskAggregationError(
                    f"leg {coid!r} in group {group.position_group_id!r} has impossible negative "
                    f"net_quantity ({qty!r}) -- reduced_quantity exceeds cumulative_filled_quantity"
                )
            total_quantity += qty

    number_of_positions = len(active_groups)
    active_position_group_ids = tuple(g.position_group_id for g in active_groups)

    total_margin_required = (
        margin_snapshot.required_margin
        if margin_snapshot is not None and margin_snapshot.margin_verified else None
    )
    total_notional_exposure = (
        margin_explanation.total_long_exposure + margin_explanation.total_short_exposure
        if margin_explanation is not None else None
    )
    total_short_exposure = margin_explanation.total_short_exposure if margin_explanation is not None else None
    total_long_exposure = margin_explanation.total_long_exposure if margin_explanation is not None else None

    total_max_loss = None
    largest_position_concentration = None
    strategy_concentration = None
    if risk_by_position_group_id is not None and active_groups:
        group_risks = {}
        for group in active_groups:
            risk = risk_by_position_group_id.get(group.position_group_id)
            if risk is None:
                raise IllegalPortfolioRiskAggregationError(
                    f"active position group {group.position_group_id!r} is missing an entry in "
                    "risk_by_position_group_id -- cannot silently treat as zero risk"
                )
            if risk < 0:
                raise IllegalPortfolioRiskAggregationError(
                    f"active position group {group.position_group_id!r} has a negative risk figure "
                    f"({risk!r}) in risk_by_position_group_id"
                )
            group_risks[group.position_group_id] = risk

        total_max_loss = sum(group_risks.values())
        if total_max_loss > 0:
            largest_position_concentration = max(group_risks.values()) / total_max_loss

            strategy_totals: Dict[str, float] = {}
            for group in active_groups:
                strategy_totals[group.strategy_id] = (
                    strategy_totals.get(group.strategy_id, 0.0) + group_risks[group.position_group_id]
                )
            strategy_concentration = {s: v / total_max_loss for s, v in strategy_totals.items()}
        else:
            largest_position_concentration = 0.0
            strategy_concentration = {g.strategy_id: 0.0 for g in active_groups}

    return PortfolioRiskSnapshot(
        number_of_positions=number_of_positions, active_position_groups=active_position_group_ids,
        total_quantity=total_quantity, timestamp=clock(),
        total_margin_required=total_margin_required, total_max_loss=total_max_loss,
        total_notional_exposure=total_notional_exposure, total_short_exposure=total_short_exposure,
        total_long_exposure=total_long_exposure, largest_position_concentration=largest_position_concentration,
        strategy_concentration=strategy_concentration,
    )


def aggregate_reserved_risk(
    position_groups: List[PositionGroupState], risk_by_position_group_id: Optional[Dict[str, float]],
) -> Optional[float]:
    """Separate, deliberately parallel computation for CONSTRUCTED
    (not-yet-filled) groups -- see module docstring for why this is
    kept distinct from aggregate_portfolio_risk's open-book scope."""
    constructed_groups = [g for g in position_groups if g.lifecycle_state == LIFECYCLE_CONSTRUCTED]
    if not constructed_groups:
        return 0.0
    if risk_by_position_group_id is None:
        return None
    total = 0.0
    for group in constructed_groups:
        risk = risk_by_position_group_id.get(group.position_group_id)
        if risk is None or risk < 0:
            return None
        total += risk
    return total


# --------------------------------------------------------------------- #
# Part 3 -- Exposure Intelligence
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class DirectionExposure:
    long_exposure: Optional[float]
    short_exposure: Optional[float]
    net_exposure: Optional[float]      # long - short; positive = net long, negative = net short


def compute_direction_exposure(snapshot: PortfolioRiskSnapshot) -> DirectionExposure:
    net = (
        snapshot.total_long_exposure - snapshot.total_short_exposure
        if snapshot.total_long_exposure is not None and snapshot.total_short_exposure is not None
        else None
    )
    return DirectionExposure(
        long_exposure=snapshot.total_long_exposure, short_exposure=snapshot.total_short_exposure,
        net_exposure=net,
    )


def compute_hedge_effectiveness(before: PortfolioRiskSnapshot, after: PortfolioRiskSnapshot) -> Optional[float]:
    """Pure measurement, not prediction: risk_reduction_percent =
    (before.total_max_loss - after.total_max_loss) / before.total_max_loss.
    Positive means risk was reduced; negative means it increased.
    Returns None if either side's total_max_loss is unavailable or the
    before figure is exactly zero (nothing to measure a reduction
    against)."""
    if before.total_max_loss is None or after.total_max_loss is None:
        return None
    if before.total_max_loss == 0:
        return None
    return (before.total_max_loss - after.total_max_loss) / before.total_max_loss


# --------------------------------------------------------------------- #
# Part 4 -- Portfolio Risk Classification
# --------------------------------------------------------------------- #

RISK_HEALTHY = "HEALTHY"
RISK_DIVERSIFIED = "DIVERSIFIED"
RISK_CONCENTRATED = "CONCENTRATED"
RISK_HIGH_RISK = "HIGH_RISK"
RISK_INVALID = "INVALID"


@dataclass(frozen=True)
class PortfolioRiskThresholds:
    """All illustrative defaults, explicitly configurable -- matching
    this whole Governor's established discipline. DIVERSIFIED requires
    BOTH low concentration AND a minimum number of distinct active
    strategies -- concentration alone cannot earn the DIVERSIFIED label
    if there is nothing to diversify across (e.g. a single healthy
    position is HEALTHY, not DIVERSIFIED, since one position is not
    "diversified" no matter how small its own concentration figure
    is)."""

    diversified_concentration_ceiling: float = 0.25
    diversified_min_strategies: int = 3
    concentrated_threshold: float = 0.35
    high_risk_threshold: float = 0.60


def classify_portfolio_risk(
    snapshot: PortfolioRiskSnapshot, thresholds: Optional[PortfolioRiskThresholds] = None,
) -> Tuple[str, Tuple[str, ...]]:
    """Returns (status, reasons), same shape/discipline as D.1's
    classify_capital_safety. Priority order, highest severity first:
      1. INVALID -- missing critical data (concentration figures
         unavailable). Checked first, unconditionally.
      2. HIGH_RISK -- largest_position_concentration or any single
         strategy's concentration >= high_risk_threshold.
      3. CONCENTRATED -- >= concentrated_threshold but below
         high_risk_threshold.
      4. DIVERSIFIED -- concentration <= diversified_concentration_
         ceiling AND at least diversified_min_strategies distinct
         active strategies.
      5. HEALTHY -- below concentrated_threshold but does not meet
         DIVERSIFIED's stricter bar (acceptable, not exemplary)."""
    active_thresholds = thresholds or PortfolioRiskThresholds()

    if snapshot.largest_position_concentration is None or snapshot.strategy_concentration is None:
        return RISK_INVALID, ("INSUFFICIENT_PORTFOLIO_RISK_DATA",)

    max_strategy_concentration = (
        max(snapshot.strategy_concentration.values()) if snapshot.strategy_concentration else 0.0
    )
    worst_concentration = max(snapshot.largest_position_concentration, max_strategy_concentration)

    if worst_concentration >= active_thresholds.high_risk_threshold:
        return RISK_HIGH_RISK, ("EXTREME_EXPOSURE_CONCENTRATION",)

    if worst_concentration >= active_thresholds.concentrated_threshold:
        return RISK_CONCENTRATED, ("SINGLE_STRATEGY_OR_POSITION_DOMINATES_RISK",)

    distinct_strategies = len(snapshot.strategy_concentration)
    if (
        worst_concentration <= active_thresholds.diversified_concentration_ceiling
        and distinct_strategies >= active_thresholds.diversified_min_strategies
    ):
        return RISK_DIVERSIFIED, ()

    return RISK_HEALTHY, ()


# --------------------------------------------------------------------- #
# Part 5 -- Connect with Capital Safety Governor (D.1). One-way adapter
# only -- imports D.1's CapitalSafetySnapshot dataclass, never its
# admission logic, and D.1's own module is never modified.
# --------------------------------------------------------------------- #

def portfolio_risk_to_capital_safety_input(
    portfolio_snapshot: PortfolioRiskSnapshot,
    reserved_risk: Optional[float],
    total_capital: Optional[float],
    available_capital: Optional[float],
    daily_pnl: Optional[float],
    daily_loss_limit: Optional[float],
    peak_capital: Optional[float],
    max_allowed_drawdown: Optional[float],
    consecutive_losses: Optional[int],
    clock: Clock,
) -> CapitalSafetySnapshot:
    """Pure composition -- total_capital/available_capital/daily_pnl/
    daily_loss_limit/peak_capital/max_allowed_drawdown/
    consecutive_losses are genuinely account-level facts this module
    has no way to derive from portfolio state, so they remain explicit
    caller-supplied parameters, exactly as they already were on D.1's
    own CapitalSafetySnapshot -- this function only supplies the two
    fields this module CAN derive: used_margin (from the aggregated
    margin_snapshot) and open_risk (from the aggregated max_loss)."""
    return CapitalSafetySnapshot(
        total_capital=total_capital, available_capital=available_capital,
        used_margin=portfolio_snapshot.total_margin_required, open_risk=portfolio_snapshot.total_max_loss,
        reserved_risk=reserved_risk, daily_pnl=daily_pnl, daily_loss_limit=daily_loss_limit,
        peak_capital=peak_capital, max_allowed_drawdown=max_allowed_drawdown,
        consecutive_losses=consecutive_losses, timestamp=clock(),
    )


# --------------------------------------------------------------------- #
# Part 6 -- Portfolio Scenario Engine. Reuses aggregate_portfolio_risk
# itself (called twice, before/after) rather than inventing a separate
# delta-arithmetic engine -- mirrors Gate C.2.5's MarginScenarioEngine
# own before/after design at the conceptual level, without duplicating
# its code (the underlying data model differs).
# --------------------------------------------------------------------- #

@dataclass(frozen=True)
class PortfolioScenarioResult:
    before: PortfolioRiskSnapshot
    after: PortfolioRiskSnapshot
    risk_delta: Optional[float]              # after.total_max_loss - before.total_max_loss
    concentration_delta: Optional[float]      # after.largest_position_concentration - before's
    margin_delta: Optional[float]              # after.total_margin_required - before's


def simulate_portfolio_change(
    before_position_groups: List[PositionGroupState],
    before_margin_snapshot: Optional[MarginSnapshot],
    before_margin_explanation: Optional[MarginExplanation],
    before_risk_by_position_group_id: Optional[Dict[str, float]],
    after_position_groups: List[PositionGroupState],
    after_margin_snapshot: Optional[MarginSnapshot],
    after_margin_explanation: Optional[MarginExplanation],
    after_risk_by_position_group_id: Optional[Dict[str, float]],
    clock: Clock,
) -> PortfolioScenarioResult:
    """Supports adding/removing a position, adding/removing a hedge,
    and increasing/reducing size -- ALL of these are expressed simply
    as a caller-supplied "after" book that differs from the "before"
    book in whatever way the scenario calls for (an added/removed
    position group, a changed risk_by_position_group_id entry, etc.).
    No execution, no broker call, no mutation of either input list."""
    before = aggregate_portfolio_risk(
        before_position_groups, before_margin_snapshot, before_margin_explanation,
        before_risk_by_position_group_id, clock=clock,
    )
    after = aggregate_portfolio_risk(
        after_position_groups, after_margin_snapshot, after_margin_explanation,
        after_risk_by_position_group_id, clock=clock,
    )

    risk_delta = (
        after.total_max_loss - before.total_max_loss
        if after.total_max_loss is not None and before.total_max_loss is not None else None
    )
    concentration_delta = (
        after.largest_position_concentration - before.largest_position_concentration
        if after.largest_position_concentration is not None and before.largest_position_concentration is not None
        else None
    )
    margin_delta = (
        after.total_margin_required - before.total_margin_required
        if after.total_margin_required is not None and before.total_margin_required is not None else None
    )

    return PortfolioScenarioResult(
        before=before, after=after, risk_delta=risk_delta,
        concentration_delta=concentration_delta, margin_delta=margin_delta,
    )


# --------------------------------------------------------------------- #
# Part 7 -- Explanation Layer. Built from the SAME snapshot/status
# already computed above -- no separate calculation.
# --------------------------------------------------------------------- #

def _format_pct(value: Optional[float]) -> str:
    return f"{value:.0%}" if value is not None else "unavailable"


def explain_portfolio_risk(
    snapshot: PortfolioRiskSnapshot, status: str, reasons: Tuple[str, ...],
) -> str:
    if status == RISK_INVALID:
        return "Portfolio risk cannot be assessed: " + ", ".join(reasons) + "."

    largest_strategy = None
    if snapshot.strategy_concentration:
        largest_strategy = max(snapshot.strategy_concentration.items(), key=lambda kv: kv[1])

    lines = []
    if status in (RISK_HEALTHY, RISK_DIVERSIFIED):
        lines.append("Portfolio risk is diversified." if status == RISK_DIVERSIFIED else "Portfolio risk is healthy.")
    elif status == RISK_CONCENTRATED:
        lines.append("Portfolio concentrated.")
    elif status == RISK_HIGH_RISK:
        lines.append("Portfolio at high risk: extreme exposure concentration.")

    if largest_strategy is not None:
        lines.append(f"Largest strategy ({largest_strategy[0]}) contributes {_format_pct(largest_strategy[1])} of total risk.")

    # Margin UTILIZATION (a fraction of total account capital) is
    # deliberately not reported here: PortfolioRiskSnapshot has no
    # total_capital field by design (that is an account-level fact,
    # not something derivable from portfolio state) -- reporting an
    # invented substitute ratio (e.g. margin/notional) would not
    # actually be margin utilization and could mislead. The absolute
    # required-margin figure is reported instead, honestly labeled.
    if snapshot.total_margin_required is not None:
        lines.append(f"Total margin required: {snapshot.total_margin_required:.2f}.")

    return " ".join(lines)
