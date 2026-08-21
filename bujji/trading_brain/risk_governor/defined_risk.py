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

import math
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
    short_qty = abs(leg_quantities["SHORT_LEG"])
    long_qty = abs(leg_quantities["LONG_LEG"])
    if short_qty > long_qty:
        # Audited finding: taking quantity=min(short_qty, long_qty) here
        # silently discarded the excess short quantity from both the price
        # AND the veto decision -- a short leg that has out-run its own
        # hedge (real, since Gate A folds each leg's fill state
        # independently) is naked, unbounded risk, not a smaller version of
        # the same bounded spread. Never priced as if it were.
        raise IllegalDefinedRiskInputError(
            f"short leg quantity ({short_qty}) exceeds long leg quantity ({long_qty}) -- "
            f"{short_qty - long_qty} unit(s) of naked, unhedged short exposure with unbounded "
            f"risk; a defined-risk vertical spread formula can never price this safely"
        )
    quantity = min(short_qty, long_qty)
    lots_equiv = quantity / profile.lot_size
    max_loss = (strike_width * profile.lot_size * lots_equiv) - (net_credit * quantity)
    if max_loss < 0:
        raise IllegalDefinedRiskInputError(
            f"vertical spread computed a negative max_loss ({max_loss!r}) -- "
            "net_credit cannot legitimately exceed strike_width for a valid defined-risk spread"
        )
    return max_loss


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


def _multi_leg_long_premium_paid(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    """Generalizes _long_option_max_loss to N required legs, ALL of
    which must be long (BUY) positions -- e.g. a long straddle/strangle
    (buy CE + buy PE). Textbook-correct for any all-long combination:
    buying options can never lose more than the total premium paid,
    regardless of how many legs or which strikes/expiries. This formula
    must NEVER be used for a profile that includes a short leg -- the
    caller (StrategyRiskProfile construction, reviewed at authoring
    time) is responsible for only ever pairing this formula with an
    all-long required_leg_roles set."""
    total = 0.0
    for role in profile.required_leg_roles:
        price = orders_by_role[role].reference_price
        if price is None:
            raise IllegalDefinedRiskInputError(f"leg role {role!r} requires a reference_price")
        total += price * abs(leg_quantities[role])
    return total


def _iron_condor_max_loss(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    """An iron condor is two independent vertical credit spreads (a
    bear call spread: SHORT_LEG_CE + LONG_LEG_CE, and a bull put
    spread: SHORT_LEG_PE + LONG_LEG_PE) sharing one position. Standard,
    textbook-correct formula: at expiry, at most ONE side can be
    breached (price cannot be simultaneously above the call wing and
    below the put wing), and the unbreached spread always expires
    worthless, keeping its own credit in full. Maximum loss is
    therefore the WIDER of the two wing widths, times quantity, minus
    the TOTAL combined credit from both spreads (never just the
    breached side's own credit) -- if the two wings happen to be equal
    width (the common, symmetric case), this reduces to the familiar
    "width minus total credit" figure; asymmetric wings are handled
    correctly by taking the max, not assuming symmetry."""
    if profile.lot_size <= 0:
        raise IllegalDefinedRiskInputError(f"StrategyRiskProfile.lot_size must be positive, got {profile.lot_size!r}")

    required = ("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE")
    prices = {r: orders_by_role[r].reference_price for r in required}
    if any(p is None for p in prices.values()):
        raise IllegalDefinedRiskInputError("iron condor requires a reference_price on all four legs")

    call_width = abs(contracts_by_role["LONG_LEG_CE"].strike - contracts_by_role["SHORT_LEG_CE"].strike)
    put_width = abs(contracts_by_role["SHORT_LEG_PE"].strike - contracts_by_role["LONG_LEG_PE"].strike)

    call_spread_credit = prices["SHORT_LEG_CE"] - prices["LONG_LEG_CE"]
    put_spread_credit = prices["SHORT_LEG_PE"] - prices["LONG_LEG_PE"]
    total_credit = call_spread_credit + put_spread_credit

    # Audited finding: a global min() across all four legs let one wing's
    # short leg silently out-run its OWN hedge (e.g. call side fully
    # hedged, put side short > put side long) without ever being detected
    # -- the global min from an unrelated leg would mask it. Each wing is
    # its own independent vertical spread and must be checked on its own
    # terms, same rule as _vertical_spread_max_loss.
    call_short_qty = abs(leg_quantities["SHORT_LEG_CE"])
    call_long_qty = abs(leg_quantities["LONG_LEG_CE"])
    put_short_qty = abs(leg_quantities["SHORT_LEG_PE"])
    put_long_qty = abs(leg_quantities["LONG_LEG_PE"])
    if call_short_qty > call_long_qty:
        raise IllegalDefinedRiskInputError(
            f"call spread short leg quantity ({call_short_qty}) exceeds its long leg quantity "
            f"({call_long_qty}) -- {call_short_qty - call_long_qty} unit(s) of naked, unhedged "
            f"short call exposure with unbounded risk"
        )
    if put_short_qty > put_long_qty:
        raise IllegalDefinedRiskInputError(
            f"put spread short leg quantity ({put_short_qty}) exceeds its long leg quantity "
            f"({put_long_qty}) -- {put_short_qty - put_long_qty} unit(s) of naked, unhedged "
            f"short put exposure with unbounded risk"
        )

    quantity = min(abs(leg_quantities[r]) for r in required)
    lots_equiv = quantity / profile.lot_size
    max_width = max(call_width, put_width)
    max_loss = (max_width * profile.lot_size * lots_equiv) - (total_credit * quantity)
    if max_loss < 0:
        raise IllegalDefinedRiskInputError(
            f"iron condor computed a negative max_loss ({max_loss!r}) -- "
            "total_credit cannot legitimately exceed max_width for a valid defined-risk condor"
        )
    return max_loss


def _butterfly_max_loss(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    """A long butterfly (buy 1x lower wing, sell 2x ATM body, buy 1x
    upper wing, all same option type) is a net-debit structure: at
    expiry, at or beyond either wing the structure collapses to zero
    value, so maximum loss is exactly the net premium paid to establish
    it -- cost of both wings minus premium received for the body,
    each at its own actual quantity (never assuming the body's 2x
    ratio holds, in case of malformed/partial-fill data). By strike
    convexity this is always >= 0 for a genuinely valid butterfly; a
    negative result signals malformed pricing data and fails closed,
    same guard as the vertical spread and iron condor formulas."""
    required = ("WING_LOWER", "BODY", "WING_UPPER")
    prices = {r: orders_by_role[r].reference_price for r in required}
    if any(p is None for p in prices.values()):
        raise IllegalDefinedRiskInputError("butterfly requires a reference_price on all three legs")

    wing_cost = (
        prices["WING_LOWER"] * abs(leg_quantities["WING_LOWER"])
        + prices["WING_UPPER"] * abs(leg_quantities["WING_UPPER"])
    )
    body_credit = prices["BODY"] * abs(leg_quantities["BODY"])
    max_loss = wing_cost - body_credit
    if max_loss < 0:
        raise IllegalDefinedRiskInputError(
            f"butterfly computed a negative max_loss ({max_loss!r}) -- "
            "body credit cannot legitimately exceed combined wing cost for a valid long butterfly"
        )
    return max_loss


def _calendar_max_loss(
    contracts_by_role: Dict[str, "NiftyOptionContract"],
    orders_by_role: Dict[str, OrderRequest],
    profile: StrategyRiskProfile,
    leg_quantities: Dict[str, int],
) -> float:
    """MODEL-DEPENDENT FORMULA -- the only one in this module that is
    not pure static strike/premium algebra. A calendar spread (sell 1x
    near-expiry ATM option, buy 1x far-expiry option at the SAME
    strike) spans two different expiries, so its terminal payoff is not
    fully determined by strikes and entry premiums alone the way every
    other formula here is.

    The bound relied on: at the near leg's expiry the near-short
    settles to intrinsic value I; because both legs share the exact
    same strike and option type, the far-long's remaining market value
    V at that same instant must satisfy V >= I (a no-arbitrage
    property -- an option can never be worth less than its own
    intrinsic value, i.e. additional time to expiry cannot subtract
    value). Combining entry cashflow (near_premium received, far_premium
    paid) with this bound gives a worst case no worse than losing the
    net debit paid (near_premium - far_premium, if negative). This is
    the standard, textbook-recognized property calendars are valued
    for -- but it is a MODEL assumption (relies on the option-pricing
    no-arbitrage bound above), not pure combinatorial payoff math like
    the vertical spread / iron condor / butterfly formulas.

    Unlike those formulas, a "negative" raw result here (net_credit >
    0, i.e. near_premium > far_premium) is NOT necessarily malformed
    data -- an inverted term structure (near-term IV richer than
    far-term, e.g. ahead of an event) is a real, legitimate market
    occurrence for calendars specifically, and it only makes the
    worst-case bound MORE conservative (floor at zero loss), never
    less. So max_loss is floored at 0.0 here rather than raising, in
    deliberate contrast to every other formula's negative-result guard."""
    required = ("NEAR_EXPIRY_SHORT", "FAR_EXPIRY_LONG")
    near_contract = contracts_by_role["NEAR_EXPIRY_SHORT"]
    far_contract = contracts_by_role["FAR_EXPIRY_LONG"]
    if near_contract.strike != far_contract.strike:
        raise IllegalDefinedRiskInputError(
            "calendar requires both legs at the identical strike -- "
            f"got near={near_contract.strike!r} far={far_contract.strike!r}; "
            "the V>=I no-arbitrage bound this formula relies on does not hold otherwise"
        )
    if near_contract.option_type != far_contract.option_type:
        raise IllegalDefinedRiskInputError(
            "calendar requires both legs to be the same option_type -- "
            f"got near={near_contract.option_type!r} far={far_contract.option_type!r}"
        )
    if far_contract.expiry <= near_contract.expiry:
        raise IllegalDefinedRiskInputError(
            "calendar requires the FAR_EXPIRY_LONG leg's expiry to be strictly later "
            f"than NEAR_EXPIRY_SHORT's -- got near={near_contract.expiry!r} far={far_contract.expiry!r}; "
            "the V>=I no-arbitrage bound is derived at the near leg's own expiry and requires "
            "the far leg to genuinely have more time remaining at that point, not just be labelled so"
        )
    prices = {r: orders_by_role[r].reference_price for r in required}
    if any(p is None for p in prices.values()):
        raise IllegalDefinedRiskInputError("calendar requires a reference_price on both legs")
    for role, price in prices.items():
        if not math.isfinite(price) or price < 0:
            raise IllegalDefinedRiskInputError(
                f"calendar requires a finite, non-negative reference_price on {role!r} -- got {price!r}; "
                "the max(0.0, net_debit) floor below is only valid for a legitimate inverted term "
                "structure, not for malformed/corrupted price data, which must fail closed here instead"
            )
    quantity = min(abs(leg_quantities[r]) for r in required)
    net_debit = (prices["FAR_EXPIRY_LONG"] - prices["NEAR_EXPIRY_SHORT"]) * quantity
    return max(0.0, net_debit)


_FORMULAS = {
    "VERTICAL_SPREAD_WIDTH_MINUS_CREDIT": _vertical_spread_max_loss,
    "LONG_OPTION_PREMIUM_PAID": _long_option_max_loss,
    "MULTI_LEG_LONG_PREMIUM_PAID": _multi_leg_long_premium_paid,
    "IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT": _iron_condor_max_loss,
    "BUTTERFLY_NET_DEBIT_PAID": _butterfly_max_loss,
    "CALENDAR_NET_DEBIT_PAID": _calendar_max_loss,
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
