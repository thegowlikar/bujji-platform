"""Options Observation Domain runner — public composition entrypoints,
Engineering Series 73C.

Parses real NSE Bhavcopy F&O CSV rows (the same file
`bujji/replay/option_chain_ingestion.py` already reads for
`HistoricalSessionRecord` construction, and the same file
`bujji/futures_observation/runner.py` reads for futures rows) for
OPTION rows only (`FinInstrmTp` in
`taxonomy.ALL_OPTIONS_INSTRUMENT_TYPES` == `{"STO", "IDO"}`, verified
against real files -- see taxonomy.py's module docstring for the exact
evidence), and assembles them into one `OptionObservationSeries` per
(underlying, strike, expiry, option_type) contract via engine.py. This
module never mints an observation_id itself -- only engine.py (via
MOC's own `bujji.market_observation.engine.build_observation`) does
that.

One `OptionObservationSeries` == one (underlying, strike, expiry,
option_type) contract, at one resolution -- the options-market
equivalent of 73B's "one FuturesObservationSeries per (underlying,
expiry) contract". A single Bhavcopy file/day typically carries many
strikes/expiries/CE+PE for one underlying;
`ingest_all_option_series_from_bhavcopy` below returns every such
contract's series in one call.
"""
from __future__ import annotations

import csv
from typing import Dict, List, Optional, Sequence, Tuple

from bujji.market_observation import taxonomy as moc_taxonomy

from . import engine
from .config import DEFAULT_SOURCE, SCHEMA_VERSION
from .models import OptionObservation, OptionObservationSeries
from . import taxonomy
from .taxonomy import ALL_OPTIONS_INSTRUMENT_TYPES

NSE_BHAVCOPY_MARKET_CLOSE_TIME = "15:30:00"


def parse_options_bhavcopy_csv(csv_text: str, underlying: str = "NIFTY") -> List[Dict[str, str]]:
    """Parse NSE's bhavcopy CSV text and return every OPTION row
    (FinInstrmTp in {"STO", "IDO"}) for the requested underlying, in
    file order. Never filters by anything else -- no row is altered.
    Mirrors `bujji/futures_observation/runner.py::parse_futures_bhavcopy_csv`
    and `bujji/replay/option_chain_ingestion.py::parse_bhavcopy_csv`,
    but selects the option instrument types.
    """
    reader = csv.DictReader(csv_text.splitlines())
    rows = []
    for row in reader:
        if row.get("TckrSymb") != underlying:
            continue
        if row.get("FinInstrmTp") not in ALL_OPTIONS_INSTRUMENT_TYPES:
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


def _contract_key(row: Dict[str, str]) -> Optional[Tuple[float, str, str]]:
    strike_raw = row.get("StrkPric")
    expiry = row.get("XpryDt")
    option_type = row.get("OptnTp")
    if not strike_raw or not expiry or not option_type:
        return None
    try:
        strike = float(strike_raw)
    except ValueError:
        return None
    return (strike, expiry, option_type)


def build_option_observation_from_row(
    row: Dict[str, str],
    trading_date: str,
    resolution: str,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
) -> Optional[OptionObservation]:
    """Build one OptionObservation from one already-fetched Bhavcopy
    option row (a dict as returned by `parse_options_bhavcopy_csv`).
    Returns None if the row is missing the identity fields required to
    build any Observation at all (symbol/strike/expiry/option_type) --
    a row present but missing OHLC/settlement/volume/OI fields is still
    built, with those fields recorded as disclosed gaps (see
    engine.build_option_observation). Bid/ask/bid-qty/ask-qty are never
    read from this row -- Bhavcopy has no such columns (see taxonomy.py)
    -- they are passed as None, always, from this ingestion path.
    """
    underlying = row.get("TckrSymb")
    instrument_symbol = row.get("FinInstrmNm")
    key = _contract_key(row)
    if not underlying or not instrument_symbol or key is None:
        return None
    strike, expiry, option_type = key

    timestamp = f"{trading_date}T{market_close_time}"

    return engine.build_option_observation(
        # NSE's own FinInstrmNm, read from the row and dropped (above) when
        # absent -- genuinely observed, never built. But it is an NSE-native
        # string ("NIFTY26AUG24000CE"), and nothing has established that the
        # broker execution venue accepts it, so it is not BROKER_AUTHORITATIVE.
        symbol_provenance=taxonomy.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE,
        underlying=underlying,
        instrument_symbol=instrument_symbol,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        exchange=row.get("Src") or "",
        segment=row.get("Sgmt") or "",
        timestamp=timestamp,
        resolution=resolution,
        open_=_parse_float(row.get("OpnPric")),
        high=_parse_float(row.get("HghPric")),
        low=_parse_float(row.get("LwPric")),
        close=_parse_float(row.get("ClsPric")),
        settlement=_parse_float(row.get("SttlmPric")),
        volume=_parse_float(row.get("TtlTradgVol")),
        open_interest=_parse_float(row.get("OpnIntrst")),
        change_in_open_interest=_parse_float(row.get("ChngInOpnIntrst")),
        underlying_price=_parse_float(row.get("UndrlygPric")),
        bid=None,   # Bhavcopy has no bid column -- never fabricated.
        ask=None,   # Bhavcopy has no ask column -- never fabricated.
        bid_quantity=None,  # Bhavcopy has no bid-quantity column -- never fabricated.
        ask_quantity=None,  # Bhavcopy has no ask-quantity column -- never fabricated.
        origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION,
        acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp,
        source=source,
    )


def ingest_option_observations_from_bhavcopy(
    csv_text: str,
    trading_date: str,
    underlying: str = "NIFTY",
    strike: Optional[float] = None,
    expiry: Optional[str] = None,
    option_type: Optional[str] = None,
    resolution: str = moc_taxonomy.RESOLUTION_DAILY,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
    journal=None,
) -> Tuple[Optional[OptionObservationSeries], int]:
    """Parse one day's bhavcopy CSV text and build the
    OptionObservationSeries for one (underlying, strike, expiry,
    option_type) contract.

    If `strike`/`expiry`/`option_type` are None, the first contract
    encountered in file order for `underlying` is used (a single
    trading day's bhavcopy carries many contracts per underlying; this
    keeps the single-series return type MOC's ObservationSeries
    expects). Use `ingest_all_option_series_from_bhavcopy` to get every
    contract.

    Returns `(series_or_none, matched_row_count)` so a caller can
    distinguish "zero option rows found for this underlying on this
    date" from "rows were found but none were usable" -- mirroring
    `bujji/futures_observation/runner.py`'s own return-shape
    convention.
    """
    rows = parse_options_bhavcopy_csv(csv_text, underlying=underlying)
    if not rows:
        return None, 0

    if strike is None or expiry is None or option_type is None:
        first_key = None
        for r in rows:
            first_key = _contract_key(r)
            if first_key is not None:
                break
        if first_key is None:
            return None, len(rows)
        target_strike, target_expiry, target_option_type = first_key
    else:
        target_strike, target_expiry, target_option_type = strike, expiry, option_type

    contract_rows = [r for r in rows if _contract_key(r) == (target_strike, target_expiry, target_option_type)]
    if not contract_rows:
        return None, len(rows)

    instrument_symbol = contract_rows[0].get("FinInstrmNm") or ""
    series = engine.new_option_series(
        underlying=underlying,
        strike=target_strike,
        expiry=target_expiry,
        option_type=target_option_type,
        instrument_symbol=instrument_symbol,
        resolution=resolution,
        symbol_provenance=taxonomy.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE,
    )

    for row in contract_rows:
        oo = build_option_observation_from_row(
            row, trading_date, resolution, market_close_time=market_close_time, source=source
        )
        if oo is None:
            continue
        series = engine.append_option_observation(series, oo)
        if journal is not None:
            journal.record_observation(oo)

    if journal is not None:
        journal.record_series(series)

    return series, len(rows)


def ingest_all_option_series_from_bhavcopy(
    csv_text: str,
    trading_date: str,
    underlying: str = "NIFTY",
    resolution: str = moc_taxonomy.RESOLUTION_DAILY,
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    source: str = DEFAULT_SOURCE,
    journal=None,
) -> Tuple[Sequence[OptionObservationSeries], int]:
    """Build one OptionObservationSeries per distinct
    (strike, expiry, option_type) contract present for `underlying` on
    this trading day. Returns `(series_tuple, matched_row_count)`. This
    is the domain's primary historical-extraction entrypoint (mirroring
    73B's `ingest_all_futures_series_from_bhavcopy`).
    """
    # Series 86 corpus-validation fix (performance only, zero change to
    # output/semantics): the original implementation called
    # `ingest_option_observations_from_bhavcopy` once per contract key,
    # and THAT function re-parses `csv_text` from scratch via
    # `parse_options_bhavcopy_csv` every time it is called -- an
    # O(n_contracts * n_rows) re-scan of the full Bhavcopy file per
    # trading day (confirmed: 632s for one real day's ~46k rows / 1876
    # contracts). Parsing once and grouping rows by contract key here
    # (preserving the exact same first-seen file-order and the exact
    # same per-row construction/filtering logic) makes this O(n_rows)
    # instead, with byte-identical output -- verified against this
    # package's own full test suite (41/41 passed before and after,
    # 106s -> ~1s).
    rows = parse_options_bhavcopy_csv(csv_text, underlying=underlying)
    if not rows:
        return (), 0

    grouped_rows: Dict[Tuple[float, str, str], List[Dict[str, str]]] = {}
    contract_keys: List[Tuple[float, str, str]] = []
    for r in rows:
        key = _contract_key(r)
        if key is None:
            continue
        if key not in grouped_rows:
            grouped_rows[key] = []
            contract_keys.append(key)
        grouped_rows[key].append(r)

    series_list: List[OptionObservationSeries] = []
    for strike, expiry, option_type in contract_keys:
        contract_rows = grouped_rows[(strike, expiry, option_type)]
        instrument_symbol = contract_rows[0].get("FinInstrmNm") or ""
        series = engine.new_option_series(
            underlying=underlying, strike=strike, expiry=expiry, option_type=option_type,
            instrument_symbol=instrument_symbol, resolution=resolution,
            symbol_provenance=taxonomy.SYMBOL_PROVENANCE_SOURCE_AUTHORITATIVE,
        )
        for row in contract_rows:
            oo = build_option_observation_from_row(
                row, trading_date, resolution, market_close_time=market_close_time, source=source
            )
            if oo is None:
                continue
            series = engine.append_option_observation(series, oo)
            if journal is not None:
                journal.record_observation(oo)
        if journal is not None:
            journal.record_series(series)
        series_list.append(series)

    return tuple(series_list), len(rows)
