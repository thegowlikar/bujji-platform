"""Phase 17E — Layer 0 Raw Observation Store.

Immutability, accept/reject routing, duplicate handling, lineage
preservation, restart safety, and the fail-closed certification gate
against real on-disk certification artifacts.
"""
import json

from bujji.market_reality import taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import CertificationGate, StaticCertificationGate
from bujji.market_reality.store import RawObservationStore

CERT = taxonomy.CERTIFIED_AVAILABLE
NOW = "2026-08-12T09:30:00+00:00"


def _gate(status=CERT):
    return StaticCertificationGate(status)


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


# --- Accept path ----------------------------------------------------------
def test_accepted_observation_is_stored_and_retrievable_unchanged(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    raw = _obs()
    result = store.append(raw, now=NOW)

    assert result.outcome == taxonomy.OUTCOME_ACCEPTED
    assert result.observation_id == raw.observation_id

    events = list(store.read_accepted_events())
    assert len(events) == 1
    assert events[0].payload["observation"]["value"]["payload"] == {"ltp": 24325.8}


def test_market_depth_observation_with_futures_oi_is_storable(tmp_path):
    """The MARKET_DEPTH type introduced in schema 1.1.0 -- the only
    endpoint carrying futures OI (confirmed live 2026-08-12)."""
    store = RawObservationStore(tmp_path, _gate())
    raw = _obs(
        kind=taxonomy.KIND_MARKET_DEPTH,
        instrument="NSE:NIFTY26AUGFUT",
        instrument_type=taxonomy.INSTRUMENT_FUTURE,
        identity_fields={"expiry": "2026-08-26"},
        payload={
            "bids": [{"price": 24424.0, "volume": 65}],
            "asks": [{"price": 24428.0, "volume": 260}],
            "oi": 12645685,
            "pdoi": 12124000,
        },
    )
    assert store.append(raw, now=NOW).outcome == taxonomy.OUTCOME_ACCEPTED
    stored = list(store.read_accepted_events())[0]
    assert stored.payload["observation"]["value"]["payload"]["oi"] == 12645685


# --- Duplicate path -------------------------------------------------------
def test_identical_observation_is_an_idempotent_noop(tmp_path):
    """Re-capturing the same fact (retried poll, reconnecting feed) is
    normal. It must neither duplicate the record nor pollute the
    rejection log."""
    store = RawObservationStore(tmp_path, _gate())
    raw = _obs()
    assert store.append(raw, now=NOW).outcome == taxonomy.OUTCOME_ACCEPTED
    second = store.append(raw, now=NOW)

    assert second.outcome == taxonomy.OUTCOME_DUPLICATE
    assert len(list(store.read_accepted_events())) == 1
    assert store.rejection_count() == 0


def test_a_different_fact_is_not_a_duplicate(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(payload={"ltp": 24325.8}), now=NOW)
    store.append(_obs(payload={"ltp": 24326.9}), now=NOW)
    assert len(list(store.read_accepted_events())) == 2


# --- Reject path ----------------------------------------------------------
def test_rejected_observation_lands_in_rejection_store_and_nowhere_else(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    result = store.append(_obs(payload={"ltp": 1.0, "iv": 0.2}), now=NOW)

    assert result.outcome == taxonomy.OUTCOME_REJECTED
    assert list(store.read_accepted_events()) == []  # Never a foothold in the trusted store.
    assert store.rejection_count() == 1


def test_rejection_record_carries_every_required_field(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(payload={"ltp": 1.0, "delta": 0.5}), now=NOW)

    rejected = store.read_rejected()[0]
    assert rejected.payload_hash.startswith("PLH-")
    assert rejected.rejection_reasons
    assert rejected.validator_version == taxonomy.VALIDATOR_VERSION
    assert rejected.rejected_at == NOW
    assert rejected.source == "fyers"
    assert rejected.original_payload == {"ltp": 1.0, "delta": 0.5}


def test_uncertified_source_cannot_write(tmp_path):
    store = RawObservationStore(tmp_path, _gate(taxonomy.PARTIAL_CERTIFICATION))
    assert store.append(_obs(), now=NOW).outcome == taxonomy.OUTCOME_REJECTED
    assert list(store.read_accepted_events()) == []


# --- Lineage --------------------------------------------------------------
def test_every_stored_record_carries_the_full_lineage_block(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)

    lineage = list(store.read_accepted_events())[0].payload["lineage"]
    for field in (
        "source",
        "access_method",
        "event_timestamp",
        "capture_timestamp",
        "certification_status",
        "confidence",
        "transformation_history",
    ):
        assert field in lineage


def test_event_and_capture_timestamps_are_kept_distinct(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    lineage = list(store.read_accepted_events())[0].payload["lineage"]
    assert lineage["event_timestamp"] == "2026-08-12T09:19:59+00:00"
    assert lineage["capture_timestamp"] == "2026-08-12T09:20:00+00:00"
    assert lineage["event_timestamp"] != lineage["capture_timestamp"]


def test_transformation_history_has_exactly_one_raw_capture_entry(tmp_path):
    """Layer 0 performs zero transformation. Any component appending a
    second entry is by definition not Layer 0 (Phase 17D Part 2.1)."""
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    history = list(store.read_accepted_events())[0].payload["lineage"][
        "transformation_history"
    ]
    assert history == [taxonomy.TRANSFORMATION_RAW_CAPTURE]


def test_confidence_is_derived_not_asserted(tmp_path):
    """There is no argument anywhere letting a caller declare its own
    data trustworthy -- confidence follows mechanically from
    certification plus integrity."""
    certified = _obs(certification_status=CERT)
    assert certified.lineage.confidence == taxonomy.CONFIDENCE_HIGH

    uncertified = _obs(certification_status=taxonomy.NOT_CERTIFIED)
    assert uncertified.lineage.confidence == taxonomy.CONFIDENCE_LOW

    failed_integrity = _obs(certification_status=CERT, integrity_ok=False)
    assert failed_integrity.lineage.confidence == taxonomy.CONFIDENCE_LOW


# --- Restart safety -------------------------------------------------------
def test_store_survives_restart_without_truncating(tmp_path):
    first = RawObservationStore(tmp_path, _gate())
    first.append(_obs(payload={"ltp": 1.0}), now=NOW)
    first.append(_obs(payload={"ltp": 2.0}), now=NOW)

    reopened = RawObservationStore(tmp_path, _gate())
    assert len(list(reopened.read_accepted_events())) == 2
    reopened.append(_obs(payload={"ltp": 3.0}), now=NOW)
    assert len(list(reopened.read_accepted_events())) == 3


def test_duplicate_detection_survives_restart(tmp_path):
    raw = _obs()
    RawObservationStore(tmp_path, _gate()).append(raw, now=NOW)

    reopened = RawObservationStore(tmp_path, _gate())
    assert reopened.holds(raw.observation_id)
    assert reopened.append(raw, now=NOW).outcome == taxonomy.OUTCOME_DUPLICATE
    assert len(list(reopened.read_accepted_events())) == 1


# --- Certification gate against real artifacts ---------------------------
def test_certification_gate_reads_real_artifact_shape(tmp_path):
    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "fyers_nifty_future_certification.json").write_text(
        json.dumps(
            {
                "timestamp": "2026-08-12T09:27:14Z",
                "instrument": "NIFTY_FUTURES",
                "access_method": "direct_sdk_fyers_broker_py",
                "validation_result": "CERTIFIED_AVAILABLE",
            }
        )
    )
    gate = CertificationGate(cert_dir)
    status, ref = gate.status_for(
        "direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_FUTURE
    )
    assert status == taxonomy.CERTIFIED_AVAILABLE
    assert "fyers_nifty_future_certification.json" in ref
    assert gate.permits_write("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_FUTURE)


def test_certification_gate_fails_closed_on_missing_directory(tmp_path):
    gate = CertificationGate(tmp_path / "does_not_exist")
    status, ref = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)
    assert status == taxonomy.CERTIFICATION_MISSING
    assert not gate.permits_write("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)


def test_certification_of_a_different_access_method_does_not_transfer(tmp_path):
    """A certification of the MCP connector says nothing about the direct
    SDK path. Conflating them is exactly what the connector incident
    proved dangerous."""
    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "spot.json").write_text(
        json.dumps(
            {
                "instrument": "NIFTY_SPOT",
                "access_method": "mcp_connector",
                "validation_result": "CERTIFIED_AVAILABLE",
            }
        )
    )
    gate = CertificationGate(cert_dir)
    status, _ = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)
    assert status == taxonomy.CERTIFICATION_MISSING


def test_index_without_an_artifact_is_certification_missing(tmp_path):
    """INDEX (e.g. India VIX) is now mapped to a cert key (Phase 17G,
    scripts/certify_vix_access.py), but mapping a key does not fabricate
    a certification -- with no artifact on disk this still resolves to
    CERTIFICATION_MISSING, exactly as an unmapped type would."""
    gate = CertificationGate(tmp_path)
    status, _ = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_INDEX)
    assert status == taxonomy.CERTIFICATION_MISSING


def test_index_is_certified_once_a_real_vix_artifact_exists(tmp_path):
    """Once scripts/certify_vix_access.py actually runs and writes a real,
    dated artifact under the INDIA_VIX key, the gate recognizes it --
    proving the mapping added in Phase 17G is wired correctly, not just
    present in a dict."""
    import json

    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "fyers_india_vix_certification.json").write_text(
        json.dumps({
            "instrument": "INDIA_VIX",
            "access_method": "direct_sdk_fyers_broker_py",
            "validation_result": "CERTIFIED_AVAILABLE",
            "timestamp": "2026-08-13T04:00:00Z",
        })
    )
    gate = CertificationGate(cert_dir)
    status, ref = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_INDEX)
    assert status == taxonomy.CERTIFIED_AVAILABLE
    assert "fyers_india_vix_certification.json" in ref
    assert gate.permits_write("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_INDEX)


def test_index_and_spot_are_distinct_certification_subjects(tmp_path):
    """NSE:NIFTY50-INDEX is also, technically, an index -- but it is
    certified under INSTRUMENT_SPOT, not INSTRUMENT_INDEX. A spot
    certification must never be read as certifying VIX, or vice versa."""
    import json

    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "fyers_nifty_spot_certification.json").write_text(
        json.dumps({
            "instrument": "NIFTY_SPOT",
            "access_method": "direct_sdk_fyers_broker_py",
            "validation_result": "CERTIFIED_AVAILABLE",
        })
    )
    gate = CertificationGate(cert_dir)
    spot_status, _ = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)
    vix_status, _ = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_INDEX)
    assert spot_status == taxonomy.CERTIFIED_AVAILABLE
    assert vix_status == taxonomy.CERTIFICATION_MISSING


def test_malformed_certification_artifact_denies_rather_than_crashes(tmp_path):
    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "broken.json").write_text("{not json")
    gate = CertificationGate(cert_dir)
    status, _ = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)
    assert status == taxonomy.CERTIFICATION_MISSING


def test_two_access_methods_for_the_same_instrument_do_not_collide(tmp_path):
    """Phase 17G.0 Gate B audit finding: a REST certification and a
    websocket certification for the SAME instrument (e.g. NIFTY_SPOT)
    must both remain independently visible to the gate. Previously the
    index was keyed by `instrument` alone, so writing a second artifact
    for the same instrument under a different access_method silently
    shadowed the first -- whichever filename sorted last won, and the
    other became invisible even though its file still existed on disk.
    This is the exact real-world scenario Gate B produces: a REST
    NIFTY_SPOT cert already exists, and a websocket NIFTY_SPOT cert is
    about to be added alongside it."""
    import json

    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "fyers_nifty_spot_certification.json").write_text(
        json.dumps({
            "instrument": "NIFTY_SPOT",
            "access_method": "direct_sdk_fyers_broker_py",
            "validation_result": "CERTIFIED_AVAILABLE",
            "timestamp": "2026-08-12T09:27:14+00:00",
        })
    )
    # Alphabetically sorts AFTER the REST artifact -- under the old
    # instrument-only index, this would have won the collision and
    # shadowed the REST certification entirely.
    (cert_dir / "fyers_websocket_certification_20260813.json").write_text(
        json.dumps({
            "instrument": "NIFTY_SPOT",
            "access_method": "fyers_websocket",
            "validation_result": "CERTIFIED_AVAILABLE",
            "timestamp": "2026-08-13T09:31:00+00:00",
        })
    )
    gate = CertificationGate(cert_dir)

    rest_status, rest_ref = gate.status_for("direct_sdk_fyers_broker_py", taxonomy.INSTRUMENT_SPOT)
    ws_status, ws_ref = gate.status_for("fyers_websocket", taxonomy.INSTRUMENT_SPOT)

    assert rest_status == taxonomy.CERTIFIED_AVAILABLE
    assert "fyers_nifty_spot_certification.json" in rest_ref
    assert ws_status == taxonomy.CERTIFIED_AVAILABLE
    assert "fyers_websocket_certification_20260813.json" in ws_ref


def test_a_third_unrelated_access_method_for_a_certified_instrument_is_missing(tmp_path):
    """Only the exact (instrument, access_method) pair that was actually
    certified is CERTIFIED -- a real cert for NIFTY_SPOT via REST says
    nothing about, say, a hypothetical third access path that was never
    certified at all."""
    import json

    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "fyers_nifty_spot_certification.json").write_text(
        json.dumps({
            "instrument": "NIFTY_SPOT",
            "access_method": "direct_sdk_fyers_broker_py",
            "validation_result": "CERTIFIED_AVAILABLE",
        })
    )
    gate = CertificationGate(cert_dir)
    status, ref = gate.status_for("some_other_access_method", taxonomy.INSTRUMENT_SPOT)
    assert status == taxonomy.CERTIFICATION_MISSING
    assert ref is None


def test_load_index_key_includes_access_method(tmp_path):
    """White-box check on the fix itself: _load()'s keys are
    (instrument, access_method) tuples, not bare instrument strings."""
    import json

    cert_dir = tmp_path / "data_certification"
    cert_dir.mkdir()
    (cert_dir / "a.json").write_text(json.dumps({
        "instrument": "NIFTY_SPOT", "access_method": "direct_sdk_fyers_broker_py",
        "validation_result": "CERTIFIED_AVAILABLE",
    }))
    gate = CertificationGate(cert_dir)
    index = gate._load()
    assert ("NIFTY_SPOT", "direct_sdk_fyers_broker_py") in index
    assert "NIFTY_SPOT" not in index  # the old bare-instrument key must not exist
