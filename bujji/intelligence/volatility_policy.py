"""VolatilityReferencePolicy — Phase 19.2.2.

Formalizes an ALREADY-REAL, ALREADY-LIVE convention this project uses
elsewhere -- `bujji/market_perception/option_chain_adapter.py`'s own
`atm_strike = min((c.strike for c in contracts), key=lambda s: abs(s - spot))`,
confirmed live by Phase 19.2.1's own audit and cited there by name.
That expression is extracted here as a pure, reusable function rather
than reimplemented -- the live adapter itself is untouched (it is
tightly coupled to an async broker quote fetch, a different concern
from this pure selection rule).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

VOLATILITY_REFERENCE_ATM_STRADDLE = "ATM_STRADDLE"
ALL_VOLATILITY_REFERENCE_POLICIES = (VOLATILITY_REFERENCE_ATM_STRADDLE,)


def select_atm_strike(strikes: Sequence[float], spot: float) -> float:
    """The exact expression already proven live in
    `option_chain_adapter.py`, extracted for reuse -- nearest strike to
    spot. Raises on an empty `strikes` sequence rather than returning a
    guessed value (fails closed, matching every other selection rule in
    this project)."""
    if not strikes:
        raise ValueError("select_atm_strike: strikes must be non-empty")
    return min(strikes, key=lambda s: abs(s - spot))


@dataclass(frozen=True)
class AtmStraddleSelection:
    """The result of applying `VOLATILITY_REFERENCE_ATM_STRADDLE`
    against one real options chain snapshot -- same expiry, CE+PE at
    the nearest strike to spot."""

    policy: str
    atm_strike: float
    expiry: str
    ce_premium: float
    pe_premium: float
    spot: float


def select_atm_straddle(
    spot: float, expiry: str, contracts: Sequence[Tuple[float, str, float]],
) -> AtmStraddleSelection:
    """`contracts`: (strike, option_type, premium) tuples for ONE
    expiry only -- the caller (the future Reality Intelligence Adapter,
    Phase 19.1's own scoped work) is responsible for filtering to a
    single expiry before calling this; this function does not guess
    which expiry the caller meant."""
    strikes = sorted({c[0] for c in contracts})
    atm_strike = select_atm_strike(strikes, spot)
    ce = next((c[2] for c in contracts if c[0] == atm_strike and c[1] == "CE"), None)
    pe = next((c[2] for c in contracts if c[0] == atm_strike and c[1] == "PE"), None)
    if ce is None or pe is None:
        raise ValueError(
            f"select_atm_straddle: ATM strike {atm_strike} is missing its CE or PE leg "
            "-- a genuinely incomplete chain, never silently filled in."
        )
    return AtmStraddleSelection(
        policy=VOLATILITY_REFERENCE_ATM_STRADDLE, atm_strike=atm_strike, expiry=expiry,
        ce_premium=ce, pe_premium=pe, spot=spot,
    )
