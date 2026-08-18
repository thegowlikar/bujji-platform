"""Which instruments to stream -- Phase 15Q.

Scope decision (explicit): spot + India VIX + the ATM +/- N strike band
in BOTH CE and PE. With the default N=3 that is 1 + 1 + 14 = 16 live
series.

Rationale, stated so a future phase can revisit it against evidence
rather than guessing at intent: the option chain Bujji OBSERVES is
wider (+/-500 points, `option_chain_strike_range`), but the strikes it
actually CONSTRUCTS positions at cluster near the money. Streaming the
full chain would multiply websocket load and storage for series no
strategy currently trades. The band is a parameter, not a constant --
widen `strikes_each_side` if a future strategy genuinely needs it.

This module builds SYMBOL LISTS only. It never opens a socket, never
subscribes, never calls a broker -- the caller owns the feed.
"""
from __future__ import annotations

from typing import List, Tuple

from .models import KIND_OPTION, KIND_SPOT, KIND_VIX

DEFAULT_STRIKES_EACH_SIDE = 3
DEFAULT_STRIKE_STEP = 50          # NIFTY strike interval (matches config.yaml's `strike_interval: 50`).

NIFTY_SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
INDIA_VIX_SYMBOL = "NSE:INDIAVIX-INDEX"


def atm_strike(spot: float, step: int = DEFAULT_STRIKE_STEP) -> int:
    """Nearest strike to spot on the `step` grid."""
    return int(round(spot / step) * step)


def strike_band(spot: float, strikes_each_side: int = DEFAULT_STRIKES_EACH_SIDE,
                step: int = DEFAULT_STRIKE_STEP) -> List[int]:
    """The ATM +/- N strikes, ascending. Deterministic for a given
    (spot, N, step) -- so a replay reproduces the same band."""
    atm = atm_strike(spot, step)
    return [atm + (i * step) for i in range(-strikes_each_side, strikes_each_side + 1)]


def option_symbol(underlying: str, expiry_code: str, strike: int, option_type: str) -> str:
    """FYERS option symbol convention: NSE:{UNDERLYING}{EXPIRY}{STRIKE}{CE|PE}.

    `expiry_code` is supplied by the caller (e.g. "25807" for a weekly)
    rather than derived here -- this module has no calendar and must
    never guess an expiry. A wrong expiry code would silently stream
    the wrong contract, so it stays the caller's explicit
    responsibility rather than a hidden default.
    """
    if option_type not in ("CE", "PE"):
        raise ValueError(f"option_type must be CE or PE, got {option_type!r}")
    return f"NSE:{underlying}{expiry_code}{strike}{option_type}"


def build_subscription(
    spot: float, expiry_code: str, underlying: str = "NIFTY",
    strikes_each_side: int = DEFAULT_STRIKES_EACH_SIDE,
    step: int = DEFAULT_STRIKE_STEP,
    include_vix: bool = True,
) -> List[Tuple[str, str]]:
    """Returns [(symbol, kind), ...] -- the exact set to stream.

    Deterministic and side-effect free. The caller passes the real,
    already-observed `spot` and the real `expiry_code`; nothing here is
    invented."""
    out: List[Tuple[str, str]] = [(NIFTY_SPOT_SYMBOL, KIND_SPOT)]
    if include_vix:
        out.append((INDIA_VIX_SYMBOL, KIND_VIX))
    for strike in strike_band(spot, strikes_each_side, step):
        out.append((option_symbol(underlying, expiry_code, strike, "CE"), KIND_OPTION))
        out.append((option_symbol(underlying, expiry_code, strike, "PE"), KIND_OPTION))
    return out


def subscription_symbols(*args, **kwargs) -> List[str]:
    """Just the symbol strings, for handing straight to
    `FyersTickFeed.subscribe(symbols=...)`."""
    return [symbol for symbol, _kind in build_subscription(*args, **kwargs)]
