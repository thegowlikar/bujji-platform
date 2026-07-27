"""Tests — Data Acquisition Sprint A: NSE bhavcopy ingestion adapter."""
from bujji.replay.option_chain_ingestion import (
    build_session_record,
    build_session_records_from_bhavcopy,
    parse_bhavcopy_csv,
)
from bujji.replay.validator import validate_session

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)

# Real-shaped rows, structurally identical to NSE's actual bhavcopy
# format (field order, instrument-type codes, symbol naming) but with
# illustrative values -- not a claim these are real market prices.
SAMPLE_ROWS = [
    "2026-07-22,2026-07-22,FO,NSE,IDO,80310,,NIFTY,,2026-07-28,2026-07-28,24000.00,CE,"
    "NIFTY26JUL24000CE,100.0,110.0,95.0,105.0,105.0,98.0,23996.25,105.0,1000,100,50,5000.0,10,F1,75,,,,,",
    "2026-07-22,2026-07-22,FO,NSE,IDO,80311,,NIFTY,,2026-07-28,2026-07-28,24000.00,PE,"
    "NIFTY26JUL24000PE,90.0,95.0,80.0,85.0,85.0,88.0,23996.25,85.0,900,80,40,4000.0,8,F1,75,,,,,",
    "2026-07-22,2026-07-22,FO,NSE,STO,90000,,RELIANCE,,2026-07-28,2026-07-28,3000.00,CE,"
    "RELIANCE26JUL3000CE,50.0,55.0,45.0,48.0,48.0,49.0,2950.0,48.0,500,50,20,1000.0,4,F1,250,,,,,",
    "2026-07-22,2026-07-22,FO,NSE,IDF,80312,,NIFTY,,2026-07-28,2026-07-28,,,"
    "NIFTY26JULFUT,24000.0,24100.0,23900.0,24000.0,24000.0,23990.0,23996.25,24000.0,2000,200,100,10000.0,20,F1,75,,,,,",
]


def _csv_text(rows):
    return "\n".join([HEADER] + rows) + "\n"


def test_parse_filters_to_requested_underlying_option_rows_only():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    # Excludes the RELIANCE row (wrong underlying) and the NIFTY future
    # row (FinInstrmTp=IDF, not an option) -- only the two NIFTY option
    # rows survive.
    assert len(rows) == 2
    assert all(r["TckrSymb"] == "NIFTY" for r in rows)
    assert all(r["OptnTp"] in ("CE", "PE") for r in rows)


def test_build_session_record_from_real_shaped_rows():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    assert record is not None
    assert record.session_id == "NIFTY-2026-07-22"
    assert record.trading_date == "2026-07-22"
    assert record.timestamp == "2026-07-22T15:30:00"
    assert record.spot == 23996.25
    assert len(record.option_chain_entries) == 2
    assert record.option_chain_expiries == ("2026-07-28",)


def test_build_session_record_never_fabricates_when_no_rows():
    record = build_session_record("2026-07-22", [], underlying="NIFTY")
    assert record is None


def test_build_session_record_never_fabricates_when_rows_incomplete():
    incomplete_row = {
        "TckrSymb": "NIFTY",
        "FinInstrmTp": "IDO",
        "StrkPric": "",  # missing strike -- must not be guessed
        "OptnTp": "CE",
        "XpryDt": "2026-07-28",
        "FinInstrmNm": "NIFTY26JUL24000CE",
        "UndrlygPric": "23996.25",
    }
    record = build_session_record("2026-07-22", [incomplete_row], underlying="NIFTY")
    assert record is None


def test_convenience_entry_point_returns_row_count():
    record, matched_rows = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY"
    )
    assert record is not None
    assert matched_rows == 2


def test_ingested_record_passes_series_59_validator_unmodified():
    record, _ = build_session_records_from_bhavcopy(_csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY")
    result = validate_session(record)
    assert result.valid is True
    assert result.issues == ()


def test_no_matching_underlying_produces_no_record():
    record, matched_rows = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="BANKNIFTY"
    )
    assert record is None
    assert matched_rows == 0


def test_original_rows_are_never_mutated():
    text = _csv_text(SAMPLE_ROWS)
    rows_before = parse_bhavcopy_csv(text, underlying="NIFTY")
    build_session_record("2026-07-22", rows_before, underlying="NIFTY")
    rows_after = parse_bhavcopy_csv(text, underlying="NIFTY")
    assert rows_before == rows_after
