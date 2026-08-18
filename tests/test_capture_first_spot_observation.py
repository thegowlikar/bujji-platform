"""Phase 17I.2 — First Live Observation Capture.

`scripts/capture_first_spot_observation.py` is an operational script
(like every other collector script in this project) loaded directly
from its file path rather than imported as a package module -- same
posture as `test_futures_depth_poller.py`. It requires live credentials
to run end-to-end (`main()`), so what IS tested here is everything that
does not: market-hours gating, the pure observation-construction step
(`build_spot_observation`), and the full append/persist/certification-
linkage/duplicate path exercised directly against `RawObservationStore`
with a real `CertificationGate` pointed at the real, already-existing
`data_certification/` artifacts -- no live broker call, no fixture
fabrication of certification state.
"""
import datetime
import importlib.util
import sys
from pathlib import Path

from bujji.market_reality import taxonomy
from bujji.market_reality.certification import CertificationGate
from bujji.market_reality.store import RawObservationStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "capture_first_spot_observation.py"
_spec = importlib.util.spec_from_file_location(
    "capture_first_spot_observation", _SCRIPT_PATH
)
capture_script = importlib.util.module_from_spec(_spec)
sys.modules["capture_first_spot_observation"] = capture_script
_spec.loader.exec_module(capture_script)

REAL_CERT_DIR = _REPO_ROOT / "data_certification"
NOW = "2026-08-13T09:30:00+05:30"


# --- Market-hours gate ----------------------------------------------------
def test_within_market_hours_true_during_session():
    weekday = datetime.datetime(2026, 8, 12, 10, 0, tzinfo=capture_script.IST)  # Wednesday
    assert capture_script.within_market_hours(weekday) is True


def test_within_market_hours_false_before_open():
    early = datetime.datetime(2026, 8, 12, 9, 0, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(early) is False


def test_within_market_hours_false_after_close():
    late = datetime.datetime(2026, 8, 12, 15, 45, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(late) is False


def test_within_market_hours_false_on_weekend():
    saturday = datetime.datetime(2026, 8, 15, 10, 0, tzinfo=capture_script.IST)
    assert capture_script.within_market_hours(saturday) is False


# --- Pure construction step -------------------------------------------------
def test_build_spot_observation_shape():
    raw = capture_script.build_spot_observation(
        ltp=24345.9, capture_timestamp=NOW,
        cert_status=taxonomy.CERTIFIED_AVAILABLE, cert_ref="some_artifact.json@ts",
    )
    assert raw.kind == taxonomy.KIND_QUOTE
    assert raw.instrument_type == taxonomy.INSTRUMENT_SPOT
    assert raw.observation.value.payload == {"ltp": 24345.9}
    assert raw.identity_fields == {}
    assert raw.lineage.access_method == capture_script.ACCESS_METHOD
    assert raw.lineage.source == capture_script.SOURCE
    assert raw.lineage.certification_status == taxonomy.CERTIFIED_AVAILABLE
    assert raw.lineage.certification_ref == "some_artifact.json@ts"


# --- Certification linkage against the REAL, already-existing artifact ----
def test_real_certification_directory_certifies_spot_via_direct_sdk():
    """This does not fabricate certification state -- it reads the actual
    `data_certification/` directory this repo ships, the same one every
    other collector script reads. If this ever regresses to anything
    other than CERTIFIED_AVAILABLE, the capture script's write path
    would fail closed for a real reason, and this test should fail too."""
    gate = CertificationGate(str(REAL_CERT_DIR))
    status, ref = gate.status_for(
        capture_script.ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT
    )
    assert status == taxonomy.CERTIFIED_AVAILABLE
    assert ref is not None and ref.startswith("fyers_nifty_spot_certification")


def test_append_stamps_the_real_certification_ref(tmp_path):
    gate = CertificationGate(str(REAL_CERT_DIR))
    status, ref = gate.status_for(
        capture_script.ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT
    )
    raw = capture_script.build_spot_observation(24345.9, NOW, status, ref)

    store = RawObservationStore(tmp_path, gate, session_id="test-spot-capture")
    result = store.append(raw, now=NOW)

    assert result.outcome == taxonomy.OUTCOME_ACCEPTED
    stored = list(store.read_accepted_events())[0]
    assert stored.payload["lineage"]["certification_status"] == taxonomy.CERTIFIED_AVAILABLE
    assert stored.payload["lineage"]["certification_ref"] == ref


# --- End-to-end append + JSONL persistence ---------------------------------
def test_append_persists_one_line_to_the_real_jsonl_file(tmp_path):
    gate = CertificationGate(str(REAL_CERT_DIR))
    status, ref = gate.status_for(
        capture_script.ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT
    )
    raw = capture_script.build_spot_observation(24345.9, NOW, status, ref)

    store = RawObservationStore(tmp_path, gate, session_id="test-spot-capture")
    result = store.append(raw, now=NOW)

    accepted_path = Path(store.accepted_path)
    assert accepted_path.name == "raw_observations.jsonl"
    assert accepted_path.exists()

    lines = accepted_path.read_text().strip().splitlines()
    assert len(lines) == 1

    import json
    record = json.loads(lines[0])
    assert record["event_id"] == result.observation_id
    assert record["payload"]["observation"]["value"]["payload"] == {"ltp": 24345.9}


def test_observation_id_is_printable_and_stable_for_the_same_fact():
    raw_a = capture_script.build_spot_observation(
        24345.9, NOW, taxonomy.CERTIFIED_AVAILABLE, "ref@ts",
    )
    raw_b = capture_script.build_spot_observation(
        24345.9, NOW, taxonomy.CERTIFIED_AVAILABLE, "ref@ts",
    )
    assert raw_a.observation_id == raw_b.observation_id
    assert isinstance(raw_a.observation_id, str) and raw_a.observation_id


# --- Duplicate execution behavior ------------------------------------------
def test_running_the_capture_twice_is_an_idempotent_noop(tmp_path):
    """Re-running the script against the same real ltp/timestamp (e.g. an
    accidental double-invocation) must not duplicate the record or
    pollute the rejection log -- the same guarantee already proven for
    every other Layer 0 writer."""
    gate = CertificationGate(str(REAL_CERT_DIR))
    status, ref = gate.status_for(
        capture_script.ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT
    )
    raw = capture_script.build_spot_observation(24345.9, NOW, status, ref)

    store = RawObservationStore(tmp_path, gate, session_id="test-spot-capture")
    first = store.append(raw, now=NOW)
    second = store.append(raw, now=NOW)

    assert first.outcome == taxonomy.OUTCOME_ACCEPTED
    assert second.outcome == taxonomy.OUTCOME_DUPLICATE
    assert second.observation_id == first.observation_id
    assert len(list(store.read_accepted_events())) == 1
    assert store.rejection_count() == 0

    # Surviving a fresh process (a real restart, not just a repeat call
    # in the same process) is the actual guarantee that matters -- a new
    # RawObservationStore instance against the same directory must still
    # recognize the id as already held.
    reopened = RawObservationStore(tmp_path, gate, session_id="test-spot-capture")
    assert reopened.holds(first.observation_id)
    third = reopened.append(raw, now=NOW)
    assert third.outcome == taxonomy.OUTCOME_DUPLICATE
    assert len(list(reopened.read_accepted_events())) == 1


def test_a_different_real_price_is_not_treated_as_a_duplicate(tmp_path):
    gate = CertificationGate(str(REAL_CERT_DIR))
    status, ref = gate.status_for(
        capture_script.ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT
    )
    store = RawObservationStore(tmp_path, gate, session_id="test-spot-capture")

    raw_a = capture_script.build_spot_observation(24345.9, NOW, status, ref)
    raw_b = capture_script.build_spot_observation(24350.1, NOW, status, ref)

    result_a = store.append(raw_a, now=NOW)
    result_b = store.append(raw_b, now=NOW)

    assert result_a.outcome == taxonomy.OUTCOME_ACCEPTED
    assert result_b.outcome == taxonomy.OUTCOME_ACCEPTED
    assert result_a.observation_id != result_b.observation_id
    assert len(list(store.read_accepted_events())) == 2
