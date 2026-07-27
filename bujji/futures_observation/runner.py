"""Futures Observation Domain runner — public composition entrypoints,
Engineering Series 73B.

Parses real NSE Bhavcopy F&O CSV rows (the same file
`bujji/replay/option_chain_ingestion.py` already reads for option rows)
for FUTURES rows only (`FinInstrmTp` in `taxonomy.ALL_FUTURES_INSTRUMENT_TYPES`
== `{"STF", "IDF"}`, verified against real files -- see taxonomy.py's
module docstring for the exact evidence), and assembles them into a
`FuturesObservationSeries` via engine.py. This module never mints an
observation_id itself -- only engine.py (via MOC's own
`bujji.market_observation.engine.build_observation`) does that.

One `FuturesObservationSeries` == one (underlying, expiry) contract, at
one resolution -- matching `docs/MARKET_OBSERVATION_CONTRACT.md`'s own
"instrument is the contract-specific string" convention. A single
Bhavcopy file/day typically carries several expiries (near/mid/far
month) for one underlying; callers that want all of them build one
series per expiry, or use `ingest_all_futures_series_from_bhavcopy`
below to get every contract's series in one call.
"""
from __future__ import annotations

import csv
from typing import Dict, List, Optional, Sequence, Tuple

from bujji.market_observation import taxonomy as moc_taxonomy

from . import engine
from .config import DEFAULT_SOURCE, SCHEMA_VERSION
from .models import FuturesObservation, FuturesObservationSeries
from .taxonomy import ALL_FUTURES_INSTRUMENT_TYPES

NSE_BHAVCOPY_MARKET_CLOSE_TIME = "15:30:00"


def parse_futures_bhavcopy_csv(csv_text: str, underlying: str = "NIFTY") -> List[Dict[str, str]]:
    """Parse NSE's bhavcopy CSV text and return every FUTURES row
    (FinInstrmTp in {"STF", "IDF"}) for the requested underlying, in
    file order. Never filters by anything else -- no row is altered.
    Mirrors `bujji/replay/option_chain_ingestion.py::parse_bhavcopy_csv`,
    but selects the futures instrument types instead of the option
    ones.
    """
    reader = csv.DictReader(csv_text.splitlines())
    rows = []
    for row in reader:
        if row.get("TckrSymb") != underlying:
            continue
        if row.get("FinInstrmTp") not in ALL_FUTURES_INSTRUMENT_TYPES:
            continue
        rows.append(row)
    return rows


def _parse_float(raw: Optional[str]) -> Optional[float]:
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_futures_observation_from_row(
    row: Dict[str, str],
    trading_date: str,
    resolution: str,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
) -> Optional[FuturesObservation]:
    """Build one FuturesObservation from one already-fetched Bhavcopy
    futures row (a dict as returned by `parse_futures_bhavcopy_csv`).
    Returns None if the row is missing the identity fields required to
    build any Observation at all (symbol/expiry) -- a row present but
    missing OHLC/volume/OI fields is still built, with those fields
    recorded as disclosed gaps (see engine.build_futures_observation).
    """
    underlying = row.get("TckrSymb")
    expiry = row.get("XpryDt")
    instrument_symbol = row.get("FinInstrmNm")
    if not underlying or not expiry or not instrument_symbol:
        return None

    timestamp = f"{trading_date}T{market_close_time}"

    return engine.build_futures_observation(
        underlying=underlying,
        instrument_symbol=instrument_symbol,
        expiry=expiry,
        exchange=row.get("Src") or "",
        segment=row.get("Sgmt") or "",
        timestamp=timestamp,
        resolution=resolution,
        open_=_parse_float(row.get("OpnPric")),
        high=_parse_float(row.get("HghPric")),
        low=_parse_float(row.get("LwPric")),
        close=_parse_float(row.get("ClsPric")),
        volume=_parse_float(row.get("TtlTradgVol")),
        open_interest=_parse_float(row.get("OpnIntrst")),
        change_in_open_interest=_parse_float(row.get("ChngInOpnIntrst")),
        settlement_price=_parse_float(row.get("SttlmPric")),
        underlying_price=_parse_float(row.get("UndrlygPric")),
        origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION,
        acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp,
        source=source,
    )


def ingest_futures_observations_from_bhavcopy(
    csv_text: str,
    trading_date: str,
    underlying: str = "NIFTY",
    expiry: Optional[str] = None,
    resolution: str = moc_taxonomy.RESOLUTION_DAILY,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
    journal=None,
) -> Tuple[Optional[FuturesObservationSeries], int]:
    """Parse one day's bhavcopy CSV text and build the
    FuturesObservationSeries for one (underlying, expiry) contract.

    If `expiry` is None, the first expiry encountered in file order for
    `underlying` is used (a single trading day's bhavcopy carries
    several expiries per underlying; this keeps the single-series
    return type MOC's ObservationSeries expects). Use
    `ingest_all_futures_series_from_bhavcopy` to get every contract.

    Returns `(series_or_none, matched_row_count)` so a caller can
    distinguish "zero futures rows found for this underlying on this
    date" from "rows were found but none were usable" -- mirroring
    `bujji/replay/option_chain_ingestion.py`'s own return-shape
    convention.
    """
    rows = parse_futures_bhavcopy_csv(csv_text, underlying=underlying)
    if not rows:
        return None, 0

    target_expiry = expiry or rows[0].get("XpryDt")
    contract_rows = [r for r in rows if r.get("XpryDt") == target_expiry]
    if not contract_rows:
        return None, len(rows)

    instrument_symbol = contract_rows[0].get("FinInstrmNm") or ""
    series = engine.new_futures_series(
        underlying=underlying,
        expiry=target_expiry or "",
        instrument_symbol=instrument_symbol,
        resolution=resolution,
    )

    for row in contract_rows:
        fo = build_futures_observation_from_row(
            row, trading_date, resolution, market_close_time=market_close_time, source=source
        )
        if fo is None:
            continue
        series = engine.append_futures_observation(series, fo)
        if journal is not None:
            journal.record_observation(fo)

    if journal is not None:
        journal.record_series(series)

    return series, len(rows)


def ingest_all_futures_series_from_bhavcopy(
    csv_text: str,
    trading_date: str,
    underlying: str = "NIFTY",
    resolution: str = moc_taxonomy.RESOLUTION_DAILY,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
    journal=None,
) -> Tuple[Sequence[FuturesObservationSeries], int]:
    """Build one FuturesObservationSeries per distinct expiry present
    for `underlying` on this trading day. Returns
    `(series_tuple, matched_row_count)`.
    """
    rows = parse_futures_bhavcopy_csv(csv_text, underlying=underlying)
    if not rows:
        return (), 0

    expiries: List[str] = []
    for r in rows:
        e = r.get("XpryDt")
        if e and e not in expiries:
            expiries.append(e)

    series_list: List[FuturesObservationSeries] = []
    for e in expiries:
        series, _ = ingest_futures_observations_from_bhavcopy(
            csv_text,
            trading_date,
            underlying=underlying,
            expiry=e,
            resolution=resolution,
            market_close_time=market_close_time,
            source=source,
            journal=journal,
        )
        if series is not None:
            series_list.append(series)

    return tuple(series_list), len(rows)
