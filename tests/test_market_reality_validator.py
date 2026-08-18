"""Phase 17E — Layer 0 observation validator.

Covers the five pre-persistence checks (symbol identity, timestamp
ordering, schema validity, certification status, lineage completeness).
Duplicate detection is a store concern and is covered in
test_market_reality_store.py.
"""
from bujji.market_reality import taxonomy, validator
from bujji.market_reality.capture import build_raw_observation

CERT = taxonomy.CERTIFIED_AVAILABLE
NOW = "2026-08-12T09:30:00+00:00"


def _obs(**overrides):
    kwargs = dict(
        kind=taxonomy.KIND_QUOTE,
        instrument="NSE:NIFTY50-INDEX",
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": 24325.8},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp="2026-08-12T09:20:00+00:00",
        event_timestamp="2026-08-12T09:19:59+00:00",
        certification_status=CERT,
        identity_fields={},
    )
    kwargs.update(overrides)
    return build_raw_observation(**kwargs)


def _reasons(raw, status=CERT, now=NOW):
    return validator.validate(raw, certification_status=status, now=now).reasons


# --- 1. Symbol identity ---------------------------------------------------
def test_valid_spot_observation_passes_every_check():
    assert validator.validate(_obs(), certification_status=CERT, now=NOW).is_valid


def test_unknown_observation_kind_rejected():
    assert taxonomy.REJECT_UNKNOWN_KIND in _reasons(_obs(kind="TELEPATHY"))


def test_unknown_instrument_type_rejected():
    assert taxonomy.REJECT_UNKNOWN_INSTRUMENT_TYPE in _reasons(
        _obs(instrument_type="WARRANT")
    )


def test_missing_instrument_rejected():
    assert taxonomy.REJECT_MISSING_INSTRUMENT in _reasons(_obs(instrument=""))


def test_future_without_expiry_is_not_identifiable():
    reasons = _reasons(
        _obs(
            instrument="NSE:NIFTY26AUGFUT",
            instrument_type=taxonomy.INSTRUMENT_FUTURE,
            identity_fields={},
        )
    )
    assert f"{taxonomy.REJECT_MISSING_IDENTITY_FIELD}:expiry" in reasons


def test_option_requires_expiry_strike_and_right():
    reasons = _reasons(
        _obs(
            instrument="NSE:NIFTY2681824350CE",
            instrument_type=taxonomy.INSTRUMENT_OPTION,
            identity_fields={"expiry": "2026-08-18"},
        )
    )
    assert f"{taxonomy.REJECT_MISSING_IDENTITY_FIELD}:strike" in reasons
    assert f"{taxonomy.REJECT_MISSING_IDENTITY_FIELD}:option_type" in reasons


def test_fully_identified_option_passes():
    raw = _obs(
        instrument="NSE:NIFTY2681824350CE",
        instrument_type=taxonomy.INSTRUMENT_OPTION,
        identity_fields={"expiry": "2026-08-18", "strike": 24350, "option_type": "CE"},
    )
    assert validator.validate(raw, certification_status=CERT, now=NOW).is_valid


def test_bogus_option_right_rejected():
    raw = _obs(
        instrument="NSE:X",
        instrument_type=taxonomy.INSTRUMENT_OPTION,
        identity_fields={"expiry": "2026-08-18", "strike": 24350, "option_type": "XX"},
    )
    assert taxonomy.REJECT_UNKNOWN_OPTION_TYPE in _reasons(raw)


# --- 2. Timestamp ordering ------------------------------------------------
def test_absent_event_timestamp_is_legal():
    """A bare LTP poll publishes no exchange event time. That is a real
    condition, not a defect -- it must not be rejected, and must never be
    silently backfilled from capture time."""
    raw = _obs(event_timestamp=None)
    assert validator.validate(raw, certification_status=CERT, now=NOW).is_valid
    assert raw.lineage.event_timestamp is None


def test_malformed_event_timestamp_rejected():
    assert taxonomy.REJECT_MALFORMED_EVENT_TIMESTAMP in _reasons(
        _obs(event_timestamp="yesterday-ish")
    )


def test_malformed_capture_timestamp_rejected():
    assert taxonomy.REJECT_MALFORMED_CAPTURE_TIMESTAMP in _reasons(
        _obs(capture_timestamp="not-a-time")
    )


def test_missing_capture_timestamp_rejected():
    assert taxonomy.REJECT_MISSING_CAPTURE_TIMESTAMP in _reasons(
        _obs(capture_timestamp="")
    )


def test_event_after_capture_rejected():
    """An event cannot post-date its own capture; that ordering is
    physically impossible and signals a corrupted feed."""
    raw = _obs(
        capture_timestamp="2026-08-12T09:20:00+00:00",
        event_timestamp="2026-08-12T09:25:00+00:00",
    )
    assert taxonomy.REJECT_EVENT_AFTER_CAPTURE in _reasons(raw)


def test_capture_in_future_rejected():
    raw = _obs(capture_timestamp="2026-08-12T10:00:00+00:00", event_timestamp=None)
    assert taxonomy.REJECT_CAPTURE_IN_FUTURE in _reasons(raw, now=NOW)


# --- 3. Schema validity ---------------------------------------------------
def test_missing_required_payload_field_rejected():
    assert f"{taxonomy.REJECT_MISSING_REQUIRED_FIELD}:ltp" in _reasons(
        _obs(payload={"volume": 100})
    )


def test_depth_requires_both_sides_of_the_book():
    reasons = _reasons(
        _obs(kind=taxonomy.KIND_MARKET_DEPTH, payload={"bids": [{"price": 1.0}]})
    )
    assert f"{taxonomy.REJECT_MISSING_REQUIRED_FIELD}:asks" in reasons


def test_zero_bid_ask_is_a_real_fact_not_a_missing_field():
    """Regression guard for the 2026-08-12 investigation: a far-OTM
    option legitimately quotes bid=0/ask=0. That is true market
    information and must be STORED, never rejected as absent. Absent and
    zero are different things."""
    raw = _obs(payload={"ltp": 0.05, "bid": 0, "ask": 0, "volume": 0})
    assert validator.validate(raw, certification_status=CERT, now=NOW).is_valid


def test_forbidden_derived_fields_rejected():
    """Layer 0 stores observations, never computations. This is enforced
    at runtime, not merely by code review."""
    for field in ("iv", "delta", "vwap", "regime", "signal"):
        reasons = _reasons(_obs(payload={"ltp": 100.0, field: 1.23}))
        assert f"{taxonomy.REJECT_FORBIDDEN_DERIVED_FIELD}:{field}" in reasons


def test_forbidden_field_detection_is_case_insensitive():
    assert any(
        r.startswith(taxonomy.REJECT_FORBIDDEN_DERIVED_FIELD)
        for r in _reasons(_obs(payload={"ltp": 100.0, "IV": 0.2}))
    )


def test_unrecognized_schema_version_rejected():
    assert taxonomy.REJECT_UNRECOGNIZED_SCHEMA_VERSION in _reasons(
        _obs(schema_version="9.9.9")
    )


def test_empty_payload_rejected():
    assert taxonomy.REJECT_EMPTY_PAYLOAD in _reasons(_obs(payload=None))


# --- 4. Certification -----------------------------------------------------
def test_only_certified_available_permits_a_write():
    for status in (
        taxonomy.PARTIAL_CERTIFICATION,
        taxonomy.NOT_CERTIFIED,
        taxonomy.CERTIFICATION_MISSING,
    ):
        reasons = _reasons(_obs(), status=status)
        assert f"{taxonomy.REJECT_NOT_CERTIFIED}:{status}" in reasons


def test_missing_certification_fails_closed():
    """An absent certification artifact must deny the write, never be an
    implicit pass -- that inversion is the exact failure mode Phase 17A's
    false conclusion came from."""
    outcome = validator.validate(
        _obs(), certification_status=taxonomy.CERTIFICATION_MISSING, now=NOW
    )
    assert not outcome.is_valid


# --- 5. Lineage completeness ---------------------------------------------
def test_missing_source_rejected():
    assert taxonomy.REJECT_MISSING_SOURCE in _reasons(_obs(source=""))


def test_missing_access_method_rejected():
    assert taxonomy.REJECT_MISSING_ACCESS_METHOD in _reasons(_obs(access_method=""))


# --- Completeness of reporting -------------------------------------------
def test_all_failures_reported_not_just_the_first():
    """`reasons` is the complete set of what is wrong, never the first
    thing noticed -- an operator fixing one problem should not have to
    re-run to discover the next."""
    raw = _obs(
        instrument="",
        kind="NONSENSE",
        payload={"iv": 1.0},
        capture_timestamp="broken",
    )
    reasons = _reasons(raw, status=taxonomy.NOT_CERTIFIED)
    assert len(reasons) >= 4


def test_validator_version_is_recorded_on_every_outcome():
    outcome = validator.validate(_obs(), certification_status=CERT, now=NOW)
    assert outcome.validator_version == taxonomy.VALIDATOR_VERSION
