"""Phase 17I.2 — Live Futures Microstructure Reality Activation tests.

Covers: certification isolation (new access_method, no collision),
payload normalization (real field mapping, no forbidden derived
fields), identity correctness (NIFTY_FUT_CONTINUOUS + source_symbol),
duplicate handling, conflict-class behavior, and restart survival.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from bujji.market_reality import taxonomy as reality_taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import CertificationGate, StaticCertificationGate
from bujji.market_reality.store import RawObservationStore


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, _REPO_ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


poller = _load("run_futures_depth_poller", "run_futures_depth_poller.py")
depth_cert = _load("certify_fyers_futures_depth_access", "certify_fyers_futures_depth_access.py")


# Real live discovery payload shape, from data_certification/fyers_depth_discovery_20260813.json.
REAL_RAW_DEPTH_ROW = {
    "totalbuyqty": 266760, "totalsellqty": 318435,
    "ask": [
        {"price": 24428.5, "volume": 65, "ord": 1},
        {"price": 24428.6, "volume": 390, "ord": 3},
        {"price": 24428.9, "volume": 260, "ord": 1},
        {"price": 24429.0, "volume": 975, "ord": 4},
        {"price": 24429.1, "volume": 845, "ord": 4},
    ],
    "bids": [
        {"price": 24416.1, "volume": 195, "ord": 3},
        {"price": 24416.0, "volume": 195, "ord": 3},
        {"price": 24415.0, "volume": 1040, "ord": 12},
        {"price": 24413.9, "volume": 1495, "ord": 2},
        {"price": 24413.8, "volume": 325, "ord": 2},
    ],
    "o": 24460.5, "h": 24460.5, "l": 24415, "c": 24470.5, "chp": -0.22,
    "tick_Size": 0.1, "ch": -54.3, "ltq": 65, "ltt": 1786593123, "ltp": 24416.2,
    "v": 103415, "atp": 24431.29, "lower_ckt": 22023.5, "upper_ckt": 26917.5,
    "expiry": "1787652600", "oi": 12587900, "oiflag": True, "pdoi": 12562800, "oipercent": 0.2,
}


# --- Certification isolation ------------------------------------------------------
def test_depth_access_method_is_distinct_from_live_quote_and_historical():
    assert poller.ACCESS_METHOD == "direct_sdk_fyers_broker_py_depth"
    assert poller.ACCESS_METHOD != "direct_sdk_fyers_broker_py"
    assert poller.ACCESS_METHOD != "direct_sdk_fyers_historical_rest"
    assert poller.ACCESS_METHOD != "direct_sdk_fyers_historical_intraday_rest"


def test_depth_cert_script_uses_the_same_new_access_method():
    assert depth_cert.ACCESS_METHOD == poller.ACCESS_METHOD


def test_depth_cert_artifact_name_is_distinct_from_live_quote_cert():
    assert depth_cert.ARTIFACT_NAME != "fyers_nifty_future_certification"
    assert "depth" in depth_cert.ARTIFACT_NAME


def test_certifying_only_live_quote_does_not_certify_depth():
    """The exact collision this phase exists to prevent: a live-quote-only
    certification must NOT make depth writes appear certified."""
    with tempfile.TemporaryDirectory() as d:
        cert_dir = Path(d)
        (cert_dir / "fyers_nifty_future_certification_20260813.json").write_text(
            '{"timestamp": "2026-08-13T09:00:00+05:30", "access_method": '
            '"direct_sdk_fyers_broker_py", "instrument": "NIFTY_FUTURES", '
            '"validation_result": "CERTIFIED_AVAILABLE"}'
        )
        gate = CertificationGate(str(cert_dir))
        quote_status, _ = gate.status_for("direct_sdk_fyers_broker_py", reality_taxonomy.INSTRUMENT_FUTURE)
        depth_status, _ = gate.status_for(poller.ACCESS_METHOD, reality_taxonomy.INSTRUMENT_FUTURE)
        assert quote_status == reality_taxonomy.CERTIFIED_AVAILABLE
        assert depth_status == reality_taxonomy.CERTIFICATION_MISSING


def test_certifying_depth_specifically_certifies_only_depth():
    with tempfile.TemporaryDirectory() as d:
        cert_dir = Path(d)
        (cert_dir / f"{depth_cert.ARTIFACT_NAME}_20260813.json").write_text(
            f'{{"timestamp": "2026-08-13T09:00:00+05:30", "access_method": '
            f'"{poller.ACCESS_METHOD}", "instrument": "NIFTY_FUTURES", '
            f'"validation_result": "CERTIFIED_AVAILABLE"}}'
        )
        gate = CertificationGate(str(cert_dir))
        depth_status, _ = gate.status_for(poller.ACCESS_METHOD, reality_taxonomy.INSTRUMENT_FUTURE)
        quote_status, _ = gate.status_for("direct_sdk_fyers_broker_py", reality_taxonomy.INSTRUMENT_FUTURE)
        assert depth_status == reality_taxonomy.CERTIFIED_AVAILABLE
        assert quote_status == reality_taxonomy.CERTIFICATION_MISSING


# --- Payload normalization ---------------------------------------------------------
def test_normalize_depth_payload_maps_real_fields():
    payload = poller._normalize_depth_payload(REAL_RAW_DEPTH_ROW)
    assert payload is not None
    assert payload["bids"][0] == {"price": 24416.1, "volume": 195, "order_count": 3}
    assert payload["asks"][0] == {"price": 24428.5, "volume": 65, "order_count": 1}
    assert len(payload["bids"]) == 5
    assert len(payload["asks"]) == 5
    assert payload["open_interest"] == 12587900
    assert payload["prior_day_open_interest"] == 12562800
    assert payload["oi_change_flag"] is True
    assert payload["oi_change_percent"] == 0.2
    assert payload["total_buy_quantity"] == 266760
    assert payload["total_sell_quantity"] == 318435
    assert payload["last_price"] == 24416.2


def test_normalize_depth_payload_satisfies_required_payload_fields():
    payload = poller._normalize_depth_payload(REAL_RAW_DEPTH_ROW)
    required = reality_taxonomy.REQUIRED_PAYLOAD_FIELDS["MARKET_DEPTH"]
    for key in required:
        assert key in payload


def test_normalize_depth_payload_contains_no_forbidden_derived_field():
    payload = poller._normalize_depth_payload(REAL_RAW_DEPTH_ROW)
    for key in payload:
        assert str(key).lower() not in reality_taxonomy.FORBIDDEN_PAYLOAD_FIELDS
    # Explicitly assert the restricted concepts from the phase spec are absent.
    for forbidden_concept in ("imbalance", "liquidity_score", "pressure", "sentiment", "signal"):
        assert forbidden_concept not in payload


def test_normalize_depth_payload_returns_none_on_missing_required_raw_field():
    broken = {k: v for k, v in REAL_RAW_DEPTH_ROW.items() if k != "oi"}
    assert poller._normalize_depth_payload(broken) is None


def test_discovery_mode_never_normalizes():
    """FIELD_MAPPING_VERIFIED is now True (activation complete), but
    DISCOVERY-mode's own code path (_poll_once with live=False) must
    still never call the store -- covered structurally by _poll_once
    returning early on `not live`."""
    import inspect
    src = inspect.getsource(poller._poll_once)
    assert "if not live" in src


# --- Identity correctness -----------------------------------------------------------
def test_instrument_identity_is_never_the_literal_contract_symbol():
    assert poller.INSTRUMENT_IDENTITY == "NIFTY_FUT_CONTINUOUS"
    assert poller.INSTRUMENT_IDENTITY != "NSE:NIFTY26AUGFUT"


def _build_depth_observation(*, source_symbol="NSE:NIFTY26AUGFUT", expiry="2026-08-27",
                              payload=None, capture_ts="2026-08-13T09:20:00+05:30",
                              cert_status="CERTIFIED_AVAILABLE"):
    payload = payload or poller._normalize_depth_payload(REAL_RAW_DEPTH_ROW)
    return build_raw_observation(
        kind="MARKET_DEPTH", instrument=poller.INSTRUMENT_IDENTITY,
        instrument_type=poller.INSTRUMENT_TYPE, payload=payload,
        source=poller.SOURCE, access_method=poller.ACCESS_METHOD,
        capture_timestamp=capture_ts,
        identity_fields={"expiry": expiry, "source_symbol": source_symbol},
        certification_status=cert_status, certification_ref="test-ref",
    )


def test_built_observation_carries_continuous_identity_and_source_symbol():
    raw = _build_depth_observation()
    assert raw.instrument == "NIFTY_FUT_CONTINUOUS"
    assert raw.identity_fields["source_symbol"] == "NSE:NIFTY26AUGFUT"
    assert raw.identity_fields["expiry"] == "2026-08-27"


def test_identity_survives_contract_rollover_unchanged():
    """Two depth observations at different contract expiries (a
    rollover) must still share the same instrument identity -- the
    same continuity principle already established for Historical
    Reality futures (17H.3)."""
    before_rollover = _build_depth_observation(source_symbol="NSE:NIFTY26AUGFUT", expiry="2026-08-27")
    after_rollover = _build_depth_observation(source_symbol="NSE:NIFTY26SEPFUT", expiry="2026-09-24",
                                               capture_ts="2026-09-01T09:20:00+05:30")
    assert before_rollover.instrument == after_rollover.instrument == "NIFTY_FUT_CONTINUOUS"
    assert before_rollover.identity_fields["source_symbol"] != after_rollover.identity_fields["source_symbol"]


# --- Duplicate handling --------------------------------------------------------------
def test_identical_depth_observation_is_a_duplicate_no_op():
    with tempfile.TemporaryDirectory() as d:
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        store = RawObservationStore(d, gate, session_id="test")
        raw = _build_depth_observation()
        r1 = store.append(raw, now="2026-08-13T09:20:01+05:30")
        r2 = store.append(raw, now="2026-08-13T09:20:02+05:30")
        assert r1.outcome == reality_taxonomy.OUTCOME_ACCEPTED
        assert r2.outcome == reality_taxonomy.OUTCOME_DUPLICATE
        assert len(store) == 1


# --- Conflict-class behavior (content-hash identity, not a natural-key store) ------
def test_differing_depth_content_at_same_timestamp_gets_distinct_ids_not_a_conflict_error():
    """RawObservation identity is a content hash over identity+value
    (unlike HistoricalObservation's natural-key store) -- two genuinely
    different depth snapshots at the same capture instant are simply
    two distinct, both-accepted observations, never a raised conflict.
    This is expected behavior for this store, verified explicitly."""
    with tempfile.TemporaryDirectory() as d:
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        store = RawObservationStore(d, gate, session_id="test")
        payload_a = poller._normalize_depth_payload(REAL_RAW_DEPTH_ROW)
        different_row = dict(REAL_RAW_DEPTH_ROW, ltp=99999.0, oi=1)
        payload_b = poller._normalize_depth_payload(different_row)

        raw_a = _build_depth_observation(payload=payload_a, capture_ts="2026-08-13T09:20:00+05:30")
        raw_b = _build_depth_observation(payload=payload_b, capture_ts="2026-08-13T09:20:00+05:30")
        assert raw_a.observation_id != raw_b.observation_id

        r1 = store.append(raw_a, now="2026-08-13T09:20:01+05:30")
        r2 = store.append(raw_b, now="2026-08-13T09:20:02+05:30")
        assert r1.outcome == reality_taxonomy.OUTCOME_ACCEPTED
        assert r2.outcome == reality_taxonomy.OUTCOME_ACCEPTED
        assert len(store) == 2


def test_uncertified_depth_write_is_rejected_not_silently_accepted():
    with tempfile.TemporaryDirectory() as d:
        gate = StaticCertificationGate(reality_taxonomy.CERTIFICATION_MISSING)
        store = RawObservationStore(d, gate, session_id="test")
        raw = _build_depth_observation()
        result = store.append(raw, now="2026-08-13T09:20:01+05:30")
        assert result.outcome == reality_taxonomy.OUTCOME_REJECTED
        assert len(store) == 0


# --- Restart survival ------------------------------------------------------------------
def test_duplicate_detection_survives_a_simulated_restart():
    with tempfile.TemporaryDirectory() as d:
        gate = StaticCertificationGate(reality_taxonomy.CERTIFIED_AVAILABLE)
        raw = _build_depth_observation()

        store_1 = RawObservationStore(d, gate, session_id="test")
        r1 = store_1.append(raw, now="2026-08-13T09:20:01+05:30")
        assert r1.outcome == reality_taxonomy.OUTCOME_ACCEPTED

        # Simulate a process restart: a brand-new store instance over the
        # same directory must rebuild its seen-ids set from the accepted
        # log and correctly recognize the same observation as a duplicate.
        store_2 = RawObservationStore(d, gate, session_id="test")
        assert store_2.holds(raw.observation_id)
        r2 = store_2.append(raw, now="2026-08-13T09:25:01+05:30")
        assert r2.outcome == reality_taxonomy.OUTCOME_DUPLICATE
        assert len(store_2) == 1
