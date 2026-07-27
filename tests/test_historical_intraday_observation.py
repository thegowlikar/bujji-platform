"""Tests — Engineering Series 67: historical intraday observation expansion."""
from bujji.mic_replay.observation_adapter import build_candle_payload
from bujji.replay.historical_session import HistoricalSessionRecord
from bujji.replay.option_chain_ingestion import build_session_records_from_bhavcopy, parse_bhavcopy_csv
from bujji.replay.schema_version import CURRENT_SCHEMA_VERSION, SCHEMA_VERSION_V3
from bujji.replay.validator import validate_session

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)
SAMPLE_ROW = (
    "2026-07-22,2026-07-22,FO,NSE,IDO,80310,,NIFTY,,2026-07-28,2026-07-28,24000.00,CE,"
    "NIFTY26JUL24000CE,100.0,110.0,95.0,105.0,105.0,98.0,23996.25,105.0,1000,100,50,5000.0,10,F1,75,,,,,"
)


def _csv_text():
    return "\n".join([HEADER, SAMPLE_ROW]) + "\n"


def _record(**kwargs):
    return HistoricalSessionRecord(
        session_id="S1", trading_date="2026-07-22", timestamp="2026-07-22T15:30:00", spot=23996.25, **kwargs
    )


# ---------------------------------------------------------------------------
# Schema investigation / extension
# ---------------------------------------------------------------------------


def test_schema_did_not_previously_support_ohlc_now_does():
    record = _record()
    assert record.open is None
    assert record.high is None
    assert record.low is None
    assert record.close is None


def test_schema_version_bumped_to_v3():
    assert CURRENT_SCHEMA_VERSION == SCHEMA_VERSION_V3


# ---------------------------------------------------------------------------
# Ingestion adapter: real OHLC pass-through, never fabricated
# ---------------------------------------------------------------------------


def test_ingestion_never_populates_ohlc_without_explicit_values():
    record, _ = build_session_records_from_bhavcopy(_csv_text(), "2026-07-22", underlying="NIFTY")
    assert record.open is None
    assert record.high is None
    assert record.low is None
    assert record.close is None


def test_ingestion_accepts_real_ohlc_pass_through():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(), "2026-07-22", underlying="NIFTY",
        open_price=24150.45, high_price=24166.3, low_price=23961.4, close_price=23996.25,
    )
    assert record.open == 24150.45
    assert record.high == 24166.3
    assert record.low == 23961.4
    assert record.close == 23996.25


def test_ingestion_never_computes_ohlc_from_bhavcopy_itself():
    rows = parse_bhavcopy_csv(_csv_text(), underlying="NIFTY")
    assert "OpnPric" not in {"open", "high", "low", "close"}  # sanity: bhavcopy has no underlying OHLC column used here
    record, _ = build_session_records_from_bhavcopy(_csv_text(), "2026-07-22", underlying="NIFTY")
    assert record.open is None  # never derived from the option contract's own OpnPric/HghPric/etc.


# ---------------------------------------------------------------------------
# Observation adapter: genuine range used when complete, honest fallback otherwise
# ---------------------------------------------------------------------------


def test_candle_uses_real_ohlc_when_all_four_present():
    record = _record(open=24150.45, high=24166.3, low=23961.4, close=23996.25)
    candle = build_candle_payload(record)
    assert candle["open"] == 24150.45
    assert candle["high"] == 24166.3
    assert candle["low"] == 23961.4
    assert candle["close"] == 23996.25
    assert not (candle["open"] == candle["high"] == candle["low"] == candle["close"])


def test_candle_falls_back_to_degenerate_when_any_ohlc_field_missing():
    record = _record(open=24150.45, high=24166.3, low=None, close=23996.25)
    candle = build_candle_payload(record)
    assert candle["open"] == candle["high"] == candle["low"] == candle["close"] == record.spot


def test_candle_falls_back_to_degenerate_when_no_ohlc_supplied():
    record = _record()
    candle = build_candle_payload(record)
    assert candle["open"] == candle["high"] == candle["low"] == candle["close"] == record.spot


def test_candle_never_partially_mixes_real_and_fabricated_ohlc():
    record = _record(open=24150.45, high=None, low=23961.4, close=23996.25)
    candle = build_candle_payload(record)
    # Must be all-degenerate, not a mix of the two real fields plus a guessed high.
    assert candle["high"] == record.spot
    assert candle["open"] == record.spot


# ---------------------------------------------------------------------------
# Backward compatibility / validator / replay compatibility
# ---------------------------------------------------------------------------


def test_legacy_record_construction_without_ohlc_kwargs_unaffected():
    record = HistoricalSessionRecord(
        session_id="S1", trading_date="2026-07-22", timestamp="2026-07-22T15:30:00", spot=23996.25,
        option_chain_entries=((24000.0, "CE", "2026-07-28", "NIFTY26JUL24000CE"),),
        option_chain_expiries=("2026-07-28",),
    )
    assert record.open is None


def test_validator_never_reads_ohlc_fields():
    import ast
    import inspect
    from bujji.replay import validator as validator_module

    tree = ast.parse(inspect.getsource(validator_module))
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (attrs & {"open", "high", "low", "close"})


def test_record_with_ohlc_still_passes_validator_unchanged():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(), "2026-07-22", underlying="NIFTY",
        open_price=24150.45, high_price=24166.3, low_price=23961.4, close_price=23996.25,
    )
    result = validate_session(record)
    assert result.valid is True
    assert result.issues == ()


def test_deterministic_candle_construction():
    record = _record(open=24150.45, high=24166.3, low=23961.4, close=23996.25)
    a = build_candle_payload(record)
    b = build_candle_payload(record)
    assert a == b
