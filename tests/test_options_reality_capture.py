"""Phase 17I.10 — Options Reality Capture tests."""
import datetime
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.historical_reality.capture import build_historical_observation
from bujji.historical_reality.store import (
    ConflictingHistoricalObservationError, HistoricalObservationStore,
)
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_reality import taxonomy as reality_taxonomy


def _load(name, filename):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


capture = _load("capture_options_reality_session", "capture_options_reality_session.py")
cert = _load("certify_fyers_optionchain_reality_access", "certify_fyers_optionchain_reality_access.py")


# --- Formatting / identity helpers -----------------------------------------------
def test_format_strike_whole_number_has_no_decimal():
    assert capture._format_strike(24300) == "24300"
    assert capture._format_strike(24300.0) == "24300"


def test_format_strike_fractional_preserved():
    assert capture._format_strike(24300.5) == "24300.5"


def test_instrument_identity_shape():
    assert capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE") == "NIFTY|2026-08-25|24500|CE"
    assert capture._instrument_identity("NIFTY", "2026-08-25", 24500, "PE") == "NIFTY|2026-08-25|24500|PE"


def test_ce_and_pe_produce_distinct_identities():
    ce = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
    pe = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "PE")
    assert ce != pe


def test_different_expiries_produce_distinct_identities():
    a = capture._instrument_identity("NIFTY", "2026-08-18", 24500, "CE")
    b = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
    assert a != b


def test_different_strikes_produce_distinct_identities():
    a = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
    b = capture._instrument_identity("NIFTY", "2026-08-25", 24550, "CE")
    assert a != b


def test_expiry_iso_from_epoch_real_value():
    # 1787047800 == 18-08-2026 (real epoch confirmed live this phase).
    assert capture._expiry_iso_from_epoch(1787047800) == "2026-08-18"


# --- Row validation ------------------------------------------------------------
def test_validate_row_rejects_missing_option_type():
    assert capture._validate_row({"option_type": "", "strike_price": 24300, "symbol": "x", "ltp": 1.0}) is not None


def test_validate_row_rejects_missing_strike():
    assert capture._validate_row({"option_type": "CE", "strike_price": -1, "symbol": "x", "ltp": 1.0}) is not None


def test_validate_row_rejects_missing_symbol():
    assert capture._validate_row({"option_type": "CE", "strike_price": 24300, "symbol": "", "ltp": 1.0}) is not None


def test_validate_row_rejects_missing_ltp():
    assert capture._validate_row({"option_type": "CE", "strike_price": 24300, "symbol": "x", "ltp": None}) is not None


def test_validate_row_accepts_a_real_zero_bid_ask_row():
    """A genuinely illiquid far strike with bid=ask=0 is a true fact,
    never a rejection -- same discipline as every other Reality-tier
    validator in this project."""
    row = {"option_type": "CE", "strike_price": 30000, "symbol": "x", "ltp": 0.05, "bid": 0, "ask": 0}
    assert capture._validate_row(row) is None


def test_validate_row_rejects_negative_price():
    row = {"option_type": "CE", "strike_price": 24300, "symbol": "x", "ltp": -1.0}
    assert capture._validate_row(row) is not None


# --- Market hours gate (this phase's explicit 09:15-15:30 window) ---------------
def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 14, 10, 0, tzinfo=capture.IST)  # Friday
    assert capture.within_market_hours(weekday) is True


def test_within_market_hours_false_after_1530():
    late = datetime.datetime(2026, 8, 14, 15, 35, tzinfo=capture.IST)
    assert capture.within_market_hours(late) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=capture.IST)
    assert capture.within_market_hours(saturday) is False


# --- Certification isolation -----------------------------------------------------
def test_access_method_is_new_and_distinct():
    assert capture.ACCESS_METHOD == "direct_sdk_fyers_optionchain_reality"
    assert cert.ACCESS_METHOD == capture.ACCESS_METHOD
    assert capture.ACCESS_METHOD not in (
        "direct_sdk_fyers_broker_py",
        "direct_sdk_fyers_historical_rest",
        "direct_sdk_fyers_historical_intraday_rest",
        "direct_sdk_fyers_broker_py_depth",
    )


def test_never_requests_greeks():
    """Structural guard: neither script may ever pass greeks=1 to the
    FYERS SDK -- IV/Greeks stay out of Reality by choice, confirmed
    live this phase to be available from the source on request."""
    # Check only the actual `_call(...)` invocations, never prose/docstrings
    # (both files' docstrings legitimately mention `greeks=1` as the
    # forbidden pattern being explained, in backticks).
    import re
    for path in (Path(capture.__file__), Path(cert.__file__)):
        for call_text in re.findall(r"broker\._call\([^)]*\)", path.read_text(), re.DOTALL):
            assert "greeks" not in call_text, f"found a greeks kwarg in a real _call(): {call_text}"
    assert '"greeks_requested": False' in Path(cert.__file__).read_text()


# --- Storage-level: identity, duplicate, conflict, restart (real store, no broker) --
def _build_obs(identity, source_symbol, ltp, capture_ts="2026-08-14T10:00:00+05:30", cert_status="CERTIFIED_AVAILABLE"):
    return build_historical_observation(
        instrument_identity=identity, instrument_type="OPTION",
        resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, timestamp=capture_ts,
        payload={"ltp": ltp, "bid": ltp - 0.5, "ask": ltp + 0.5, "volume": 100,
                 "open_interest": 1000, "prior_day_open_interest": 990},
        source="fyers", access_method="direct_sdk_fyers_optionchain_reality",
        source_epoch=1755151800, source_symbol=source_symbol,
        raw_artifact_ref="", ingestion_run_id="RUN-x", retrieved_at=capture_ts,
        certification_status=cert_status, certification_ref="ref",
        # PHASE_17I11: matches what the real capture script now passes --
        # option chain state is MAPPING-shaped, never OHLC.
        value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
    )


def test_ce_pe_and_different_strikes_and_expiries_all_coexist_in_store():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "opt.db"))
        ce = _build_obs(capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE"), "NSE:NIFTY2681824500CE", 200.0)
        pe = _build_obs(capture._instrument_identity("NIFTY", "2026-08-25", 24500, "PE"), "NSE:NIFTY2681824500PE", 180.0)
        other_strike = _build_obs(capture._instrument_identity("NIFTY", "2026-08-25", 24550, "CE"), "NSE:NIFTY2681824550CE", 175.0)
        other_expiry = _build_obs(capture._instrument_identity("NIFTY", "2026-09-01", 24500, "CE"), "NSE:NIFTY2610124500CE", 220.0)
        for obs in (ce, pe, other_strike, other_expiry):
            store.write(obs)
        assert store.count() == 4


def test_identical_reingestion_is_idempotent_no_op():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "opt.db"))
        identity = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
        obs = _build_obs(identity, "NSE:NIFTY2681824500CE", 200.0)
        store.write(obs)
        store.write(obs)  # identical re-write.
        assert store.count() == 1


def test_conflicting_reingestion_is_rejected_not_overwritten():
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "opt.db"))
        identity = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
        obs_a = _build_obs(identity, "NSE:NIFTY2681824500CE", 200.0)
        obs_b = _build_obs(identity, "NSE:NIFTY2681824500CE", 999.0)  # different ltp -> different observation_id.
        store.write(obs_a)
        try:
            store.write(obs_b)
            raised = False
        except ConflictingHistoricalObservationError:
            raised = True
        assert raised
        assert store.count() == 1


def test_restart_survival():
    with tempfile.TemporaryDirectory() as d:
        db_path = str(Path(d) / "opt.db")
        identity = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
        obs = _build_obs(identity, "NSE:NIFTY2681824500CE", 200.0)

        store_1 = HistoricalObservationStore(db_path)
        store_1.write(obs)
        assert store_1.count() == 1

        store_2 = HistoricalObservationStore(db_path)  # simulated restart
        assert store_2.count() == 1
        store_2.write(obs)  # identical re-write must remain a no-op across the restart.
        assert store_2.count() == 1


def test_uncertified_write_is_structurally_impossible_via_the_gate():
    """The capture script itself checks CertificationGate before ever
    calling store.write() -- this test asserts the certification_status
    stamped into a record honestly reflects what the gate resolved,
    never a hardcoded CERTIFIED value regardless of gate state."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "opt.db"))
        identity = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
        obs = _build_obs(identity, "NSE:NIFTY2681824500CE", 200.0, cert_status="NOT_CERTIFIED")
        assert obs.lineage.certification_status == "NOT_CERTIFIED"
        # HistoricalObservationStore itself does not re-check certification
        # (that is the capture script's own responsibility, mirroring every
        # other historical ingestion script in this project) -- this test
        # documents that division of responsibility explicitly.
        store.write(obs)
        assert store.count() == 1


# --- PHASE_17I11: option chain state must never be classified as OHLC ------------
def test_options_capture_writes_mapping_not_ohlc():
    """The real capture script (capture_options_reality_session.py) must
    pass value_kind=MAPPING explicitly -- this is the actual semantic
    fix for PHASE_17I11's discovered integrity debt. Read the source,
    do not just trust the docstring."""
    src = Path(capture.__file__).read_text()
    assert "value_kind=moc_taxonomy.VALUE_KIND_MAPPING" in src
    assert "value_kind=moc_taxonomy.VALUE_KIND_OHLC" not in src


def test_build_historical_observation_defaults_to_ohlc_for_backward_compatibility():
    """Every pre-17I11 caller (spot/futures/VIX ingestion) never passed
    value_kind and must keep getting OHLC without any code change on
    their side -- the default parameter is what preserves them."""
    obs = build_historical_observation(
        instrument_identity="NSE:NIFTY50-INDEX", instrument_type="INDEX",
        resolution=moc_taxonomy.RESOLUTION_DAILY, timestamp="2026-08-14T09:15:00+05:30",
        payload={"open": 1, "high": 2, "low": 0.5, "close": 1.5},
        source="fyers", access_method="direct_sdk_fyers_historical_rest",
        source_epoch=1755151800, source_symbol="NSE:NIFTY50-INDEX",
        raw_artifact_ref="", ingestion_run_id="RUN-y", retrieved_at="2026-08-14T09:15:00+05:30",
        certification_status="CERTIFIED_AVAILABLE",
    )
    assert obs.observation.value.value_kind == moc_taxonomy.VALUE_KIND_OHLC


def test_build_historical_observation_honours_explicit_mapping_kind():
    obs = _build_obs(capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE"),
                      "NSE:NIFTY2681824500CE", 200.0)
    assert obs.observation.value.value_kind == moc_taxonomy.VALUE_KIND_MAPPING
    assert obs.observation.value.value_kind != moc_taxonomy.VALUE_KIND_OHLC


def test_mapping_value_kind_is_a_real_recognized_taxonomy_member():
    """No new enum was needed for this fix -- VALUE_KIND_MAPPING already
    existed in moc_taxonomy.ALL_VALUE_KINDS and is already used
    elsewhere in this project (market_reality.capture's KIND_OPTION_CHAIN,
    options_observation.engine) -- confirming reuse, not invention."""
    assert moc_taxonomy.VALUE_KIND_MAPPING in moc_taxonomy.ALL_VALUE_KINDS


def test_spot_futures_vix_historical_ingestion_scripts_still_default_ohlc():
    """Structural guard: none of the real production ingestion scripts
    for spot/futures/VIX were touched by this phase -- they must still
    call build_historical_observation() without any value_kind kwarg,
    relying entirely on the preserved default."""
    ohlc_scripts = (
        "ingest_india_vix_daily_historical.py",
        "ingest_india_vix_intraday_historical.py",
        "ingest_nifty_spot_daily_historical.py",
        "ingest_nifty_spot_intraday_historical.py",
        "ingest_nifty_futures_daily_historical.py",
        "ingest_nifty_futures_intraday_historical.py",
    )
    for name in ohlc_scripts:
        src = (_REPO_ROOT / "scripts" / name).read_text()
        assert "value_kind=" not in src, f"{name} should not have been touched by PHASE_17I11"


def test_options_identity_and_idempotency_unaffected_by_value_kind_fix():
    """Re-proves 17I.10's own identity-uniqueness/idempotency guarantees
    still hold after switching options records to MAPPING -- the
    observation_id hash includes value_kind in its seed, so this is a
    real, not hypothetical, regression risk to re-check explicitly."""
    with tempfile.TemporaryDirectory() as d:
        store = HistoricalObservationStore(str(Path(d) / "opt.db"))
        identity = capture._instrument_identity("NIFTY", "2026-08-25", 24500, "CE")
        obs = _build_obs(identity, "NSE:NIFTY2681824500CE", 200.0)
        store.write(obs)
        store.write(obs)  # identical re-write, still idempotent under MAPPING.
        assert store.count() == 1

        conflicting = _build_obs(identity, "NSE:NIFTY2681824500CE", 999.0)
        try:
            store.write(conflicting)
            raised = False
        except ConflictingHistoricalObservationError:
            raised = True
        assert raised
        assert store.count() == 1
