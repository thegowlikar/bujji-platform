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


class ConflictingLotSizeError(ValueError):
    """The symbol master carries more than one lot size for one underlying.

    Carries the structured breakdown so callers and tests can assert on the
    real state instead of parsing prose.
    """

    def __init__(self, underlying: str, by_expiry: dict) -> None:
        self.underlying = underlying
        self.by_expiry = by_expiry
        super().__init__(
            f"conflicting lot sizes for {underlying}: "
            + ", ".join(f"{e}={sorted(v)}" for e, v in sorted(by_expiry.items()))
            + " -- a lot-size transition is in progress; size per-contract from "
            "OptionRow.lot_size, not per-underlying."
        )


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

    def _futures_rows_for(self, underlying: str) -> list[OptionRow]:
        """Futures rows (`option_type == "XX"`) for `underlying`, read from
        the same real CSV `_rows_for()` reads.

        Deliberately NOT merged into `_rows_for()` or its
        `_rows_by_underlying` cache: Phase 17I.5's audit confirmed
        `_rows_for()`'s CE/PE-only filter is intentional, protected by
        `test_excludes_futures_rows`, and must stay unchanged. This is an
        additive read path, not a modification of that one.

        Reuses `OptionRow` as a plain row carrier rather than a new
        dataclass -- `strike` carries the CSV's own `-1.0` sentinel for a
        futures row and `option_type` carries `"XX"` verbatim, both
        verified directly against the real NFO CSV in Phase 17I.5.
        """
        rows: list[OptionRow] = []
        with open(self._cache_file, newline="", encoding="utf-8", errors="replace") as f:
            for r in csv.reader(f):
                if len(r) < _MIN_COLUMNS:
                    continue
                if r[_COL_UNDERLYING] != underlying:
                    continue
                if r[_COL_OPTION_TYPE] != "XX":
                    continue
                try:
                    rows.append(OptionRow(
                        symbol=r[_COL_SYMBOL],
                        underlying=underlying,
                        strike=float(r[_COL_STRIKE]),
                        option_type=r[_COL_OPTION_TYPE],
                        expiry_epoch=int(r[_COL_EXPIRY_EPOCH]),
                        lot_size=int(float(r[_COL_LOT_SIZE])),
                    ))
                except (ValueError, IndexError):
                    continue  # Malformed row — skip rather than crash the scan.
        return rows

    def lot_size_for(self, underlying: str) -> int:
        """The exchange lot size for `underlying`, read from the cached
        symbol master -- every options AND futures row, unanimity required.

        Cache-only and synchronous ON PURPOSE: this is called on the trading
        session's startup path, which must not grow a network dependency.
        The cache is refreshed daily by the capture path's `_ensure_fresh()`;
        a missing cache raises `FileNotFoundError` (fail closed -- sizing off
        a guess is worse than not starting).

        Raises `ConflictingLotSizeError` when contracts disagree. That is a
        real state, not a defect: during an exchange lot-size revision the new
        size applies to far-dated series first, so a single per-underlying
        number is momentarily a lie. Callers that hit this must size
        per-contract from `OptionRow.lot_size` instead of per-underlying.

        Raises `LookupError` when the master has no rows for `underlying`.

        WHY THIS EXISTS: the 2026-07-19 audit (module docstring) found the
        live master says NIFTY=65 while config.yaml hardcodes 75, and the
        trading path sized every order from the config value -- 15.4%% oversized
        per lot. This method makes the master the one authoritative read.
        """
        rows = self._rows_for(underlying) + self._futures_rows_for(underlying)
        if not rows:
            raise LookupError(
                f"instrument master has no rows for underlying {underlying!r} "
                f"in {self._cache_file}"
            )
        by_expiry: dict[str, set[int]] = {}
        for row in rows:
            by_expiry.setdefault(row.expiry_date.isoformat(), set()).add(row.lot_size)
        distinct = {size for sizes in by_expiry.values() for size in sizes}
        if len(distinct) > 1:
            raise ConflictingLotSizeError(underlying, by_expiry)
        return distinct.pop()

    async def resolve_nearest_future(self, underlying: str) -> tuple[str, str, int]:
        """Resolve the nearest-upcoming-expiry futures contract for
        `underlying` directly from the real NFO instrument master.

        Phase 17I.5's audit-confirmed replacement for `_futures_symbol()`'s
        provisional, wall-clock-driven symbol construction: the real CSV
        carries a genuine `expiry_epoch` for futures rows (same column as
        options), so there is no need to guess a symbol from today's date.

        Returns `(symbol, expiry_iso, lot_size)` -- no new dataclass,
        mirroring `resolve_atm()`'s own nearest-expiry selection exactly
        (same `>= today - 86400` window, same "soonest listed expiry"
        rule, no new algorithm).
        """
        await self._ensure_fresh()
        rows = self._futures_rows_for(underlying)
        if not rows:
            raise LookupError(
                f"No futures rows found for underlying={underlying!r} in "
                f"the instrument master ({self._cache_file})."
            )

        today_epoch = int(datetime.now(timezone.utc).timestamp())
        upcoming = [r for r in rows if r.expiry_epoch >= today_epoch - 86400]
        if not upcoming:
            raise LookupError(f"No upcoming futures expiries found for {underlying!r}.")
        nearest = min(upcoming, key=lambda r: r.expiry_epoch)

        return nearest.symbol, nearest.expiry_date.isoformat(), nearest.lot_size

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
