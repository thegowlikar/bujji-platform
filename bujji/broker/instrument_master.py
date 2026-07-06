"""FYERS NFO instrument master — real option contract resolution.

Downloads FYERS's public F&O symbol-master CSV (unauthenticated,
``https://public.fyers.in/sym_details/{EXCHANGE}_FO.csv``), caches it locally
(24h TTL, matching the sibling ``bujji-mcp`` project's proven pattern for the
cash-market equivalent), and resolves an ATM option contract for a given
underlying/spot/direction/expiry-preference. No hardcoded symbols — every
resolved symbol is a real row read out of this file.

Column layout verified directly against a live download (see
``docs/FYERS_TRANSPORT_READINESS.md``): for a NIFTY option row such as
``...,1783418400,NSE:NIFTY2670729450CE,...,NIFTY,26000,29450.0,CE,...`` —
index 8 = expiry (epoch seconds), index 9 = FYERS symbol, index 13 =
underlying, index 15 = strike price, index 16 = option type (``CE``/``PE``,
or ``XX`` for futures — futures rows are skipped).
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
                    ))
                except (ValueError, IndexError):
                    continue  # Malformed row — skip rather than crash the scan.
        self._rows_by_underlying[underlying] = rows
        return rows

    async def resolve_atm(
        self, underlying: str, spot: float, option_type: OptionType,
        strike_interval: int, lot_size: int,
    ) -> OptionContract:
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

        target_strike = round(spot / strike_interval) * strike_interval
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

        return OptionContract(
            symbol=best.symbol,
            underlying=underlying,
            strike=int(best.strike),
            option_type=option_type,
            expiry=best.expiry_date.isoformat(),
            lot_size=lot_size,
        )
