"""Phase 20.26 -- the thin broker -> OptionMarketDataForCycle adapter.

FyersBroker -> OptionMarketDataAdapter -> OptionMarketDataForCycle
(Phase 20.25's own real input contract).

Reuses ONLY already-real, already-tested `bujji.broker.base.Broker`
ABC methods (`resolve_atm_contract`/`get_ltp`/`get_quote`/
`get_option_chain`) -- confirmed live-verified against real FYERS
responses (see each method's own docstring in `bujji/broker/fyers.py`).
No option-chain parsing, no quote parsing, no IV/Greeks math is
reimplemented here -- `get_option_chain()`'s own return shape
(`list[tuple[float, float, float]]`, i.e. `(strike, ce_oi, pe_oi)`)
already matches `OptionMarketDataForCycle.strikes` exactly, so no
translation is needed for that field at all.

Typed against the `Broker` ABC, not `FyersBroker` specifically -- any
broker implementing the same real interface (a real, guarded
`FyersBroker`, `HybridPaperBroker`, `PaperBroker`, or `ReplayBroker`)
can supply this adapter safely in shadow mode. Only READ-ONLY methods
are called (`resolve_atm_contract`/`get_ltp`/`get_quote`/
`get_option_chain`) -- none of `place_order`/`modify_order`/
`cancel_order`/`connect` appear anywhere in this file. A real
`FyersBroker` used with this adapter is expected to already be wrapped
in `bujji.broker.guard.disable_live_execution()` by its own
constructing caller (the live entrypoint) -- this module does not
re-apply or depend on that guard; it simply never calls a
capability the guard would block.

Every field on the returned `OptionMarketDataForCycle` is a real
value obtained from a real broker call. If ANY required real value is
missing (a `None` quote, a broker exception, an unresolvable
contract), this adapter returns `None` -- never a partially-fabricated
object with a defaulted/zero value standing in for missing data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from bujji.broker.base import Broker
from bujji.core.enums import Direction
from bujji.mic_runtime_context import OptionMarketDataForCycle

# NIFTY's real, disclosed strike interval -- the same value already
# established and used by `scripts/verify_fo_access.py`'s own real F&O
# access verification (NIFTY_STRIKE_INTERVAL = 50), mirrored here
# rather than imported (that script is a one-off diagnostic, not a
# stable public API). `lot_size` is only ever a fallback: `resolve_atm`
# (Phase 20.13's own already-established finding, see `InstrumentMaster.
# resolve_atm`'s own docstring) always prefers the live NFO row's real
# lot size when parsing succeeds.
NIFTY_STRIKE_INTERVAL = 50
NIFTY_LOT_SIZE_FALLBACK = 75


def _t_years_to_expiry(expiry_iso: str, now: datetime) -> Optional[float]:
    """Pure date arithmetic -- calendar days to expiry / 365. Returns
    `None` on any unparseable/invalid expiry rather than guessing."""
    try:
        expiry_dt = datetime.fromisoformat(expiry_iso)
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        delta_days = (expiry_dt - now).total_seconds() / 86400.0
        if delta_days <= 0:
            return None
        return delta_days / 365.0
    except (ValueError, TypeError):
        return None


async def fetch_option_market_data_for_cycle(
    broker: Broker, underlying: str, spot: float, *, now: datetime,
    strike_interval: int = NIFTY_STRIKE_INTERVAL, lot_size: int = NIFTY_LOT_SIZE_FALLBACK,
    strike_count: int = 5,
) -> Optional[OptionMarketDataForCycle]:
    """Returns `None` on ANY missing/invalid real value -- never
    fabricates a partial `OptionMarketDataForCycle`. Callers (the live
    entrypoint) are expected to treat `None` as "no option market data
    this cycle" and pass it straight through to `process_cycle(...,
    option_market_data=None)`, exactly its own documented default."""
    try:
        ce_contract = await broker.resolve_atm_contract(underlying, spot, Direction.BEARISH, strike_interval, lot_size)
        pe_contract = await broker.resolve_atm_contract(underlying, spot, Direction.BULLISH, strike_interval, lot_size)
    except Exception:  # noqa: BLE001 -- a resolution failure means no real data this cycle, never a crash.
        return None

    if ce_contract.strike != pe_contract.strike:
        # A real, honest inconsistency (should never happen for two ATM
        # resolutions at the same spot) -- fail closed rather than pick one.
        return None

    t_years = _t_years_to_expiry(ce_contract.expiry, now)
    if t_years is None:
        return None

    try:
        ce_premium = await broker.get_ltp(ce_contract)
        pe_premium = await broker.get_ltp(pe_contract)
        ce_quote = await broker.get_quote(ce_contract)
        pe_quote = await broker.get_quote(pe_contract)
        strikes = await broker.get_option_chain(underlying, spot, strike_count)
    except Exception:  # noqa: BLE001 -- any real broker/network failure -> honestly no data this cycle.
        return None

    if ce_premium is None or pe_premium is None or ce_premium <= 0 or pe_premium <= 0:
        return None
    if ce_quote is None or pe_quote is None:
        return None
    if strikes is None:
        return None

    return OptionMarketDataForCycle(
        spot=spot, strike=float(ce_contract.strike), t_years=t_years,
        ce_premium=float(ce_premium), pe_premium=float(pe_premium),
        ce_bid=ce_quote["bid"], ce_ask=ce_quote["ask"], pe_bid=pe_quote["bid"], pe_ask=pe_quote["ask"],
        strikes=tuple(strikes),
    )
