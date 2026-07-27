"""Option Chain Ingestion — Data Acquisition Sprint A (evidence fields
populated by Data Acquisition Sprint C).

Converts NSE's own official, free, self-serve daily Futures & Options
Bhavcopy CSV (`https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_..._<YYYYMMDD>_F_0000.csv.zip`,
published under NSE's public "All Reports" disclosure -- no login, no
API key, no scraping-in-violation-of-terms; it is the same regulatory
end-of-day disclosure every NSE member and vendor already reads) into
`HistoricalSessionRecord` objects (Series 59, schema extended by
Series 64), unmodified.

This module reads provider data and converts it. It never fabricates a
strike, an expiry, an option type, or a spot price -- every value in
every `HistoricalSessionRecord` this module produces is read directly
from a real bhavcopy row. It never interpolates a missing contract and
never invents a session for a date with no usable data (see
`build_session_record`'s `None` return).

Known, disclosed limitation: NSE's bhavcopy is an end-of-day
settlement snapshot -- one option-chain snapshot per trading day, not
an intraday/tick-level time series, and it carries no Greeks and no
bid/ask. See `docs/HISTORICAL_REPLAY_CORPUS.md` and Data Acquisition
Sprint A's own comparison matrix for how this compares to other
evaluated providers.

Data Acquisition Sprint C populates the Series 64 evidence fields this
module previously left empty, from real sources only:
- **Option open interest / change in OI**: read directly from the same
  bhavcopy rows already parsed here (`OpnIntrst`/`ChngInOpnIntrst`
  columns) -- the data was always present in the source, only unused
  until now.
- **Session metadata** (`session_exchange`, `session_segment`): read
  directly from each row's own `Src`/`Sgmt` columns.
- **India VIX**: NOT sourced from bhavcopy at all (bhavcopy has no VIX
  column) -- `vix`/`vix_as_of` are accepted as optional pass-through
  parameters, to be supplied by the caller from a separate real source
  (the connected FYERS MCP's `fyers_historical`, symbol
  `NSE:INDIAVIX-INDEX`, proven in Qualification Data Enrichment Sprint
  B). This module never fetches VIX itself and never fabricates one --
  if the caller supplies nothing, `vix`/`vix_as_of` remain `None`,
  exactly Series 64's own "missing value, never inferred" contract.
- **Bid/ask**: left `None` in every `OptionLiquiditySnapshot` produced
  here -- bhavcopy has no bid/ask column, and this sprint's own
  explicit non-goals forbid fabricating one.
"""
from __future__ import annotations

import csv
from typing import Dict, List, Optional, Sequence, Tuple

from .historical_session import OptionLiquiditySnapshot
from .validator import HistoricalSessionRecord

NSE_BHAVCOPY_MARKET_CLOSE_TIME = "15:30:00"

# NSE bhavcopy's own FinInstrmTp codes for option contracts: "STO"
# (stock options) and "IDO" (index options, e.g. NIFTY/BANKNIFTY).
# Futures ("STF"/"IDF") and any other instrument type are never
# included -- this ingestion adapter produces option-chain data only.
OPTION_INSTRUMENT_TYPES = ("STO", "IDO")


def parse_bhavcopy_csv(csv_text: str, underlying: str = "NIFTY") -> List[Dict[str, str]]:
    """Parse NSE's bhavcopy CSV text and return every row for the
    requested underlying's option contracts, in file order. Never
    filters by anything other than `TckrSymb`/`FinInstrmTp` -- no row
    is altered.
    """
    reader = csv.DictReader(csv_text.splitlines())
    rows = []
    for row in reader:
        if row.get("TckrSymb") != underlying:
            continue
        if row.get("FinInstrmTp") not in OPTION_INSTRUMENT_TYPES:
            continue
        rows.append(row)
    return rows


def _parse_int(raw: Optional[str]) -> Optional[int]:
    if raw in (None, ""):
        return None
    try:
        return int(float(raw))
    except ValueError:
        return None


def build_session_record(
    trading_date: str,
    rows: Sequence[Dict[str, str]],
    underlying: str = "NIFTY",
    market_close_time: str = NSE_BHAVCOPY_MARKET_CLOSE_TIME,
    vix: Optional[float] = None,
    vix_as_of: Optional[str] = None,
    open_price: Optional[float] = None,
    high_price: Optional[float] = None,
    low_price: Optional[float] = None,
    close_price: Optional[float] = None,
) -> Optional[HistoricalSessionRecord]:
    """Build one `HistoricalSessionRecord` from every bhavcopy row for
    one underlying on one trading day.

    `vix`/`vix_as_of` and `open_price`/`high_price`/`low_price`/
    `close_price` (Series 67) are optional pass-through parameters --
    this function never fetches either from bhavcopy (it has neither a
    VIX column nor an underlying-index OHLC column) and never
    fabricates a value; the caller supplies them from a separate real
    source (e.g. FYERS `fyers_historical` daily OHLC for the
    underlying index), or omits them and they remain `None`.

    Returns `None` if no usable rows are present -- the caller is
    expected to record that as a genuine data gap for that date, never
    to silently skip it or substitute a fabricated session.
    """
    if not rows:
        return None

    timestamp = f"{trading_date}T{market_close_time}"
    entries: List[Tuple[float, str, str, str]] = []
    liquidity: List[OptionLiquiditySnapshot] = []
    expiries = set()
    underlying_price: Optional[float] = None
    session_exchange: Optional[str] = None
    session_segment: Optional[str] = None

    for row in rows:
        strike_raw = row.get("StrkPric")
        option_type = row.get("OptnTp")
        expiry = row.get("XpryDt")
        symbol = row.get("FinInstrmNm")
        if not strike_raw or not option_type or not expiry or not symbol:
            continue
        try:
            strike = float(strike_raw)
        except ValueError:
            continue

        entries.append((strike, option_type, expiry, symbol))
        expiries.add(expiry)

        # Open interest / change in OI: read directly from the same
        # row, never inferred from a different row or a different day.
        liquidity.append(
            OptionLiquiditySnapshot(
                strike=int(strike),
                option_type=option_type,
                expiry=expiry,
                contract_symbol=symbol,
                open_interest=_parse_int(row.get("OpnIntrst")),
                change_in_open_interest=_parse_int(row.get("ChngInOpnIntrst")),
                bid=None,  # bhavcopy has no bid/ask column -- never fabricated.
                ask=None,
            )
        )

        if underlying_price is None:
            up_raw = row.get("UndrlygPric")
            if up_raw not in (None, ""):
                try:
                    underlying_price = float(up_raw)
                except ValueError:
                    pass

        if session_exchange is None and row.get("Src"):
            session_exchange = row["Src"]
        if session_segment is None and row.get("Sgmt"):
            session_segment = row["Sgmt"]

    if not entries or underlying_price is None:
        return None

    return HistoricalSessionRecord(
        session_id=f"{underlying}-{trading_date}",
        trading_date=trading_date,
        timestamp=timestamp,
        market_context=None,
        market_opinion=None,
        context_stability=None,
        calibration=None,
        governance=None,
        lifecycle=None,
        contract=None,
        spot=underlying_price,
        spot_as_of=timestamp,
        option_chain_entries=tuple(entries),
        option_chain_expiries=tuple(sorted(expiries)),
        option_chain_as_of=timestamp,
        option_chain_liquidity=tuple(liquidity),
        vix=vix,
        vix_as_of=vix_as_of,
        session_exchange=session_exchange,
        session_segment=session_segment,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
    )


def build_session_records_from_bhavcopy(
    csv_text: str,
    trading_date: str,
    underlying: str = "NIFTY",
    vix: Optional[float] = None,
    vix_as_of: Optional[str] = None,
    open_price: Optional[float] = None,
    high_price: Optional[float] = None,
    low_price: Optional[float] = None,
    close_price: Optional[float] = None,
) -> Tuple[Optional[HistoricalSessionRecord], int]:
    """Convenience entry point: parse one day's bhavcopy CSV text and
    build its `HistoricalSessionRecord`. Returns `(record_or_none,
    matched_row_count)` so a caller can distinguish "zero rows found
    for this underlying on this date" from "rows were found but none
    were usable."

    `vix`/`vix_as_of` and `open_price`/`high_price`/`low_price`/
    `close_price` are optional -- pass real values sourced separately
    (e.g. from FYERS) to have them attached; omit them and the
    record's corresponding fields remain `None`, exactly as before
    this sprint.
    """
    rows = parse_bhavcopy_csv(csv_text, underlying=underlying)
    record = build_session_record(
        trading_date,
        rows,
        underlying=underlying,
        vix=vix,
        vix_as_of=vix_as_of,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
    )
    return record, len(rows)
