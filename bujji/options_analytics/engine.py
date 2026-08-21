"""Chain in, analytics out: IV, Greeks and skew for one expiry.

THE PIPELINE, and every step is either an observation or a disclosed
derivation from one:

  observed chain (ltp, bid, ask, per strike, both legs)
      -> forward + discount factor, recovered by put-call parity
      -> implied volatility per contract, by inverting the observed price
      -> Greeks at that volatility
      -> skew, read off the resulting IV curve

DERIVED IS LABELLED DERIVED. Every record carries `value_class="DERIVED"`,
the model name, the price basis used, the time convention, and the forward
estimate's own fit evidence. A consumer that treats these like the observed
LTP beside them is making a mistake, and the record gives it no excuse.

WHICH PRICE IS INVERTED. The MID of a real two-sided quote by default: the
LTP is the last trade, which may be minutes old on a far strike while the
book has moved, and a stale price implies a stale vol. When there is no
two-sided quote the record says so and falls back to LTP with the basis
recorded as LTP, so a reader can filter on it. Nothing is inverted from a
one-sided book silently.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .black76 import greeks as black76_greeks
from .black76 import time_to_expiry_years
from .forward import ForwardEstimate, estimate_forward
from .implied import implied_volatility

MODEL = "black76_forward"
VALUE_CLASS_DERIVED = "DERIVED"

BASIS_MID = "MID"
BASIS_LTP = "LTP"

STATUS_OK = "OK"
STATUS_NO_FORWARD = "NO_FORWARD"
STATUS_EXPIRED = "EXPIRED_OR_NO_TIME"
STATUS_NO_PRICE = "NO_USABLE_PRICE"


@dataclass(frozen=True)
class ContractAnalytics:
    """Derived analytics for ONE option contract."""

    strike: float
    option_type: str                    # "CE" | "PE"
    status: str
    price_used: Optional[float] = None
    price_basis: Optional[str] = None   # BASIS_MID | BASIS_LTP
    iv: Optional[float] = None
    iv_status: Optional[str] = None
    forward_delta: Optional[float] = None
    gamma: Optional[float] = None
    vega_per_pct: Optional[float] = None
    theta_per_day: Optional[float] = None
    reason: Optional[str] = None
    value_class: str = VALUE_CLASS_DERIVED
    model: str = MODEL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strike": self.strike, "option_type": self.option_type, "status": self.status,
            "price_used": self.price_used, "price_basis": self.price_basis,
            "iv": self.iv, "iv_status": self.iv_status,
            "forward_delta": self.forward_delta, "gamma": self.gamma,
            "vega_per_pct": self.vega_per_pct, "theta_per_day": self.theta_per_day,
            "reason": self.reason, "value_class": self.value_class, "model": self.model,
        }


@dataclass(frozen=True)
class SkewSummary:
    """The IV curve, read off the solved contracts.

    Every field is None unless the contracts it needs actually solved --
    a skew number computed from two surviving strikes would be a shape
    asserted from almost nothing.
    """

    atm_iv: Optional[float] = None
    atm_strike: Optional[float] = None
    put_iv_25d: Optional[float] = None
    call_iv_25d: Optional[float] = None
    risk_reversal_25d: Optional[float] = None   # call IV - put IV at ~25 delta
    butterfly_25d: Optional[float] = None       # wings mean - ATM
    solved_calls: int = 0
    solved_puts: int = 0
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "atm_iv": self.atm_iv, "atm_strike": self.atm_strike,
            "put_iv_25d": self.put_iv_25d, "call_iv_25d": self.call_iv_25d,
            "risk_reversal_25d": self.risk_reversal_25d,
            "butterfly_25d": self.butterfly_25d,
            "solved_calls": self.solved_calls, "solved_puts": self.solved_puts,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ExpiryAnalytics:
    status: str
    expiry: str
    forward: ForwardEstimate
    t_years: Optional[float] = None
    contracts: Tuple[ContractAnalytics, ...] = ()
    skew: SkewSummary = field(default_factory=SkewSummary)
    as_of: Optional[str] = None
    reason: Optional[str] = None
    value_class: str = VALUE_CLASS_DERIVED
    model: str = MODEL
    time_convention: str = "calendar_365_to_1530_expiry"

    @property
    def solved(self) -> int:
        return sum(1 for c in self.contracts if c.iv is not None)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status, "expiry": self.expiry, "as_of": self.as_of,
            "t_years": self.t_years, "forward": self.forward.to_dict(),
            "contracts": [c.to_dict() for c in self.contracts],
            "skew": self.skew.to_dict(), "solved": self.solved,
            "reason": self.reason, "value_class": self.value_class,
            "model": self.model, "time_convention": self.time_convention,
        }


def _usable_price(row: Dict[str, Any]) -> Tuple[Optional[float], Optional[str]]:
    """Mid of a real two-sided quote, else the last trade, else nothing."""
    bid, ask = row.get("bid"), row.get("ask")
    try:
        if bid is not None and ask is not None and float(bid) > 0 and float(ask) > 0:
            return (float(bid) + float(ask)) / 2.0, BASIS_MID
    except (TypeError, ValueError):
        pass
    try:
        ltp = float(row.get("ltp"))
        if ltp > 0:
            return ltp, BASIS_LTP
    except (TypeError, ValueError):
        pass
    return None, None


def _nearest_delta(contracts: Sequence[ContractAnalytics], target: float,
                   option_type: str) -> Optional[ContractAnalytics]:
    candidates = [c for c in contracts
                  if c.option_type == option_type and c.iv is not None
                  and c.forward_delta is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda c: abs(abs(c.forward_delta) - target))


def build_skew(contracts: Sequence[ContractAnalytics],
               forward: Optional[float]) -> SkewSummary:
    """Read the curve. Refuses rather than extrapolating a shape."""
    calls = [c for c in contracts if c.option_type == "CE" and c.iv is not None]
    puts = [c for c in contracts if c.option_type == "PE" and c.iv is not None]
    if forward is None or (not calls and not puts):
        return SkewSummary(solved_calls=len(calls), solved_puts=len(puts),
                           reason="no forward or no solved contracts")

    solved = calls + puts
    atm = min(solved, key=lambda c: abs(c.strike - forward))
    # ATM IV is the MEDIAN of every solved contract at that strike -- the call
    # and the put should agree under parity, and taking one arbitrarily would
    # hide it when they do not.
    at_strike = [c.iv for c in solved if c.strike == atm.strike]
    atm_iv = median(at_strike) if at_strike else None

    put_25 = _nearest_delta(contracts, 0.25, "PE")
    call_25 = _nearest_delta(contracts, 0.25, "CE")
    rr = bf = None
    if put_25 is not None and call_25 is not None:
        rr = call_25.iv - put_25.iv
        if atm_iv is not None:
            bf = 0.5 * (call_25.iv + put_25.iv) - atm_iv

    return SkewSummary(
        atm_iv=atm_iv, atm_strike=atm.strike,
        put_iv_25d=put_25.iv if put_25 else None,
        call_iv_25d=call_25.iv if call_25 else None,
        risk_reversal_25d=rr, butterfly_25d=bf,
        solved_calls=len(calls), solved_puts=len(puts))


def analyse_expiry(
    *,
    rows: Sequence[Dict[str, Any]],
    expiry: str,
    as_of: str,
    expiry_time: str = "15:30:00",
) -> ExpiryAnalytics:
    """Full analytics for one expiry.

    `rows` are chain rows for THIS expiry, each with strike, option_type and
    at least one of bid/ask/ltp. Never raises: every failure mode resolves to
    a status and a reason, because a missing IV must be visibly missing.
    """
    calls: Dict[float, float] = {}
    puts: Dict[float, float] = {}
    basis: Dict[Tuple[float, str], Tuple[float, str]] = {}
    for row in rows:
        try:
            strike = float(row["strike"])
            option_type = row["option_type"]
        except (KeyError, TypeError, ValueError):
            continue
        if option_type not in ("CE", "PE"):
            continue
        value, how = _usable_price(row)
        if value is None:
            continue
        basis[(strike, option_type)] = (value, how)
        (calls if option_type == "CE" else puts)[strike] = value

    forward = estimate_forward(calls, puts, expiry=expiry)
    t_years = time_to_expiry_years(as_of, expiry, expiry_time)

    if t_years is None:
        return ExpiryAnalytics(
            status=STATUS_EXPIRED, expiry=expiry, forward=forward, as_of=as_of,
            reason="expiry has passed -- an expired option has no implied volatility")
    if not forward.is_usable:
        return ExpiryAnalytics(
            status=STATUS_NO_FORWARD, expiry=expiry, forward=forward, as_of=as_of,
            t_years=t_years,
            reason=(f"no forward recovered ({forward.status}) -- every implied vol built "
                    f"on a wrong forward would be quietly wrong, so none is published"))

    contracts: List[ContractAnalytics] = []
    for (strike, option_type), (value, how) in sorted(basis.items()):
        is_call = option_type == "CE"
        solution = implied_volatility(value, forward.forward, strike, t_years, is_call,
                                      forward.discount_factor or 1.0)
        if not solution.is_solved:
            contracts.append(ContractAnalytics(
                strike=strike, option_type=option_type, status=STATUS_OK,
                price_used=value, price_basis=how, iv=None,
                iv_status=solution.status, reason=solution.reason))
            continue
        g = black76_greeks(forward.forward, strike, solution.sigma, t_years, is_call,
                           forward.discount_factor or 1.0)
        contracts.append(ContractAnalytics(
            strike=strike, option_type=option_type, status=STATUS_OK,
            price_used=value, price_basis=how, iv=solution.sigma,
            iv_status=solution.status, forward_delta=g.forward_delta, gamma=g.gamma,
            vega_per_pct=g.vega_per_pct, theta_per_day=g.theta_per_day))

    return ExpiryAnalytics(
        status=STATUS_OK, expiry=expiry, forward=forward, t_years=t_years,
        contracts=tuple(contracts), skew=build_skew(contracts, forward.forward),
        as_of=as_of)
