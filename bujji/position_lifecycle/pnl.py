"""Position Lifecycle P&L Engine -- Phase 15K. Pure functions, no
state, no IO, no broker, no execution, no wall-clock read, no
randomness.

FORENSIC FINDING driving the sign convention below: `ShadowTradeLeg.
side` (Phase 14) is always "BUY"|"SELL", and `ratio`/`LegRecord.
quantity` is always a positive integer (never signed) -- confirmed by
direct source inspection of `shadow_trade_construction/engine.py`.
This means BUY/SELL direction is carried EXCLUSIVELY by the `side`
string, never by a signed quantity -- so this module must never
multiply by a signed quantity itself (that would double-sign), and
never infer direction from quantity's sign (there isn't one).

    BUY:  P&L = (exit_price - entry_price) * quantity * multiplier
    SELL: P&L = (entry_price - exit_price) * quantity * multiplier

`multiplier` is the real contract lot size (shares per lot) -- when
unavailable, P&L is honestly UNKNOWN, never computed with an assumed
multiplier of 1 (that would silently fabricate a wrong number, not
merely an imprecise one).
"""
from __future__ import annotations

from typing import Optional, Sequence, Tuple

from .models import PNL_COMPLETE, PNL_PARTIAL, PNL_UNKNOWN


def compute_leg_gross_pnl(
    side: str, quantity: Optional[int], multiplier: Optional[int],
    entry_price: Optional[float], exit_price: Optional[float],
) -> Tuple[Optional[float], str]:
    """Returns (gross_pnl_or_None, pnl_status). Never raises; every
    missing input degrades to `(None, PNL_UNKNOWN)`, never a fabricated
    number (e.g. assuming multiplier=1 or quantity=1)."""
    if quantity is None or multiplier is None or entry_price is None or exit_price is None:
        return None, PNL_UNKNOWN
    if side not in ("BUY", "SELL"):
        return None, PNL_UNKNOWN
    if quantity <= 0 or multiplier <= 0:
        return None, PNL_UNKNOWN  # a real position never has non-positive quantity/multiplier -- malformed input.

    if side == "BUY":
        per_unit = exit_price - entry_price
    else:  # SELL
        per_unit = entry_price - exit_price
    return per_unit * quantity * multiplier, PNL_COMPLETE


def compute_position_gross_pnl(leg_pnls: Sequence[Tuple[Optional[float], str]]) -> Tuple[Optional[float], str]:
    """`leg_pnls`: the (gross_pnl, pnl_status) result of
    `compute_leg_gross_pnl` for every leg, in order. Position-level P&L
    is the sum of ALL leg P&Ls -- but ONLY when every leg individually
    resolved to a real number; a partial sum (some legs known, some
    not) is never silently presented as if it were the whole position's
    P&L (that would understate real exposure). No legs at all -> UNKNOWN."""
    if not leg_pnls:
        return None, PNL_UNKNOWN
    known = [pnl for pnl, status in leg_pnls if status == PNL_COMPLETE and pnl is not None]
    if len(known) == len(leg_pnls):
        return sum(known), PNL_COMPLETE
    if known:
        return None, PNL_PARTIAL  # some legs resolved, not all -- the TOTAL stays honestly unknown, never a partial sum presented as complete.
    return None, PNL_UNKNOWN


def compute_net_pnl(gross_pnl: Optional[float], fees: Optional[float], slippage: Optional[float]) -> Optional[float]:
    """`fees`/`slippage` genuinely UNKNOWN (None) means net P&L is
    ALSO None -- never assumed zero. `gross_pnl` may be known even
    when net is not (Step 4's explicit requirement).

    Correctness precondition (Phase 20.2.1): `gross_pnl` here must be
    computed from THEORETICAL reference prices, never from an
    already-slippage-adjusted fill price -- otherwise `slippage`
    would be double-counted. `reconstruct_reference_price` below
    exists to give callers a theoretical price to use for `gross_pnl`
    even when only fill evidence was recorded."""
    if gross_pnl is None or fees is None or slippage is None:
        return None
    return gross_pnl - fees - slippage


def reconstruct_reference_price(avg_fill_price: float, total_slippage_currency: Optional[float],
                                 total_filled_qty: int, order_side: Optional[str]) -> float:
    """Inverts `SlippageCalculator.apply()`'s own documented contract
    (a BUY fills at or above reference, a SELL fills at or below it --
    see `bujji.broker.simulation.slippage`) to recover the THEORETICAL
    reference price a fill started from, using only already-recorded
    fields -- an exact algebraic identity over real numbers, never a
    fabricated one.

    Phase 20.2.1: added so a caller (e.g. `position_lifecycle.
    paper_bridge`) can pass a THEORETICAL exit price into
    `compute_leg_gross_pnl` instead of the slippage-adjusted fill
    price -- otherwise gross P&L silently bakes in exit-side slippage,
    and subtracting `slippage` again via `compute_net_pnl` double-
    counts it. `order_side` is the side of the ORDER THAT PRODUCED
    THIS FILL (e.g. the exit order's side, which is the opposite of
    the position's entry side). With zero slippage, or an unknown
    side/quantity, this returns `avg_fill_price` unchanged -- so any
    caller with no real slippage configured sees byte-identical
    behavior to before this function existed. Lives here, not in
    `paper_bridge.py`, because it is P&L-shaped arithmetic (a
    subtraction over prices) -- `paper_bridge.py` is architecturally
    forbidden from performing that (see
    `test_bridge_never_computes_pnl_arithmetic`); this module is the
    single place such arithmetic belongs."""
    if not total_slippage_currency or order_side not in ("BUY", "SELL") or total_filled_qty <= 0:
        return avg_fill_price
    avg_slippage_per_unit = total_slippage_currency / total_filled_qty
    return avg_fill_price - avg_slippage_per_unit if order_side == "BUY" else avg_fill_price + avg_slippage_per_unit
