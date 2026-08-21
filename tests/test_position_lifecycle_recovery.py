"""Tests -- Phase 15G Position Lifecycle recovery (Step 10). Reuses
EventStore directly -- no new persistence mechanism. Tests semantic
STATE equivalence at multiple restart boundaries, not merely "the file
loads.\""""
from __future__ import annotations

import json

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_thesis_evaluated_payload,
)
from bujji.position_lifecycle.models import (
    STATUS_CLOSED, STATUS_OPEN, SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION,
)
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL, PersistedEvent
from bujji.state_persistence.store import EventStore


class FakeLeg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, expiry="2026-08-13",
                 side="BUY", ratio=1, entry_mid=130.0, delta=0.5):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, expiry
        self.side, self.ratio, self.entry_mid, self.delta = side, ratio, entry_mid, delta


class FakeCandidate:
    def __init__(self, candidate_id="STC-abc", family="LONG_DIRECTIONAL", legs=None, ts="t0"):
        self.candidate_id = candidate_id
        self.source_cycle_id = ts
        self.strategy_family = family
        self.timestamp = ts
        self.market_regime = "RANGING"
        self.direction = "BULLISH"
        self.thesis = "TREND_CONTINUATION"
        self.selection_confidence = "HIGH"
        self.underlying_price = 24450.0
        self.legs = legs or (FakeLeg(),)


class _FakeEvaluation:
    def __init__(self, status):
        self._status = status

    def to_dict(self):
        return {"thesis_status": self._status, "checks": [], "recommendation": "HOLD", "recommendation_reason": "r"}


def _persist_open(store, session_id, candidate, entry_timestamp, seq):
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, entry_timestamp)
    event_id = f"OPEN-{payload['position_id']}"
    store.append(PersistedEvent(
        event_id=event_id, event_type="POSITION_OPENED", session_id=session_id, cycle_id=entry_timestamp,
        timestamp=entry_timestamp, schema_version=LIFECYCLE_SCHEMA_VERSION,
        provenance="test", payload=payload,
    ))
    return payload["position_id"]


def _persist_thesis(store, session_id, position_id, cycle_id, ts, status, seq):
    payload = build_thesis_evaluated_payload(position_id, cycle_id, _FakeEvaluation(status))
    store.append(PersistedEvent(
        event_id=f"THESIS-{position_id}-{seq}", event_type="THESIS_EVALUATED", session_id=session_id,
        cycle_id=cycle_id, timestamp=ts, schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=payload,
    ))


def _persist_close(store, session_id, position_id, ts, reason, seq):
    payload = build_position_closed_payload(position_id, ts, reason)
    store.append(PersistedEvent(
        event_id=f"CLOSE-{position_id}", event_type="POSITION_CLOSED", session_id=session_id,
        cycle_id=ts, timestamp=ts, schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=payload,
    ))


# ---------------------------------------------------------------------------
# 1. Missing file -- clean recovery.
# ---------------------------------------------------------------------------
def test_missing_file_is_recovery_complete(tmp_path):
    store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    states, report = hydrate_position_lifecycles(store, "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert states == {}


# ---------------------------------------------------------------------------
# 2. Full lifecycle replay equivalence -- before entry, mid-monitoring,
#    after close, restart at each boundary.
# ---------------------------------------------------------------------------
def test_restart_at_multiple_boundaries_matches_uninterrupted(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()

    pid = _persist_open(store, "S1", candidate, "t0", 0)
    _persist_thesis(store, "S1", pid, "c1", "t1", "THESIS_INTACT", 1)
    _persist_thesis(store, "S1", pid, "c2", "t2", "THESIS_WEAKENING", 2)
    _persist_close(store, "S1", pid, "t3", "session_end", 3)

    # Full replay is the reference.
    ref_states, ref_report = hydrate_position_lifecycles(store, "S1")
    assert ref_report.base.status == RECOVERY_COMPLETE
    assert ref_states[pid].status == STATUS_CLOSED
    assert len(ref_states[pid].thesis_evaluations) == 2

    # "Restart" at any point just means: construct a FRESH EventStore
    # against the SAME real file and hydrate again -- must be identical.
    restarted_store = EventStore(path)
    restarted_states, restarted_report = hydrate_position_lifecycles(restarted_store, "S1")
    assert restarted_states[pid].to_dict() == ref_states[pid].to_dict()
    assert restarted_report.to_dict() == ref_report.to_dict()


def test_restart_before_entry_no_events_yet(tmp_path):
    store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    states, report = hydrate_position_lifecycles(store, "S1")
    assert states == {}
    assert report.base.status == RECOVERY_COMPLETE


def test_restart_immediately_after_entry(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].status == STATUS_OPEN
    assert states[pid].thesis_evaluations == ()


def test_restart_mid_monitoring(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    _persist_thesis(store, "S1", pid, "c1", "t1", "THESIS_INTACT", 1)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].status == STATUS_OPEN
    assert len(states[pid].thesis_evaluations) == 1


def test_restart_after_thesis_weakening(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    _persist_thesis(store, "S1", pid, "c1", "t1", "THESIS_WEAKENING", 1)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].final_thesis_status == "THESIS_WEAKENING"


def test_restart_immediately_before_close(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    _persist_thesis(store, "S1", pid, "c1", "t1", "THESIS_WEAKENING", 1)
    # Restart here -- close has NOT been persisted yet.
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].status == STATUS_OPEN
    # Now the "next process" closes it.
    _persist_close(store, "S1", pid, "t2", "manual", 2)
    states2, report2 = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states2[pid].status == STATUS_CLOSED


def test_restart_after_close(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    _persist_close(store, "S1", pid, "t1", "manual", 1)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].status == STATUS_CLOSED


def test_restart_multi_leg_position(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    legs = (FakeLeg(role="SHORT_CALL", option_type="CE", strike=24600.0, side="SELL"),
            FakeLeg(role="SHORT_PUT", option_type="PE", strike=24300.0, side="SELL"))
    candidate = FakeCandidate(family="IRON_CONDOR", legs=legs)
    pid = _persist_open(store, "S1", candidate, "t0", 0)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert len(states[pid].legs) == 2
    assert {l.role for l in states[pid].legs} == {"SHORT_CALL", "SHORT_PUT"}


def test_restart_multiple_simultaneous_positions(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate_a = FakeCandidate(candidate_id="STC-A", ts="ta")
    candidate_b = FakeCandidate(candidate_id="STC-B", ts="tb")
    pid_a = _persist_open(store, "S1", candidate_a, "ta", 0)
    pid_b = _persist_open(store, "S1", candidate_b, "tb", 1)
    _persist_close(store, "S1", pid_a, "ta2", "manual", 2)

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert len(states) == 2
    assert states[pid_a].status == STATUS_CLOSED
    assert states[pid_b].status == STATUS_OPEN


# ---------------------------------------------------------------------------
# Corruption / failure recovery.
# ---------------------------------------------------------------------------
def test_torn_final_event_degrades_safely(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    with open(path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "THESIS_EVAL')  # torn.

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_PARTIAL
    assert report.base.events_skipped_malformed == 1
    assert states[pid].status == STATUS_OPEN


def test_duplicate_event_id_is_idempotent_at_store_layer(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    pid = _persist_open(store, "S1", candidate, "t0", 0)
    # Re-append the exact same open event (same event_id).
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S1", candidate, entry, "t0")
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=payload,
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.events_skipped_duplicate == 1
    assert states[pid].status == STATUS_OPEN  # not double-applied.


def test_event_from_another_session_rejected_during_replay(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload("S2", candidate, entry, "t0")  # built FOR session S2.
    store.append(PersistedEvent(
        event_id="OPEN-wrong-session", event_type="POSITION_OPENED", session_id="S2", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=payload,
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")  # hydrating S1.
    assert states == {}
    assert report.transitions_rejected == 1
