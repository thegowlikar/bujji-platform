"""Tests -- Phase 15J OUTCOME_ATTRIBUTED lifecycle integration and
replay (bujji.position_lifecycle + bujji.outcome_attribution)."""
from __future__ import annotations

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_outcome_attributed_payload,
    build_position_closed_payload, build_position_opened_payload,
)
from bujji.position_lifecycle.models import (
    SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION, STATUS_CLOSED, STATUS_OPEN,
    TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED,
)
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.state_persistence.models import RECOVERY_COMPLETE, PersistedEvent
from bujji.state_persistence.store import EventStore


class FakeLeg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 130.0, 0.5


class FakeCandidate:
    candidate_id, source_cycle_id, strategy_family = "STC-abc", "t0", "LONG_DIRECTIONAL"
    timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
    selection_confidence, underlying_price, legs = "HIGH", 24450.0, (FakeLeg(),)


def _open(session_id="S1"):
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    return apply_event({}, session_id, "POSITION_OPENED", session_id, payload)


def _close(states, session_id, pid):
    return apply_event(states, session_id, "POSITION_CLOSED", session_id, build_position_closed_payload(pid, "t5", "session_end"))


# ---------------------------------------------------------------------------
# Guard: only accepted while CLOSED (opposite of THESIS_EVALUATED/MANAGEMENT_ASSESSED).
# ---------------------------------------------------------------------------
def test_outcome_attribution_rejected_while_open():
    states, r1 = _open()
    pid = r1.position_id
    attribution = attribute_position_outcome(states[pid])
    payload = build_outcome_attributed_payload(pid, attribution)
    states, r2 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload)
    assert r2.outcome == TRANSITION_REJECTED
    assert "not CLOSED" in r2.reason
    assert states[pid].outcome_attribution is None


def test_outcome_attribution_accepted_after_close():
    states, r1 = _open()
    pid = r1.position_id
    states, _ = _close(states, "S1", pid)
    attribution = attribute_position_outcome(states[pid])
    assert attribution.readiness == "READY"
    payload = build_outcome_attributed_payload(pid, attribution)
    states, r2 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload)
    assert r2.outcome == TRANSITION_ACCEPTED
    assert states[pid].outcome_attribution == attribution.to_dict()
    assert states[pid].status == STATUS_CLOSED  # unchanged.


def test_outcome_attribution_unknown_position_rejected():
    attribution_payload = {"position_id": "POS-nope", "attribution": {"x": 1}}
    states, r = apply_event({}, "S1", "OUTCOME_ATTRIBUTED", "S1", attribution_payload)
    assert r.outcome == TRANSITION_REJECTED
    assert "unknown position_id" in r.reason


def test_duplicate_outcome_attribution_identical_content_idempotent():
    states, r1 = _open()
    pid = r1.position_id
    states, _ = _close(states, "S1", pid)
    attribution = attribute_position_outcome(states[pid])
    payload = build_outcome_attributed_payload(pid, attribution)
    states, r2 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload)
    states, r3 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload)
    assert r2.outcome == TRANSITION_ACCEPTED
    assert r3.outcome == TRANSITION_IDEMPOTENT


def test_conflicting_outcome_attribution_rejected():
    states, r1 = _open()
    pid = r1.position_id
    states, _ = _close(states, "S1", pid)
    attribution = attribute_position_outcome(states[pid])
    payload_a = build_outcome_attributed_payload(pid, attribution)
    states, r2 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload_a)
    payload_b = dict(payload_a)
    payload_b["attribution"] = dict(payload_a["attribution"])
    payload_b["attribution"]["narrative"] = "a different, conflicting narrative"
    states, r3 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload_b)
    assert r3.outcome == TRANSITION_REJECTED
    assert states[pid].outcome_attribution == payload_a["attribution"]  # never silently overwritten.


def test_wrong_session_outcome_attribution_rejected():
    states, r1 = _open(session_id="S1")
    pid = r1.position_id
    states, _ = _close(states, "S1", pid)
    attribution = attribute_position_outcome(states[pid])
    payload = build_outcome_attributed_payload(pid, attribution)
    states2, r2 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S2", payload)
    assert r2.outcome == TRANSITION_REJECTED
    assert states2[pid].outcome_attribution is None


# ---------------------------------------------------------------------------
# Replay integration (Step 7) -- reuses Phase 15G's EventStore-based
# recovery directly, no new replay mechanism.
# ---------------------------------------------------------------------------
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
    close_payload = build_position_closed_payload(pid, "t5", "session_end")
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id=session_id, cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=close_payload,
    ))
    # Compute the attribution from the closed state, then persist it.
    states, _ = hydrate_position_lifecycles(EventStore(path), session_id)
    attribution = attribute_position_outcome(states[pid])
    attribution_payload = build_outcome_attributed_payload(pid, attribution)
    store.append(PersistedEvent(
        event_id=f"ATTR-{pid}", event_type="OUTCOME_ATTRIBUTED", session_id=session_id, cycle_id="t6",
        timestamp="t6", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=attribution_payload,
    ))
    return pid


def test_replay_reconstructs_outcome_attribution(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_lifecycle(path)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert states[pid].outcome_attribution is not None
    assert states[pid].outcome_attribution["readiness"] == "READY"


def test_deterministic_double_replay_of_attribution(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_lifecycle(path)
    states1, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    states2, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states1[pid].to_dict() == states2[pid].to_dict()


def test_event_reordering_within_file_is_still_correctly_rejected(tmp_path):
    """An OUTCOME_ATTRIBUTED event appended BEFORE the POSITION_CLOSED
    event in the file must still be rejected at replay time -- file
    order is not a substitute for the CLOSED guard."""
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    # Attribution attempted BEFORE close.
    fake_attribution_payload = {"position_id": pid, "attribution": {"readiness": "READY"}}
    store.append(PersistedEvent(
        event_id=f"ATTR-{pid}", event_type="OUTCOME_ATTRIBUTED", session_id="S1", cycle_id="t1",
        timestamp="t1", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=fake_attribution_payload,
    ))
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "session_end"),
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states[pid].outcome_attribution is None  # the premature attempt was correctly rejected.
    assert states[pid].status == STATUS_CLOSED
    assert report.transitions_rejected == 1


def test_duplicate_event_handling_at_store_layer(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_lifecycle(path)
    # Re-append the exact same OUTCOME_ATTRIBUTED event (same event_id).
    store = EventStore(path)
    events = list(store.read_events())
    attr_event = next(e for e in events if e.event_type == "OUTCOME_ATTRIBUTED")
    store.append(attr_event)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.events_skipped_duplicate == 1
    assert states[pid].outcome_attribution is not None  # not double-applied, still correct.
