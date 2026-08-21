"""Tests -- Phase 15K replay/recovery proof, reusing Phase 15G's own
EventStore-based hydration + Phase 15H's replay discipline directly --
no new replay mechanism. Proves the full chain:
OPEN -> MANAGEMENT_ASSESSED -> CLOSED (structured) -> OUTCOME_ATTRIBUTED
persists, restarts, hydrates, and reconstructs identically."""
from __future__ import annotations

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_outcome_attributed_payload, build_position_closed_payload, build_position_opened_payload,
    build_structured_exit,
)
from bujji.position_lifecycle.models import SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION, STATUS_CLOSED
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.position_management.engine import assess_position_management
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL, PersistedEvent
from bujji.state_persistence.store import EventStore


class FakeLeg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 100.0, 0.5
    entry_bid, entry_ask = 98.0, 102.0


class FakeCandidate:
    candidate_id, source_cycle_id, strategy_family = "STC-abc", "t0", "LONG_DIRECTIONAL"
    timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
    selection_confidence, underlying_price, legs = "HIGH", 24450.0, (FakeLeg(),)
    lot_size = 75


class _FakeThesisEval:
    def __init__(self, status):
        self.thesis_status = status
        self.evidence_confidence = "HIGH"


def _persist_full_chain(path, session_id="S1", exit_price=150.0):
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id=session_id, cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))

    mgmt_assessment = assess_position_management(pid, "c1", _FakeThesisEval("THESIS_INTACT"), None, None)
    mgmt_payload = build_management_assessed_payload(pid, "c1", mgmt_assessment)
    store.append(PersistedEvent(
        event_id=f"MGMT-{pid}", event_type="MANAGEMENT_ASSESSED", session_id=session_id, cycle_id="c1",
        timestamp="t1", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=mgmt_payload,
    ))

    leg = LegRecordFromPayload(open_payload["legs"][0])
    structured_exit = build_structured_exit((leg,), 75, "t5", "manual", {leg.leg_id: {"exit_price": exit_price}})
    close_payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id=session_id, cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=close_payload,
    ))

    states, _ = hydrate_position_lifecycles(EventStore(path), session_id)
    attribution = attribute_position_outcome(states[pid])
    attribution_payload = build_outcome_attributed_payload(pid, attribution)
    store.append(PersistedEvent(
        event_id=f"ATTR-{pid}", event_type="OUTCOME_ATTRIBUTED", session_id=session_id, cycle_id="t6",
        timestamp="t6", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=attribution_payload,
    ))
    return pid


def LegRecordFromPayload(d):
    from bujji.position_lifecycle.models import LegRecord
    return LegRecord.from_dict(d)


def test_full_chain_replay_reconstructs_identical_state(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_chain(path)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    lifecycle = states[pid]
    assert lifecycle.status == STATUS_CLOSED
    assert lifecycle.structured_exit["gross_realized_pnl"] == 50.0 * 75
    assert lifecycle.realized_pnl == 50.0 * 75
    assert lifecycle.outcome_attribution is not None
    assert lifecycle.outcome_attribution["outcome_direction"] == "PROFIT"
    assert len(lifecycle.management_assessments) == 1


def test_deterministic_double_replay_of_full_chain(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_chain(path)
    states1, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    states2, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states1[pid].to_dict() == states2[pid].to_dict()


def test_restart_before_close_then_close_matches_uninterrupted(tmp_path):
    """Persist OPEN + MANAGEMENT_ASSESSED, 'restart' (fresh EventStore
    against the same file), THEN persist the structured CLOSED and
    OUTCOME_ATTRIBUTED -- must match a single uninterrupted persist."""
    path_full = str(tmp_path / "full.jsonl")
    pid_full = _persist_full_chain(path_full)
    full_states, _ = hydrate_position_lifecycles(EventStore(path_full), "S1")

    path_split = str(tmp_path / "split.jsonl")
    store = EventStore(path_split)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    mgmt_assessment = assess_position_management(pid, "c1", _FakeThesisEval("THESIS_INTACT"), None, None)
    store.append(PersistedEvent(
        event_id=f"MGMT-{pid}", event_type="MANAGEMENT_ASSESSED", session_id="S1", cycle_id="c1",
        timestamp="t1", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_management_assessed_payload(pid, "c1", mgmt_assessment),
    ))
    # "restart" here -- fresh EventStore against the same real file.
    mid_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")
    assert mid_states[pid].status == "OPEN"

    leg = LegRecordFromPayload(open_payload["legs"][0])
    structured_exit = build_structured_exit((leg,), 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "manual", structured_exit),
    ))
    post_close_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")
    attribution = attribute_position_outcome(post_close_states[pid])
    store.append(PersistedEvent(
        event_id=f"ATTR-{pid}", event_type="OUTCOME_ATTRIBUTED", session_id="S1", cycle_id="t6",
        timestamp="t6", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_outcome_attributed_payload(pid, attribution),
    ))
    final_split_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")

    assert final_split_states[pid].to_dict() == full_states[pid_full].to_dict()


def test_torn_close_event_degrades_safely(tmp_path):
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
    with open(path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "POSITION_CL')  # torn.

    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_PARTIAL
    assert report.base.events_skipped_malformed == 1
    assert states[pid].status == "OPEN"


def test_duplicate_close_event_handled_at_store_layer(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid = _persist_full_chain(path)
    store = EventStore(path)
    events = list(store.read_events())
    close_event = next(e for e in events if e.event_type == "POSITION_CLOSED")
    store.append(close_event)  # exact duplicate.
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.events_skipped_duplicate == 1
    assert states[pid].structured_exit["gross_realized_pnl"] == 50.0 * 75  # not double-applied.


def test_multi_leg_position_replay(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    store = EventStore(path)

    class MultiLegCandidate(FakeCandidate):
        legs = (
            FakeLeg(),
            type("Leg2", (), {"role": "SECONDARY", "option_type": "PE", "strike": 24300.0, "expiry": "2026-08-13",
                              "side": "SELL", "ratio": 1, "entry_mid": 90.0, "delta": -0.3,
                              "entry_bid": 88.0, "entry_ask": 92.0})(),
        )

    entry = build_entry_snapshot_for_position(MultiLegCandidate(), None)
    open_payload = build_position_opened_payload("S1", MultiLegCandidate(), entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    legs = tuple(LegRecordFromPayload(l) for l in open_payload["legs"])
    exit_prices = {legs[0].leg_id: {"exit_price": 150.0}, legs[1].leg_id: {"exit_price": 60.0}}
    structured_exit = build_structured_exit(legs, 75, "t5", "manual", exit_prices)
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "manual", structured_exit),
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert len(states[pid].legs) == 2
    assert states[pid].structured_exit["pnl_status"] == "COMPLETE"
    expected = (50.0 * 75) + (30.0 * 75)  # long CE +50, short PE +30.
    assert states[pid].structured_exit["gross_realized_pnl"] == expected


def test_legacy_lifecycle_record_without_structured_exit_hydrates_safely(tmp_path):
    """An old (pre-Phase-15K) POSITION_CLOSED payload with no
    structured_exit key at all must still hydrate without error."""
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
    legacy_close_payload = {"position_id": pid, "closed_at": "t5", "exit_reason": "manual"}  # NO structured_exit key.
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=legacy_close_payload,
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert states[pid].status == STATUS_CLOSED
    assert states[pid].structured_exit is None
    assert states[pid].realized_pnl is None
