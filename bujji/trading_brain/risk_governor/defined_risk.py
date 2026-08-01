"""Defined-Risk Projection Engine — BUJJI Options OS v3, Numeric Risk
Governor Gate B.

Computes a conservative, executable-value defined-risk figure for one
position group, using its Gate A-derived projected state (net_quantity,
not gross fills) and the real contracts/orders that constructed it.

Pure functions only -- no I/O, no clock beyond what is passed in, no
mutation of any Gate A object. Never priced from a naked/undefined-risk
leg without a documented, reviewed StrategyRiskProfile formula; never
priced from a market order without a certified adverse-fill bound
(none exists in this codebase yet, so every market order vetoes).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, Optional, Tuple

from bujji.trading_brain.order_construction.models import OrderRequest
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_ACKED,
    LEG_CANCEL_PENDING_UNKNOWN,
    LEG_CANCELLED,
    LEG_NOT_SUBMITTED,
    LIFECYCLE_CLOSED,
    LIFECYCLE_CONSTRUCTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    PositionGroupState,
    net_quantity,
)

Clock = Callable[[], datetime]

MARKET_ORDER_TYPE = "MARKET"

# Post-fill assessment (an already-open/partially-open/closed group):
# legs must be in a state that's actually eligible to carry a real fill.
_FILL_ELIGIBLE_STATUSES = (LEG_ACKED, LEG_CANCEL_PENDING_UNKNOWN, LEG_CANCELLED)
# Pre-trade assessment (a freshly-CONSTRUCTED, nothing-submitted-yet
# group): a leg must genuinely be NOT_SUBMITTED -- anything else means
# this "pre-trade" assessment is being run on a group that has already
# started submitting, which is a caller bug, not a legitimate pre-trade
# state.
_PRE_TRADE_ELIGIBLE_STATUSES = (LEG_NOT_SUBMITTED,)
_ASSESSABLE_LIFECYCLE_STATES = (
    LIFECYCLE_CONSTRUCTED, LIFECYCLE_OPEN, LIFECYCLE_PARTIALLY_OPEN, LIFECYCLE_CLOSED,
)


class IllegalDefinedRiskInputError(Exception):
    """Raised for a caller bug -- e.g. an OrderRequest whose
    client_order_id has no matching leg in the supplied
    PositionGroupState. Never silently ignored."""


@dataclass(frozen=True)
class StrategyRiskProfile:
    """One documented, reviewed, closed-form defined-risk formula.
    `formula` is a human-readable label only -- the actual computation
    lives in `_FORMULAS` below, keyed by `formula`, never inferred or
    fitted at runtime."""

    strategy_id: str
    required_leg_roles: Tuple[str, ...]
    formula: str
    lot_size: int


@dataclass(frozen=True)
class DefinedRiskAssessment:
    position_group_id: str
    decision: str                      # "ALLOW" | "VETO"
    blocking_reason: Optional[str]
    max_loss: Optional[float]           # None whenever decision == "VETO"
    formula_used: Optional[str]
    evaluated_at: datetime


def _vertical_spread_max_loss(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    if profile.lot_size <= 0:
        raise IllegalDefinedRiskInputError(f"StrategyRiskProfile.lot_size must be positive, got {profile.lot_size!r}")
    short_contract = contracts_by_role["SHORT_LEG"]
    long_contract = contracts_by_role["LONG_LEG"]
    short_price = orders_by_role["SHORT_LEG"].reference_price
    long_price = orders_by_role["LONG_LEG"].reference_price
    strike_width = abs(long_contract.strike - short_contract.strike)
    net_credit = (short_price - long_price) if (short_price is not None and long_price is not None) else None
    if net_credit is None:
        raise IllegalDefinedRiskInputError("vertical spread requires a reference_price on both legs")
    quantity = min(abs(leg_quantities["SHORT_LEG"]), abs(leg_quantities["LONG_LEG"]))
    lots_equiv = quantity / profile.lot_size
    return (strike_width * profile.lot_size * lots_equiv) - (net_credit * quantity)


def _long_option_max_loss(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    order = orders_by_role["LONG_LEG"]
    if order.reference_price is None:
        raise IllegalDefinedRiskInputError("long option requires a reference_price")
    return order.reference_price * abs(leg_quantities["LONG_LEG"])


_FORMULAS = {
    "VERTICAL_SPREAD_WIDTH_MINUS_CREDIT": _vertical_spread_max_loss,
    "LONG_OPTION_PREMIUM_PAID": _long_option_max_loss,
}


def _early(position_group_id: str, reason: str, clock: Clock) -> DefinedRiskAssessment:
    return DefinedRiskAssessment(
        position_group_id=position_group_id, decision="VETO", blocking_reason=reason,
        max_loss=None, formula_used=None, evaluated_at=clock(),
    )


def assess_defined_risk(
    state: PositionGroupState,
    contracts_by_client_order_id: Dict[str, "NiftyOptionContract"],
    orders_by_client_order_id: Dict[str, OrderRequest],
    leg_roles: Dict[str, str],           # {client_order_id: role}, caller-supplied -- e.g. from strategy construction
    profile: Optional[StrategyRiskProfile],
    clock: Clock,
) -> DefinedRiskAssessment:
    """Pure. Reads only; never mutates `state`/contracts/orders."""
    pg_id = state.position_group_id

    if state.lifecycle_state not in _ASSESSABLE_LIFECYCLE_STATES:
        return _early(pg_id, f"GROUP_NOT_ASSESSABLE_LIFECYCLE_{state.lifecycle_state}", clock)

    for coid, order in orders_by_client_order_id.items():
        if order.order_type == MARKET_ORDER_TYPE:
            return _early(pg_id, "NO_ADVERSE_FILL_BOUND_FOR_MARKET_ORDER", clock)

    if profile is None:
        return _early(pg_id, "UNDEFINED_RISK_NO_STRESS_MODEL", clock)

    formula_fn = _FORMULAS.get(profile.formula)
    if formula_fn is None:
        return _early(pg_id, "UNDEFINED_RISK_NO_STRESS_MODEL", clock)

    is_pre_trade = state.lifecycle_state == LIFECYCLE_CONSTRUCTED
    eligible_statuses = _PRE_TRADE_ELIGIBLE_STATUSES if is_pre_trade else _FILL_ELIGIBLE_STATUSES

    contracts_by_role: Dict[str, "NiftyOptionContract"] = {}
    orders_by_role: Dict[str, OrderRequest] = {}
    leg_quantities: Dict[str, int] = {}

    for coid, role in leg_roles.items():
        leg = state.legs.get(coid)
        if leg is None:
            raise IllegalDefinedRiskInputError(f"leg_roles references unknown client_order_id {coid!r}")
        if leg.submit_status not in eligible_statuses:
            return _early(pg_id, "INCOMPLETE_DEFINED_RISK_GROUP", clock)
        contract = contracts_by_client_order_id.get(coid)
        order = orders_by_client_order_id.get(coid)
        if contract is None or order is None:
            return _early(pg_id, "INCOMPLETE_DEFINED_RISK_GROUP", clock)
        contracts_by_role[role] = contract
        orders_by_role[role] = order
        # Pre-trade: nothing has filled yet by definition (net_quantity is
        # always 0 in CONSTRUCTED) -- risk is assessed against what was
        # REQUESTED, since that is the only quantity that exists yet.
        # Post-fill: net_quantity is the real, projected, already-adjusted
        # figure and is used as-is, never overridden by the original request.
        qty = net_quantity(leg)
        if is_pre_trade:
            qty = leg.requested_quantity or 0
        leg_quantities[role] = qty

    missing_roles = [r for r in profile.required_leg_roles if r not in contracts_by_role]
    if missing_roles:
        return _early(pg_id, "INCOMPLETE_DEFINED_RISK_GROUP", clock)

    if any(leg_quantities[r] == 0 for r in profile.required_leg_roles):
        return _early(pg_id, "INCOMPLETE_DEFINED_RISK_GROUP", clock)

    try:
        max_loss = formula_fn(contracts_by_role, orders_by_role, profile, leg_quantities)
    except IllegalDefinedRiskInputError:
        return _early(pg_id, "INCOMPLETE_DEFINED_RISK_GROUP", clock)

    return DefinedRiskAssessment(
        position_group_id=pg_id, decision="ALLOW", blocking_reason=None,
        max_loss=max_loss, formula_used=profile.formula, evaluated_at=clock(),
    )
