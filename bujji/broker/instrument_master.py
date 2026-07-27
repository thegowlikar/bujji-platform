"""FYERS NFO instrument master — real option contract resolution.

Downloads FYERS's public F&O symbol-master CSV (unauthenticated,
``https://public.fyers.in/sym_details/{EXCHANGE}_FO.csv``), caches it locally
(24h TTL, matching the sibling ``bujji-mcp`` project's proven pattern for the
cash-market equivalent), and resolves an ATM option contract for a given
underlying/spot/direction/expiry-preference. No hardcoded symbols — every
resolved symbol is a real row read out of this file.

Column layout verified directly against a LIVE download, fetched during
the 2026-07-19 Capital Management Engine v2 audit (``curl
https://public.fyers.in/sym_details/NSE_FO.csv``), not merely from
documentation:

    101126072135426,NIFTY 21 Jul 26 29450 CE,14,65,0.05,,0915-1530|1815-1915:,
    2026-07-17,1784628000,NSE:NIFTY2672129450CE,10,11,35426,NIFTY,26000,
    29450.0,CE,101000000026000,None,0,0.0

    index 3  = LOT SIZE (verified per-underlying, live, same day: NIFTY=65,
               BANKNIFTY=30, FINNIFTY=60, MIDCPNIFTY=120, RELIANCE=500 --
               each a distinct, plausible, currently-correct exchange lot
               size, confirmed by cross-referencing multiple underlyings in
               the same live file).
    index 8  = expiry (epoch seconds)
    index 9  = FYERS symbol
    index 13 = underlying
    index 15 = strike price
    index 16 = option type (``CE``/``PE``, or ``XX`` for futures -- futures
               rows are skipped)

CRITICAL FINDING from this same audit: NIFTY's lot size in this live file is
**65**, not 75 -- the value hardcoded in ``config.yaml``'s
``market.lot_size``. Before this fix, ``resolve_atm`` ignored this column
entirely and stamped the caller-supplied (static, config-driven) lot_size
onto every resolved contract regardless of what the exchange actually
specifies -- meaning BUJJI was, at time of this audit, sizing every order
off a stale lot size. See docs/AUDIT_LOG.md and
docs/CAPITAL_MANAGEMENT_ENGINE.md for the full writeup.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from ..core.enums import OptionType
from ..core.models import OptionContract

_SYMBOL_MASTER_URL = "https://public.fyers.in/sym_details/{exchange}_FO.csv"
_COL_LOT_SIZE = 3
_COL_EXPIRY_EPOCH = 8
_COL_SYMBOL = 9
_COL_UNDERLYING = 13
_COL_STRIKE = 15
_COL_OPTION_TYPE = 16
_MIN_COLUMNS = 21


@dataclass(frozen=True)
class OptionRow:
    symbol: str
    underlying: str
    strike: float
    option_type: str  # "CE" | "PE"
    expiry_epoch: int
    lot_size: int  # Live exchange lot size, read from the symbol master --
                    # never a config value (see the column-layout docstring
                    # above for how this was verified).

    @property
    def expiry_date(self) -> date:
        return datetime.fromtimestamp(self.expiry_epoch, tz=timezone.utc).date()


class InstrumentMaster:
    """Downloads, caches, and searches the FYERS NFO symbol master."""

    def __init__(self, cache_dir: Path, logger: logging.Logger,
                 exchange: str = "NSE", ttl_seconds: float = 86400.0) -> None:
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._log = logger
        self._exchange = exchange
        self._ttl = ttl_seconds
        self._cache_file = self._cache_dir / f"fyers_fo_{exchange}.csv"
        self._rows_by_underlying: dict[str, list[OptionRow]] = {}

    async def _ensure_fresh(self) -> None:
        stale = (
            not self._cache_file.exists()
            or (time.time() - self._cache_file.stat().st_mtime) > self._ttl
        )
        if stale:
            await asyncio.to_thread(self._download)
            self._rows_by_underlying.clear()  # Force re-parse.

    def _download(self) -> None:
        url = _SYMBOL_MASTER_URL.format(exchange=self._exchange)
        self._log.info("instrument_master_download", extra={"data": {"url": url}})
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(self._cache_file)  # Atomic — never leaves a half-written cache.

    def _rows_for(self, underlying: str) -> list[OptionRow]:
        if underlying in self._rows_by_underlying:
            return self._rows_by_underlying[underlying]
        rows: list[OptionRow] = []
        with open(self._cache_file, newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.reader(f):
                if len(r) < _MIN_COLUMNS:
                    continue
                if r[_COL_UNDERLYING] != underlying:
                    continue
                opt_type = r[_COL_OPTION_TYPE]
                if opt_type not in ("CE", "PE"):
                    continue  # Skip futures (XX) and anything unexpected.
                try:
                    rows.append(OptionRow(
                        symbol=r[_COL_SYMBOL],
                        underlying=underlying,
                        strike=float(r[_COL_STRIKE]),
                        option_type=opt_type,
                        expiry_epoch=int(r[_COL_EXPIRY_EPOCH]),
                        lot_size=int(float(r[_COL_LOT_SIZE])),
                    ))
                except (ValueError, IndexError):
                    continue  # Malformed row — skip rather than crash the scan.
        self._rows_by_underlying[underlying] = rows
        return rows

    async def resolve_atm(
        self, underlying: str, spot: float, option_type: OptionType,
        strike_interval: int, lot_size: int,
    ) -> OptionContract:
        # NOTE: the `lot_size` parameter above is now ONLY a fallback (see
        # the end of this method) -- the authoritative value is read live
        # from the matched row's own lot-size column whenever parsing
        # succeeded for it. This was a real, previously-undiscovered gap:
        # the parameter used to be stamped onto every contract unconditionally.
        """Resolve the nearest-expiry ATM contract for underlying/spot/side.

        "Nearest expiry" covers both weekly and monthly instruments
        uniformly — it is simply the soonest expiry (today or later) present
        in the real instrument list, which is a weekly expiry whenever one
        exists (the normal case) and falls through to the next listed
        (monthly) expiry otherwise.
        """
        await self._ensure_fresh()
        rows = self._rows_for(underlying)
        if not rows:
            raise LookupError(
                f"No F&O rows found for underlying={underlying!r} in the "
                f"instrument master ({self._cache_file}); it may have "
                f"downloaded incorrectly or the underlying is unlisted."
            )

        today_epoch = int(datetime.now(timezone.utc).timestamp())
        future_expiries = sorted({r.expiry_epoch for r in rows if r.expiry_epoch >= today_epoch - 86400})
        if not future_expiries:
            raise LookupError(f"No upcoming expiries found for {underlying!r}.")
        nearest_expiry = future_expiries[0]

        # Same canonical rounding as Broker.atm_strike() — do not
        # reimplement independently; a second, drifted rounding formula
        # here would be indistinguishable from a real CE/PE strike-
        # mismatch bug the day the two formulas disagree.
        from .base import Broker
        target_strike = Broker.atm_strike(spot, strike_interval)
        candidates = [
            r for r in rows
            if r.expiry_epoch == nearest_expiry and r.option_type == option_type.value
        ]
        if not candidates:
            raise LookupError(
                f"No {option_type.value} contracts found for {underlying!r} "
                f"at expiry {nearest_expiry}."
            )
        best = min(candidates, key=lambda r: abs(r.strike - target_strike))

        # Prefer the live, exchange-sourced lot size from the matched row;
        # fall back to the caller-supplied config value ONLY if the row's
        # lot size failed to parse as a positive integer (should not
        # happen for a well-formed file, but never silently size an order
        # off a zero/negative/garbage figure).
        effective_lot_size = best.lot_size if best.lot_size > 0 else lot_size
        if best.lot_size <= 0:
            self._log.warning(
                "instrument_master_lot_size_fallback",
                extra={"data": {"symbol": best.symbol, "config_lot_size": lot_size}},
            )

        return OptionContract(
            symbol=best.symbol,
            underlying=underlying,
            strike=int(best.strike),
            option_type=option_type,
            expiry=best.expiry_date.isoformat(),
            lot_size=effective_lot_size,
        )
