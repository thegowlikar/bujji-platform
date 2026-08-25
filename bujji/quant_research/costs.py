"""Transaction cost modelling. The documented reason retail loses.

WHY THIS FILE EXISTS AT ALL. SEBI's FY26 study found ~88% of individual
traders lost money, options drove 92% of those losses, and roughly Rs 25,000
crore went to transaction costs in a single year. The cited behavioural causes
include overpaying relative to realised volatility and underestimating
friction. Both are measurement failures, not character failures -- which means
they are the two things a research layer can actually fix.

Friction is not a rounding term on this venue. Bujji's own 2026-08-24 corpus
measured a median relative spread of 0.494% and a p95 of 9.524%; published
commentary describes a Rs 4 out-of-the-money option carrying a Rs 1.50 spread
in the final hour, which is 37% of the contract's value. A backtest that
ignores this does not have a small error. It has the wrong sign.

RATES ARE NOT KNOWLEDGE, THEY ARE A DATED FACT. Statutory rates change --
STT on option sales was revised in 2024 -- and a stale rate silently
misprices every trade in a study. So no rate is hardcoded as truth here. A
RateCard carries its own effective date and a `verified` flag, the default
card is marked UNVERIFIED, and any cost computed from an unverified card is
labelled as such so the evaluation contract can refuse it. This mirrors
FYERS_POSITION_SCHEMA_VERIFIED: a value nobody confirmed must not be able to
pass as one somebody did.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"

REFUSE_NO_QUOTE = "NO_TWO_SIDED_QUOTE"
REFUSE_UNVERIFIED_RATES = "UNVERIFIED_RATE_CARD"
REFUSE_NONSENSE_QUOTE = "CROSSED_OR_INVALID_QUOTE"


@dataclass(frozen=True)
class RateCard:
    """Statutory and broker charges, as of a stated date.

    Every field is a fraction of the stated base, not a percentage, so there
    is one convention and no factor-of-100 to get wrong.
    """
    effective_from: str
    verified: bool = False
    source_note: str = ""
    # Options: STT applies to the SELL side, on premium.
    stt_sell_on_premium: float = 0.001
    exchange_txn_on_premium: float = 0.00035
    sebi_turnover_on_premium: float = 0.000001
    stamp_duty_buy_on_premium: float = 0.00003
    gst_on_brokerage_and_exchange: float = 0.18
    brokerage_per_order: float = 20.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "effective_from": self.effective_from,
            "verified": self.verified,
            "source_note": self.source_note,
            "stt_sell_on_premium": self.stt_sell_on_premium,
            "exchange_txn_on_premium": self.exchange_txn_on_premium,
            "sebi_turnover_on_premium": self.sebi_turnover_on_premium,
            "stamp_duty_buy_on_premium": self.stamp_duty_buy_on_premium,
            "gst_on_brokerage_and_exchange": self.gst_on_brokerage_and_exchange,
            "brokerage_per_order": self.brokerage_per_order,
        }


# The shipped default. DELIBERATELY UNVERIFIED. The numbers are the widely
# published ones for Indian index options, but "widely published" is not
# "confirmed against your contract notes", and only your own notes can settle
# what your broker actually charges you.
DEFAULT_RATE_CARD = RateCard(
    effective_from="2026-08-25",
    verified=False,
    source_note=("Commonly published Indian index-option charges. NOT checked "
                 "against this account's contract notes. Verify each line "
                 "against a real note, then construct a card with "
                 "verified=True. Until then every cost here is provisional."),
)


@dataclass
class FillModel:
    """How a fill price is formed from a quote.

    Crossing the spread is the default because it is what actually happens to
    a taker. Assuming a mid fill is the single most common way a backtest
    manufactures profit that does not exist: it awards half the spread on
    entry and half on exit, which on this venue's measured median is ~0.49%
    per round trip and far more in the tail.
    """
    cross_the_spread: bool = True
    extra_slippage_fraction: float = 0.0


def fill_price(bid: Optional[float], ask: Optional[float], side: str,
               model: Optional[FillModel] = None) -> Dict[str, Any]:
    """The price a taker would actually pay or receive, or a refusal.

    Refuses rather than falling back to last-traded price. LTP is a record
    that a trade happened, not an offer to transact with you.
    """
    model = model or FillModel()
    has = (isinstance(bid, (int, float)) and isinstance(ask, (int, float))
           and bid > 0 and ask > 0)
    if not has:
        return {"price": None, "refused": REFUSE_NO_QUOTE,
                "detail": ("no two-sided quote; a fill price cannot be formed. "
                           "Last-traded price is not a substitute -- it is "
                           "neither side of a market you could transact in")}
    if ask < bid:
        return {"price": None, "refused": REFUSE_NONSENSE_QUOTE,
                "detail": f"crossed book: bid {bid} above ask {ask}"}

    mid = (bid + ask) / 2.0
    if model.cross_the_spread:
        px = ask if side == SIDE_BUY else bid
    else:
        px = mid
    if model.extra_slippage_fraction:
        adj = px * model.extra_slippage_fraction
        px = px + adj if side == SIDE_BUY else px - adj
    return {
        "price": px,
        "mid": mid,
        "refused": None,
        "spread_absolute": ask - bid,
        "relative_spread": ((ask - bid) / mid) if mid > 0 else None,
        "half_spread_cost": abs(px - mid),
        "basis": ("taker crossing the spread" if model.cross_the_spread
                  else "MID FILL ASSUMED -- optimistic, not achievable as a taker"),
    }


def round_trip_cost(*, entry_bid, entry_ask, exit_bid, exit_ask,
                    lots: int, lot_size: int, side: str,
                    rates: Optional[RateCard] = None,
                    model: Optional[FillModel] = None) -> Dict[str, Any]:
    """Total cost of one round trip, itemised, or a refusal.

    Returns spread cost and statutory charges separately, because they behave
    differently: statutory charges scale with premium and are knowable in
    advance, while spread cost depends on liquidity at the moment you trade
    and is the part that explodes in the tail.
    """
    rates = rates or DEFAULT_RATE_CARD
    model = model or FillModel()
    qty = lots * lot_size

    open_side = side
    close_side = SIDE_SELL if side == SIDE_BUY else SIDE_BUY
    entry = fill_price(entry_bid, entry_ask, open_side, model)
    exit_ = fill_price(exit_bid, exit_ask, close_side, model)
    if entry["refused"] or exit_["refused"]:
        return {"total": None,
                "refused": entry["refused"] or exit_["refused"],
                "detail": entry.get("detail") or exit_.get("detail"),
                "rates_verified": rates.verified}

    entry_px, exit_px = entry["price"], exit_["price"]
    entry_prem, exit_prem = entry_px * qty, exit_px * qty

    # STT lands on the sell leg only.
    sell_premium = exit_prem if side == SIDE_BUY else entry_prem
    buy_premium = entry_prem if side == SIDE_BUY else exit_prem
    turnover = entry_prem + exit_prem

    stt = sell_premium * rates.stt_sell_on_premium
    exch = turnover * rates.exchange_txn_on_premium
    sebi = turnover * rates.sebi_turnover_on_premium
    stamp = buy_premium * rates.stamp_duty_buy_on_premium
    brokerage = 2 * rates.brokerage_per_order
    gst = (brokerage + exch) * rates.gst_on_brokerage_and_exchange
    statutory = stt + exch + sebi + stamp + brokerage + gst

    spread_cost = (entry["half_spread_cost"] + exit_["half_spread_cost"]) * qty
    total = statutory + spread_cost

    # What the position must move, per unit, merely to break even.
    breakeven_move = total / qty if qty else None
    return {
        "total": total,
        "refused": None,
        "quantity": qty,
        "entry_price": entry_px,
        "exit_price": exit_px,
        "statutory": {"stt": stt, "exchange": exch, "sebi": sebi,
                      "stamp_duty": stamp, "brokerage": brokerage, "gst": gst,
                      "subtotal": statutory},
        "spread_cost": spread_cost,
        "entry_relative_spread": entry["relative_spread"],
        "exit_relative_spread": exit_["relative_spread"],
        "breakeven_move_per_unit": breakeven_move,
        "breakeven_as_fraction_of_entry": (
            (breakeven_move / entry_px) if entry_px else None),
        "rates_verified": rates.verified,
        "rates_effective_from": rates.effective_from,
        "warning": (None if rates.verified else
                    "RATE CARD UNVERIFIED -- these charges were not checked "
                    "against this account's contract notes and must not be "
                    "used to accept or reject a strategy"),
        "fill_basis": entry["basis"],
    }


def cost_gate(result: Dict[str, Any], *,
              max_breakeven_fraction: float = 0.10) -> Dict[str, Any]:
    """Would friction alone make this trade a bad idea?

    A trade needing a large fraction of its own premium just to break even is
    the documented retail trap: the view can be right and the position still
    loses, because the move never covered the friction. Naming that BEFORE the
    trade is the whole point.
    """
    if result.get("refused"):
        return {"verdict": "REFUSED", "reason": result["refused"],
                "detail": result.get("detail")}
    frac = result.get("breakeven_as_fraction_of_entry")
    if frac is None:
        return {"verdict": "REFUSED", "reason": "NO_BREAKEVEN_COMPUTED"}
    ok = frac <= max_breakeven_fraction
    return {
        "verdict": "ACCEPTABLE_FRICTION" if ok else "FRICTION_DOMINATES",
        "breakeven_as_fraction_of_entry": frac,
        "threshold": max_breakeven_fraction,
        "rates_verified": result.get("rates_verified"),
        "note": ("this judges FRICTION ONLY. It says nothing about whether the "
                 "trade has an edge, and passing it is not a reason to trade."),
    }
