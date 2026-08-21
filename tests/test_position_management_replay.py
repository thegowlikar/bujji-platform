"""Tests -- Phase 15I Step 7: Position Management replayability, via
Phase 15G's own EventStore-based lifecycle recovery (Phase 15H's
formal replay pattern, reused directly -- no new mechanism)."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_position_closed_payload, build_position_opened_payload,
)
from bujji.position_lifecycle.models import SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION, STATUS_OPEN
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.position_management.engine import assess_position_management
from bujji.state_persistence.models import RECOVERY_COMPLETE, PersistedEvent
from bujji.state_persistence.store import EventStore


class FakeLeg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 130.0, 0.5


class FakeCandidate:
    candidate_id, source_cycle_id, strategy_family = "STC-abc", "t0", "LONG_DIRECTIONAL"
    timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
    selection_confidence, underlying_price, legs = "HIGH", 24450.0, (FakeLeg(),)


class _FakeThesisEval:
    def __init__(self, thesis_status, evidence_confidence="HIGH"):
        self.thesis_status = thesis_status
        self.evidence_confidence = evidence_confidence


def _persist_full_lifecycle(path, session_id="S1"):
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id=session_id, cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    for i, status in enumerate(("THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED")):
        assessment = assess_position_management(pid, f"c{i}", _FakeThesisEval(status), None, None)
        mgmt_payload = build_management_assessed_payload(pid, f"c{i}", assessment)
        store.append(PersistedEvent(
            event_id=f"MGMT-{pid}-{i}", event_type="MANAGEMENT_ASSESSED", session_id=session_id, cycle_id=f"c{i}",
            timestamp=f"t{i+1}", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=mgmt_payload,
        ))
    return pid


def test_replay_from_persisted_events_matches_uninterrupted_state(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_lifecycle(path)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert len(states[pid].management_assessments) == 3
    assert states[pid].status == STATUS_OPEN


def test_deterministic_double_replay(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_lifecycle(path)

    states1, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    states2, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states1[pid].to_dict() == states2[pid].to_dict()


def test_restart_mid_management_history_matches_full_replay(tmp_path):
    """Persist open + 2 management events, hydrate (simulating a
    restart), then persist the 3rd event and hydrate again -- must
    match a single uninterrupted replay of all 3."""
    path_full = str(tmp_path / "full.jsonl")
    pid = _persist_full_lifecycle(path_full)
    full_states, _ = hydrate_position_lifecycles(EventStore(path_full), "S1")

    # Split: build the same session incrementally with a restart in between.
    path_split = str(tmp_path / "split.jsonl")
    store = EventStore(path_split)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    split_pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{split_pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    for i, status in enumerate(("THESIS_INTACT", "THESIS_WEAKENING")):
        assessment = assess_position_management(split_pid, f"c{i}", _FakeThesisEval(status), None, None)
        mgmt_payload = build_management_assessed_payload(split_pid, f"c{i}", assessment)
        store.append(PersistedEvent(
            event_id=f"MGMT-{split_pid}-{i}", event_type="MANAGEMENT_ASSESSED", session_id="S1", cycle_id=f"c{i}",
            timestamp=f"t{i+1}", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=mgmt_payload,
        ))
    # "restart" -- fresh EventStore against the same real file.
    mid_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")
    assert len(mid_states[split_pid].management_assessments) == 2

    # Continue: the 3rd event.
    assessment = assess_position_management(split_pid, "c2", _FakeThesisEval("THESIS_INVALIDATED"), None, None)
    mgmt_payload = build_management_assessed_payload(split_pid, "c2", assessment)
    store.append(PersistedEvent(
        event_id=f"MGMT-{split_pid}-2", event_type="MANAGEMENT_ASSESSED", session_id="S1", cycle_id="c2",
        timestamp="t3", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=mgmt_payload,
    ))
    final_split_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")

    assert final_split_states[split_pid].to_dict() == full_states[pid].to_dict()
