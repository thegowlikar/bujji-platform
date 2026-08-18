"""Phase 15O Step 9 -- MANDATORY crash/restart proof. All 7 required
lifecycle interruption points, each reconstructed deterministically via
the EXISTING recovery/replay infrastructure (Phase 15G/15H/15N) -- no
new recovery mechanism.

EVIDENCE CLASS: SYNTHETIC/SEMANTIC FIXTURE + REPLAY. The candidate is a
fixture (see test_shadow_lifecycle_orchestrator.py's own header); the
recovery/replay behaviour proven here is real."""
from __future__ import annotations

import asyncio

from bujji.broker.paper import PaperBroker
from bujji.outcome_memory.recovery import hydrate_outcome_memory
from bujji.position_lifecycle.models import STATUS_CLOSED, STATUS_OPEN
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.shadow_lifecycle.orchestrator import (
    close_position, monitor_position, open_position_from_candidate, record_outcome_memory,
)
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL
from bujji.state_persistence.store import EventStore

from tests.test_shadow_lifecycle_orchestrator import FixtureCandidate, FixtureThesisEval


def _run(coro):
    return asyncio.run(coro)


def _paths(tmp_path):
    return str(tmp_path / "lifecycle.jsonl"), str(tmp_path / "memory.jsonl")


# 1. Restart immediately after candidate formation (nothing persisted yet).
def test_restart_after_candidate_formation(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    # A candidate exists in memory but no lifecycle event was ever written.
    states, report = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert states == {}  # nothing to recover -- honest empty state, not an error.


# 2. Restart after OPEN.
def test_restart_after_open(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id

    rehydrated, report = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    assert rehydrated[pid].status == STATUS_OPEN
    assert rehydrated[pid].to_dict() == states[pid].to_dict()


# 3. Restart while the position is being monitored.
def test_restart_during_monitoring(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = monitor_position(store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"))

    rehydrated, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert rehydrated[pid].status == STATUS_OPEN
    assert len(rehydrated[pid].thesis_evaluations) == 1
    assert rehydrated[pid].to_dict() == states[pid].to_dict()

    # Monitoring must be able to CONTINUE after the restart, from recovered state.
    rehydrated, steps = monitor_position(store, "S1", pid, "c2", "t2", rehydrated, FixtureThesisEval("THESIS_WEAKENING"))
    assert len(rehydrated[pid].thesis_evaluations) == 2


# 4. Restart after MANAGEMENT_ASSESSED.
def test_restart_after_management_assessed(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = monitor_position(store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"))

    rehydrated, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert len(rehydrated[pid].management_assessments) == 1
    assert rehydrated[pid].latest_management_recommendation is not None


# 5. Restart immediately before CLOSE -- then close from recovered state.
def test_restart_immediately_before_close(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = monitor_position(store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"))

    rehydrated, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert rehydrated[pid].status == STATUS_OPEN
    # Close using the RECOVERED state (and the same still-live broker session).
    rehydrated, close_steps = _run(close_position(store, broker, "S1", pid, "c2", "t5", "session_end", rehydrated))
    assert rehydrated[pid].status == STATUS_CLOSED
    assert rehydrated[pid].structured_exit["pnl_status"] == "COMPLETE"


# 6. Restart after CLOSE but before OUTCOME_ATTRIBUTED.
def test_restart_after_close_before_attribution(tmp_path):
    """`close_position` writes CLOSE and ATTRIBUTED together, so this
    interruption is simulated at the persistence layer: hydrate from a
    store containing ONLY the events up to CLOSE, then verify
    attribution can still be produced from the recovered state."""
    from bujji.outcome_attribution.engine import attribute_position_outcome
    from bujji.position_lifecycle.engine import apply_event, build_outcome_attributed_payload

    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = _run(close_position(store, broker, "S1", pid, "c2", "t5", "session_end", states))

    # Rebuild a store containing every event EXCEPT the attribution one.
    truncated_path = str(tmp_path / "truncated.jsonl")
    truncated = EventStore(truncated_path)
    for event in EventStore(lifecycle_path).read_events():
        if event.event_type == "OUTCOME_ATTRIBUTED":
            continue
        truncated.append(event)

    rehydrated, report = hydrate_position_lifecycles(EventStore(truncated_path), "S1")
    assert rehydrated[pid].status == STATUS_CLOSED
    assert rehydrated[pid].outcome_attribution is None  # genuinely not yet attributed.

    # Attribution must still be derivable from the recovered closed position.
    attribution = attribute_position_outcome(rehydrated[pid])
    assert attribution.readiness == "READY"
    rehydrated, result = apply_event(rehydrated, "S1", "OUTCOME_ATTRIBUTED", "S1",
                                      build_outcome_attributed_payload(pid, attribution))
    assert result.outcome == "ACCEPTED"
    assert rehydrated[pid].outcome_attribution is not None


# 7. Restart after OUTCOME_ATTRIBUTED (memory not yet written).
def test_restart_after_attribution_before_memory(tmp_path):
    lifecycle_path, memory_path = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    memory_store = EventStore(memory_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = _run(close_position(store, broker, "S1", pid, "c2", "t5", "session_end", states))

    # Memory was never written before the "crash".
    memories, _ = hydrate_outcome_memory(EventStore(memory_path))
    assert memories == {}

    # After restart, memory must still be recordable from the recovered lifecycle.
    rehydrated, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert rehydrated[pid].outcome_attribution is not None
    step = record_outcome_memory(memory_store, "S1", pid, "c2", "t6", rehydrated)
    assert step.outcome == "RECORDED"
    memories, _ = hydrate_outcome_memory(EventStore(memory_path))
    assert len(memories) == 1


def test_full_chain_replay_is_deterministic(tmp_path):
    lifecycle_path, memory_path = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    memory_store = EventStore(memory_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = monitor_position(store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"))
    states, _ = _run(close_position(store, broker, "S1", pid, "c2", "t5", "session_end", states))
    record_outcome_memory(memory_store, "S1", pid, "c2", "t6", states)

    a, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    b, _ = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert a[pid].to_dict() == b[pid].to_dict()

    m1, _ = hydrate_outcome_memory(EventStore(memory_path))
    m2, _ = hydrate_outcome_memory(EventStore(memory_path))
    assert {k: v.to_dict() for k, v in m1.items()} == {k: v.to_dict() for k, v in m2.items()}


def test_torn_record_mid_lifecycle_degrades_safely(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    with open(lifecycle_path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "POSITION_CLO')  # torn mid-write.
    rehydrated, report = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert report.base.status == RECOVERY_PARTIAL
    assert rehydrated[pid].status == STATUS_OPEN  # never corrupted into a false CLOSED.


def test_duplicate_lifecycle_events_never_double_applied(tmp_path):
    lifecycle_path, _ = _paths(tmp_path)
    store = EventStore(lifecycle_path)
    broker = PaperBroker()
    states, open_step = _run(open_position_from_candidate(store, broker, "S1", FixtureCandidate(), "c0", "t0", {}))
    pid = open_step.position_id
    states, _ = monitor_position(store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"))
    # Re-append every event verbatim -- EventStore's own dedup must absorb it.
    for event in list(EventStore(lifecycle_path).read_events()):
        store.append(event)
    rehydrated, report = hydrate_position_lifecycles(EventStore(lifecycle_path), "S1")
    assert report.base.events_skipped_duplicate >= 1
    assert len(rehydrated[pid].thesis_evaluations) == 1  # not doubled.
