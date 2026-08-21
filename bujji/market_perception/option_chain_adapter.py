"""Option Chain Adapter -- Shadow Campaign v2 Phase 1.

Builds one OptionChainSnapshot per cycle:
  - strikes/symbols/lot_size resolved OFFLINE from the cached
    instrument master CSV (no network, no broker call -- same column
    mapping used throughout this project's offline ATM resolution work)
  - per-leg bid/ask/spread via broker.get_quote() (read-only)
  - per-strike OI via broker.get_option_chain() (read-only)

Deliberately NOT populated this phase: volume (get_quote()'s own
docstring explains why FYERS's per-symbol volume figure is distrusted
and never used there), IV, and Greeks -- computing either requires
calling volatility_brain/greeks_brain, which is Phase 2 (Intelligence
Engine Integration) work, not this raw-collection layer. Left as None,
never fabricated.
"""
from __future__ import annotations

import csv
import datetime
import time
from typing import Dict, List, Optional, Tuple

from bujji.core.enums import OptionType
from bujji.core.models import OptionContract

from .models import OptionChainConfig, OptionChainSnapshot, OptionLeg

DEFAULT_CACHE_FILE = "/opt/bujji/app/data/instrument_master/fyers_fo_NSE.csv"
COL_LOT_SIZE, COL_EXPIRY_EPOCH, COL_SYMBOL = 3, 8, 9
COL_UNDERLYING, COL_STRIKE, COL_OPTION_TYPE = 13, 15, 16


def resolve_chain_contracts(
    underlying: str, spot: float, config: OptionChainConfig, cache_file: str = DEFAULT_CACHE_FILE,
) -> Tuple[str, List[OptionContract]]:
    """Offline: nearest unexpired weekly expiry + every strike within
    +/- config.strike_range of spot. Reads the cached instrument
    master CSV only. Returns (expiry_iso_date, contracts)."""
    now_epoch = time.time()
    rows = []
    with open(cache_file) as f:
        for row in csv.reader(f):
            if len(row) <= COL_OPTION_TYPE:
                continue
            if row[COL_UNDERLYING] != underlying or row[COL_OPTION_TYPE] not in ("CE", "PE"):
                continue
            try:
                expiry_epoch = int(row[COL_EXPIRY_EPOCH])
                strike = float(row[COL_STRIKE])
                lot_size = int(row[COL_LOT_SIZE])
            except ValueError:
                continue
            if expiry_epoch < now_epoch:
                continue
            rows.append((expiry_epoch, strike, row[COL_OPTION_TYPE], row[COL_SYMBOL], lot_size))

    if not rows:
        return "", []

    nearest_expiry = min(r[0] for r in rows)
    same_expiry = [r for r in rows if r[0] == nearest_expiry]

    lo, hi = spot - config.strike_range, spot + config.strike_range
    contracts: List[OptionContract] = []
    for _, strike, opt_type, symbol, lot_size in same_expiry:
        if strike % config.strike_step != 0:
            continue
        if strike < lo or strike > hi:
            continue
        contracts.append(
            OptionContract(
                symbol=symbol, underlying=underlying, strike=int(strike),
                option_type=OptionType.CE if opt_type == "CE" else OptionType.PE,
                expiry=str(nearest_expiry), lot_size=lot_size,
            )
        )

    expiry_date = datetime.datetime.fromtimestamp(nearest_expiry, datetime.timezone.utc).date().isoformat()
    return expiry_date, contracts


async def build_option_chain_snapshot(
    broker, underlying: str, spot: float, config: OptionChainConfig, cache_file: str = DEFAULT_CACHE_FILE,
) -> Optional[OptionChainSnapshot]:
    expiry, contracts = resolve_chain_contracts(underlying, spot, config, cache_file)
    if not contracts:
        return None

    oi_by_strike: Dict[float, Tuple[float, float]] = {}
    try:
        strike_count = max(1, config.strike_range // config.strike_step)
        chain = await broker.get_option_chain(underlying, spot, strike_count=strike_count)
        if chain:
            for strike, ce_oi, pe_oi in chain:
                oi_by_strike[float(strike)] = (ce_oi, pe_oi)
    except Exception:
        pass  # OI is best-effort -- legs are still returned with open_interest=None.

    legs: List[OptionLeg] = []
    for contract in contracts:
        try:
            quote = await broker.get_quote(contract)
        except Exception:
            quote = None
        quote = quote or {}
        ce_pe = oi_by_strike.get(float(contract.strike))
        oi = None
        if ce_pe is not None:
            oi = ce_pe[0] if contract.option_type == OptionType.CE else ce_pe[1]
        legs.append(
            OptionLeg(
                symbol=contract.symbol, strike=float(contract.strike),
                option_type=contract.option_type.value,
                ltp=None,  # the quotes endpoint used here returns bid/ask, not a separate LTP field
                bid=quote.get("bid"), ask=quote.get("ask"), spread=quote.get("spread"),
                volume=None, open_interest=oi,
                iv=None, delta=None, gamma=None, theta=None, vega=None,
            )
        )

    atm_strike = min((c.strike for c in contracts), key=lambda s: abs(s - spot))
    return OptionChainSnapshot(
        underlying=underlying, expiry=expiry, atm_strike=float(atm_strike),
        config=config, legs=tuple(legs),
    )
