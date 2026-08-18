"""Phase 15M Step 8 -- portfolio replay/recovery proof, reusing
`EventStore` + Phase 15G's `hydrate_position_lifecycles` directly. No
new persistence format: `PortfolioSnapshot` is recomputed on demand
from whatever `PositionLifecycle` dict hydration already produces."""
from __future__ import annotations

from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_structured_exit,
)
from bujji.position_lifecycle.models import SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.portfolio_intelligence.engine import build_portfolio_snapshot
from bujji.state_persistence.models import PersistedEvent, RECOVERY_COMPLETE, RECOVERY_PARTIAL
from bujji.state_persistence.store import EventStore


class Leg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 100.0, 0.5
    entry_bid, entry_ask = 98.0, 102.0


class Candidate:
    def __init__(self, candidate_id, ts="t0"):
        self.candidate_id, self.source_cycle_id, self.strategy_family = candidate_id, "t0", "LONG_DIRECTIONAL"
        self.timestamp, self.market_regime, self.direction, self.thesis = ts, "RANGING", "BULLISH", "X"
        self.selection_confidence, self.underlying_price, self.legs = "HIGH", 24450.0, (Leg(),)
        self.lot_size = 75
        self.underlying_symbol = "NIFTY"


def _open_event(candidate, session_id="S1", ts="t0"):
    entry = build_entry_snapshot_for_position(candidate, None)
    payload = build_position_opened_payload(session_id, candidate, entry, ts)
    pid = payload["position_id"]
    return PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id=session_id, cycle_id=ts,
        timestamp=ts, schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=payload,
    ), pid


def test_uninterrupted_replay_multiple_positions(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    event2, pid2 = _open_event(Candidate("C2"), ts="t1")
    store.append(event1)
    store.append(event2)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 2
    assert set(snap.open_position_ids) == {pid1, pid2}


def test_checkpoint_resume_matches_uninterrupted(tmp_path):
    """'Checkpoint' here means: hydrate mid-stream, build a snapshot,
    then append MORE events and hydrate again -- the later snapshot
    must be a strict, consistent superset (Step 8's 'checkpoint/resume'
    requirement), since PortfolioSnapshot has no persistence of its own
    to diverge from lifecycle state."""
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    store.append(event1)
    states_checkpoint, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap_checkpoint = build_portfolio_snapshot(states_checkpoint, "S1")
    assert snap_checkpoint.position_count == 1

    event2, pid2 = _open_event(Candidate("C2"), ts="t1")
    store.append(event2)
    states_resumed, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap_resumed = build_portfolio_snapshot(states_resumed, "S1")
    assert snap_resumed.position_count == 2
    assert pid1 in snap_resumed.open_position_ids
    assert pid2 in snap_resumed.open_position_ids


def test_restart_produces_identical_snapshot(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    event2, pid2 = _open_event(Candidate("C2"), ts="t1")
    store.append(event1)
    store.append(event2)
    states_a, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap_a = build_portfolio_snapshot(states_a, "S1")
    # "restart" -- fresh EventStore against the same file.
    states_b, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap_b = build_portfolio_snapshot(states_b, "S1")
    assert snap_a.to_dict() == snap_b.to_dict()


def test_duplicate_events_deduped_before_snapshot(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    store.append(event1)
    store.append(event1)  # exact duplicate.
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.events_skipped_duplicate >= 1
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1


def test_malformed_record_degrades_safely(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    store.append(event1)
    with open(path, "a") as f:
        f.write("not even json\n")
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status in (RECOVERY_PARTIAL, RECOVERY_COMPLETE)
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1  # the one valid position still recovered.


def test_truncated_jsonl_degrades_safely(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    store.append(event1)
    with open(path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "POSITION_OPE')  # torn mid-write, no trailing newline.
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_PARTIAL
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.position_count == 1


def test_same_symbol_concurrent_positions_replay(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    event2, pid2 = _open_event(Candidate("C2"), ts="t1")
    store.append(event1)
    store.append(event2)
    states, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap = build_portfolio_snapshot(states, "S1")
    conc = {c.key: c for c in snap.concentration_by_underlying}
    assert conc["NIFTY"].position_count == 2


def test_mixed_open_closed_positions_replay(tmp_path):
    path = str(tmp_path / "events.jsonl")
    store = EventStore(path)
    event1, pid1 = _open_event(Candidate("C1"), ts="t0")
    store.append(event1)
    states, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    leg = states[pid1].legs[0]
    structured_exit = build_structured_exit((leg,), 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    close_payload = build_position_closed_payload(pid1, "t5", "manual", structured_exit)
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid1}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=close_payload,
    ))
    event2, pid2 = _open_event(Candidate("C2"), ts="t1")
    store.append(event2)

    states, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    snap = build_portfolio_snapshot(states, "S1")
    assert snap.closed_position_ids == (pid1,)
    assert snap.open_position_ids == (pid2,)
    assert snap.pnl.per_position[pid1] == 50.0 * 75
    assert snap.pnl.per_position[pid2] is None
