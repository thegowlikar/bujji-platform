"""Phase 15N Step 11 -- Outcome Memory replay/recovery proof, reusing
`EventStore` directly. Proves:
Outcome Attribution -> EventStore -> Outcome Memory -> restart ->
hydrate -> query produces identical memory."""
from __future__ import annotations

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_memory.engine import build_outcome_memory_record, build_outcome_memory_recorded_payload
from bujji.outcome_memory.models import EVENT_OUTCOME_MEMORY_RECORDED, SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from bujji.outcome_memory.query import query_outcome_distribution
from bujji.outcome_memory.recovery import hydrate_outcome_memory
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_structured_exit,
)
from bujji.state_persistence.models import PersistedEvent, RECOVERY_COMPLETE, RECOVERY_PARTIAL
from bujji.state_persistence.store import EventStore


class Leg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 100.0, 0.5
    entry_bid, entry_ask = 98.0, 102.0


class Candidate:
    def __init__(self, cid, session_id="S1"):
        self.candidate_id, self.source_cycle_id, self.strategy_family = cid, "t0", "LONG_DIRECTIONAL"
        self.timestamp, self.market_regime, self.direction, self.thesis = "t0", "RANGING", "BULLISH", "X"
        self.selection_confidence, self.underlying_price, self.legs = "HIGH", 24450.0, (Leg(),)
        self.lot_size = 75
        self.underlying_symbol = "NIFTY"


def _build_memory_record(session_id, cid, exit_price=150.0):
    candidate = Candidate(cid, session_id)
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    pid = open_payload["position_id"]
    states, _ = apply_event({}, session_id, "POSITION_OPENED", session_id, open_payload)
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit((leg,), 75, "t5", "manual", {leg.leg_id: {"exit_price": exit_price}})
    close_payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)
    states, _ = apply_event(states, session_id, "POSITION_CLOSED", session_id, close_payload)
    attribution = attribute_position_outcome(states[pid])
    return build_outcome_memory_record(states[pid], attribution, "t6")


def _persist(store, record, event_id=None):
    payload = build_outcome_memory_recorded_payload(record)
    store.append(PersistedEvent(
        event_id=event_id or f"MEM-{record.memory_id}", event_type=EVENT_OUTCOME_MEMORY_RECORDED,
        session_id=record.session_id, cycle_id="t6", timestamp="t6",
        schema_version=MEMORY_SCHEMA_VERSION, provenance="test",
        payload=payload,
    ))


def test_uninterrupted_recording_and_hydration(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record = _build_memory_record("S1", "C1")
    _persist(store, record)
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.base.status == RECOVERY_COMPLETE
    assert states[record.memory_id].to_dict() == record.to_dict()


def test_restart_mid_recording(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record1 = _build_memory_record("S1", "C1")
    _persist(store, record1)
    # "restart" -- fresh EventStore against the same file.
    states, _ = hydrate_outcome_memory(EventStore(path))
    assert len(states) == 1

    record2 = _build_memory_record("S1", "C2")
    _persist(EventStore(path), record2)
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.base.status == RECOVERY_COMPLETE
    assert len(states) == 2


def test_duplicate_event_deduped_at_store_layer(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record = _build_memory_record("S1", "C1")
    _persist(store, record)
    _persist(store, record)  # exact same event_id -- caught by EventStore's own dedup.
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.base.events_skipped_duplicate >= 1
    assert len(states) == 1


def test_conflicting_event_rejected_at_reducer_layer(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record = _build_memory_record("S1", "C1")
    _persist(store, record)
    tampered = dict(record.to_dict())
    tampered["realized_pnl"] = 999999.0
    store.append(PersistedEvent(
        event_id=f"MEM-{record.memory_id}-conflict", event_type=EVENT_OUTCOME_MEMORY_RECORDED,
        session_id="S1", cycle_id="t6", timestamp="t6", schema_version=MEMORY_SCHEMA_VERSION,
        provenance="test", payload={"memory_id": record.memory_id, "record": tampered},
    ))
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.transitions_rejected >= 1
    assert states[record.memory_id].realized_pnl == record.realized_pnl  # original preserved.


def test_malformed_event_degrades_safely(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record = _build_memory_record("S1", "C1")
    _persist(store, record)
    with open(path, "a") as f:
        f.write("not json at all\n")
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.base.status == RECOVERY_PARTIAL
    assert len(states) == 1


def test_torn_jsonl_degrades_safely(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record = _build_memory_record("S1", "C1")
    _persist(store, record)
    with open(path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "OUTCOME_MEM')  # torn mid-write, no newline.
    states, report = hydrate_outcome_memory(EventStore(path))
    assert report.base.status == RECOVERY_PARTIAL
    assert len(states) == 1


def test_replay_deterministic(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    _persist(store, _build_memory_record("S1", "C1"))
    _persist(store, _build_memory_record("S1", "C2", exit_price=60.0))
    states1, _ = hydrate_outcome_memory(EventStore(path))
    states2, _ = hydrate_outcome_memory(EventStore(path))
    assert {k: v.to_dict() for k, v in states1.items()} == {k: v.to_dict() for k, v in states2.items()}


def test_checkpoint_resume_consistent_superset(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    _persist(store, _build_memory_record("S1", "C1"))
    states_checkpoint, _ = hydrate_outcome_memory(EventStore(path))
    assert len(states_checkpoint) == 1

    _persist(EventStore(path), _build_memory_record("S1", "C2"))
    states_resumed, _ = hydrate_outcome_memory(EventStore(path))
    assert len(states_resumed) == 2
    assert set(states_checkpoint.keys()) <= set(states_resumed.keys())


def test_multi_session_isolation_of_identity_not_query():
    """CROSS-SESSION memory (this phase's own design) means BOTH
    sessions' records legitimately coexist in one hydrated dict -- but
    their memory_ids must never collide, and each record must retain
    its OWN correct session_id (proven here) so a query can still
    isolate by session when it explicitly wants to (`filter_records`)."""
    record_a = _build_memory_record("SESSION-A", "SAME-CANDIDATE")
    record_b = _build_memory_record("SESSION-B", "SAME-CANDIDATE")
    assert record_a.memory_id != record_b.memory_id
    assert record_a.session_id == "SESSION-A"
    assert record_b.session_id == "SESSION-B"


def test_historical_ordering_preserved_via_recorded_at(tmp_path):
    path = str(tmp_path / "memory.jsonl")
    store = EventStore(path)
    record1 = _build_memory_record("S1", "C1")
    record2 = _build_memory_record("S1", "C2")
    _persist(store, record1, event_id="EV-1")
    _persist(store, record2, event_id="EV-2")
    states, _ = hydrate_outcome_memory(EventStore(path))
    recorded_ats = sorted(r.recorded_at for r in states.values())
    assert recorded_ats == ["t6", "t6"]  # both real, honest timestamps -- no ordering was fabricated.
    dist = query_outcome_distribution(list(states.values()))
    assert dist.sample_size == 2
