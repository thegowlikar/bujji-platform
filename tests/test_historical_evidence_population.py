"""Tests — Data Acquisition Sprint C: historical evidence population."""
from bujji.replay.option_chain_ingestion import (
    build_session_record,
    build_session_records_from_bhavcopy,
    parse_bhavcopy_csv,
)
from bujji.replay.validator import validate_session
from bujji.replay.corpus_builder import build_corpus, compute_checksum

HEADER = (
    "TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
    "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
    "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
    "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4"
)

SAMPLE_ROWS = [
    "2026-07-22,2026-07-22,FO,NSE,IDO,80310,,NIFTY,,2026-07-28,2026-07-28,24000.00,CE,"
    "NIFTY26JUL24000CE,100.0,110.0,95.0,105.0,105.0,98.0,23996.25,105.0,1000,100,50,5000.0,10,F1,75,,,,,",
    "2026-07-22,2026-07-22,FO,NSE,IDO,80311,,NIFTY,,2026-07-28,2026-07-28,24000.00,PE,"
    "NIFTY26JUL24000PE,90.0,95.0,80.0,85.0,85.0,88.0,23996.25,85.0,900,80,40,4000.0,8,F1,75,,,,,",
]

SAMPLE_ROWS_MISSING_OI = [
    "2026-07-22,2026-07-22,FO,NSE,IDO,80312,,NIFTY,,2026-07-28,2026-07-28,24050.00,CE,"
    "NIFTY26JUL24050CE,100.0,110.0,95.0,105.0,105.0,98.0,23996.25,105.0,,,50,5000.0,10,F1,75,,,,,",
]


def _csv_text(rows):
    return "\n".join([HEADER] + rows) + "\n"


# ---------------------------------------------------------------------------
# OI extraction
# ---------------------------------------------------------------------------


def test_oi_extracted_from_real_shaped_bhavcopy_rows():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    assert len(record.option_chain_liquidity) == 2
    ce = next(l for l in record.option_chain_liquidity if l.option_type == "CE")
    assert ce.open_interest == 1000


def test_change_in_oi_extracted():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    ce = next(l for l in record.option_chain_liquidity if l.option_type == "CE")
    assert ce.change_in_open_interest == 100


def test_oi_liquidity_never_carries_bid_ask():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    assert all(l.bid is None and l.ask is None for l in record.option_chain_liquidity)


def test_session_metadata_extracted_from_bhavcopy():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    assert record.session_exchange == "NSE"
    assert record.session_segment == "FO"


# ---------------------------------------------------------------------------
# India VIX extraction (pass-through, not sourced from bhavcopy)
# ---------------------------------------------------------------------------


def test_vix_attached_when_supplied_by_caller():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    assert record.vix == 13.29
    assert record.vix_as_of == "2026-07-22T15:30:00"


def test_vix_never_fetched_or_fabricated_by_this_module():
    record, _ = build_session_records_from_bhavcopy(_csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY")
    assert record.vix is None
    assert record.vix_as_of is None


def test_ingestion_module_never_imports_a_vix_data_source():
    import ast
    import inspect
    from bujji.replay import option_chain_ingestion as module

    tree = ast.parse(inspect.getsource(module))
    imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
    for node in imports:
        module_name = getattr(node, "module", None) or ",".join(a.name for a in node.names)
        assert "fyers" not in (module_name or "").lower()


# ---------------------------------------------------------------------------
# Missing-value handling
# ---------------------------------------------------------------------------


def test_missing_oi_represented_as_none_not_zero():
    rows = parse_bhavcopy_csv(_csv_text(SAMPLE_ROWS_MISSING_OI), underlying="NIFTY")
    record = build_session_record("2026-07-22", rows, underlying="NIFTY")
    assert record.option_chain_liquidity[0].open_interest is None
    assert record.option_chain_liquidity[0].open_interest != 0


def test_missing_vix_never_defaults_to_zero():
    record, _ = build_session_records_from_bhavcopy(_csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=None)
    assert record.vix is None


# ---------------------------------------------------------------------------
# Backward / validator / manifest / replay compatibility
# ---------------------------------------------------------------------------


def test_legacy_callers_without_vix_kwargs_still_work():
    record, matched = build_session_records_from_bhavcopy(_csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY")
    assert record is not None
    assert matched == 2


def test_populated_record_still_passes_validator_unchanged():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    result = validate_session(record)
    assert result.valid is True
    assert result.issues == ()


def test_checksum_unaffected_by_new_evidence_fields():
    without_evidence, _ = build_session_records_from_bhavcopy(_csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY")
    with_evidence, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    # OI is populated in both regardless of the vix kwarg (it always
    # comes from the same bhavcopy rows) -- only vix/vix_as_of differ
    # between these two records, and compute_checksum must not read
    # either Series 64 field.
    assert compute_checksum([without_evidence]) == compute_checksum([with_evidence])


def test_build_corpus_succeeds_with_populated_records():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    result = build_corpus([record], source_description="test")
    assert len(result.scenarios) == 1
    assert result.validation_report.valid_sessions == 1


# ---------------------------------------------------------------------------
# Deterministic serialization / no behavioral regression
# ---------------------------------------------------------------------------


def test_deterministic_population_given_same_rows():
    a, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    b, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    assert a == b


def test_scenario_construction_never_receives_new_evidence_fields():
    record, _ = build_session_records_from_bhavcopy(
        _csv_text(SAMPLE_ROWS), "2026-07-22", underlying="NIFTY", vix=13.29, vix_as_of="2026-07-22T15:30:00"
    )
    result = build_corpus([record], source_description="test")
    scenario = result.scenarios[0]
    assert not hasattr(scenario, "vix")
    assert not hasattr(scenario, "option_chain_liquidity")
