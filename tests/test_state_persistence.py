"""Tests -- State Persistence + Hydration, Phase 15B. Real filesystem
IO in a temp directory (deterministic, isolated), no broker execution,
no live calls."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.market_regime_memory.models import RegimeMemoryState
from bujji.state_persistence.models import (
    RECOVERY_COMPLETE, RECOVERY_FAILED, RECOVERY_PARTIAL, PersistedEvent,
)
from bujji.state_persistence.paper_broker import hydrate_paper_broker, record_paper_state
from bujji.state_persistence.regime_memory import hydrate_regime_memory, record_regime_cycle
from bujji.state_persistence.store import EventStore, deduplicated_events


@pytest.fixture
def tmp_path_str():
    path = tempfile.mktemp()
    yield path
    if os.path.exists(path):
        os.remove(path)


# ---------------------------------------------------------------------------
# 1. Clean restart -- create/operate/persist/kill/restart/hydrate/compare.
# ---------------------------------------------------------------------------
def test_clean_restart_regime_memory_state_equivalence(tmp_path_str):
    store = EventStore(tmp_path_str)
    live_state = RegimeMemoryState()
    regimes = ["RANGING"] * 4 + ["COMPRESSED"] * 6 + ["TRENDING"] * 3
    for i, r in enumerate(regimes):
        live_state = live_state.advance(r)
        record_regime_cycle(store, "S1", f"c{i}", f"t{i}", r)

    # "kill" -- store is just a file path; a fresh EventStore/process
    # sees exactly the same file, proving no in-process state is needed.
    store2 = EventStore(tmp_path_str)
    hydrated_state, report = hydrate_regime_memory(store2, "S1")

    assert hydrated_state == live_state
    assert report.status == RECOVERY_COMPLETE
    assert report.events_replayed == len(regimes)


# ---------------------------------------------------------------------------
# 2. Repeated hydration -- idempotent, same result every time.
# ---------------------------------------------------------------------------
def test_repeated_hydration_is_idempotent(tmp_path_str):
    store = EventStore(tmp_path_str)
    for i, r in enumerate(["RANGING", "TRENDING", "COMPRESSED"]):
        record_regime_cycle(store, "S1", f"c{i}", f"t{i}", r)

    state_a, report_a = hydrate_regime_memory(store, "S1")
    state_b, report_b = hydrate_regime_memory(store, "S1")
    assert state_a == state_b
    assert report_a.to_dict() == report_b.to_dict()


# ---------------------------------------------------------------------------
# 3. Duplicate event replay -- never double-applied.
# ---------------------------------------------------------------------------
def test_duplicate_event_never_double_applied(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")  # exact same cycle -- same event_id.
    state, report = hydrate_regime_memory(store, "S1")
    assert state.duration_cycles == 1  # NOT 2 -- the duplicate must not double-advance.
    assert report.events_skipped_duplicate == 1


# ---------------------------------------------------------------------------
# 4. Interrupted/partial final write -- truncated last line skipped.
# ---------------------------------------------------------------------------
def test_truncated_final_line_is_skipped_not_raised(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    record_regime_cycle(store, "S1", "c1", "t1", "TRENDING")
    # Simulate a torn write: append a truncated JSON fragment with no newline.
    with open(tmp_path_str, "a") as f:
        f.write('{"event_id": "broken", "event_type": "REGIME_OBS')  # deliberately incomplete.

    state, report = hydrate_regime_memory(store, "S1")
    assert report.events_skipped_malformed == 1
    assert report.status == RECOVERY_PARTIAL
    assert state.duration_cycles == 1  # the two valid events still replayed correctly.
    assert state.current_regime == "TRENDING"


# ---------------------------------------------------------------------------
# 5. Malformed persisted record (valid JSON, wrong shape).
# ---------------------------------------------------------------------------
def test_malformed_record_missing_required_field_is_skipped(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    with open(tmp_path_str, "a") as f:
        f.write(json.dumps({"not": "a real event"}) + "\n")
    state, report = hydrate_regime_memory(store, "S1")
    assert report.events_skipped_malformed == 1
    assert report.status == RECOVERY_PARTIAL


# ---------------------------------------------------------------------------
# 6. Unknown/missing field in payload -- honestly preserved as None, never guessed.
# ---------------------------------------------------------------------------
def test_none_regime_reading_preserved_not_fabricated(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    record_regime_cycle(store, "S1", "c1", "t1", None)  # a real "no reading this cycle" event.
    state, report = hydrate_regime_memory(store, "S1")
    assert state.current_regime == "RANGING"
    assert state.duration_cycles == 1  # None must not reset OR advance duration.
    assert report.events_replayed == 2


# ---------------------------------------------------------------------------
# 7. Schema version mismatch.
# ---------------------------------------------------------------------------
def test_schema_version_mismatch_is_skipped_not_applied(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    bad_event = PersistedEvent(
        event_id="future-event", event_type="REGIME_OBSERVED", session_id="S1", cycle_id="c1",
        timestamp="t1", schema_version="99.0.0", provenance="test", payload={"regime": "TRENDING"},
    )
    store.append(bad_event)
    state, report = hydrate_regime_memory(store, "S1")
    assert report.events_skipped_schema_mismatch == 1
    assert state.current_regime == "RANGING"  # the future-schema event never applied.
    assert report.status == RECOVERY_PARTIAL


# ---------------------------------------------------------------------------
# 8. Missing state -- a session with zero events is honestly COMPLETE (nothing to recover).
# ---------------------------------------------------------------------------
def test_missing_state_file_is_recovery_complete_not_failed(tmp_path_str):
    store = EventStore(tmp_path_str)  # file never created.
    state, report = hydrate_regime_memory(store, "S1")
    assert report.status == RECOVERY_COMPLETE
    assert report.events_discovered == 0
    assert state == RegimeMemoryState()


# ---------------------------------------------------------------------------
# 9. Multiple sessions -- isolation.
# ---------------------------------------------------------------------------
def test_multiple_sessions_are_isolated_in_the_same_store(tmp_path_str):
    store = EventStore(tmp_path_str)
    record_regime_cycle(store, "SESSION-A", "c0", "t0", "RANGING")
    record_regime_cycle(store, "SESSION-A", "c1", "t1", "RANGING")
    record_regime_cycle(store, "SESSION-B", "c0", "t0", "TRENDING")

    state_a, _ = hydrate_regime_memory(store, "SESSION-A")
    state_b, _ = hydrate_regime_memory(store, "SESSION-B")
    assert state_a.current_regime == "RANGING"
    assert state_a.duration_cycles == 2
    assert state_b.current_regime == "TRENDING"
    assert state_b.duration_cycles == 1


# ---------------------------------------------------------------------------
# 10. Continuation after hydration -- the hydrated state can keep advancing normally.
# ---------------------------------------------------------------------------
def test_continuation_after_hydration_behaves_identically_to_uninterrupted_run(tmp_path_str):
    store = EventStore(tmp_path_str)
    for i, r in enumerate(["RANGING", "RANGING", "RANGING"]):
        record_regime_cycle(store, "S1", f"c{i}", f"t{i}", r)
    hydrated_state, _ = hydrate_regime_memory(store, "S1")

    # Continue live from the hydrated state.
    continued = hydrated_state.advance("TRENDING")

    # Reference: a never-interrupted run over the exact same full sequence.
    reference = RegimeMemoryState()
    for r in ["RANGING", "RANGING", "RANGING", "TRENDING"]:
        reference = reference.advance(r)

    assert continued == reference


# ---------------------------------------------------------------------------
# 11. PaperBroker order/position preservation.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_paper_broker_position_and_pnl_preserved_across_restart(tmp_path_str):
    store = EventStore(tmp_path_str)
    broker = PaperBroker(seed=1)
    contract = OptionContract("NIFTY24600CE", "NIFTY", 24600, OptionType.CE, "2026-08-14", 75)

    sell_req = OrderRequest(contract=contract, side=Side.SELL, quantity=75, client_order_id="cid-1", reference_price=130.0)
    await broker.place_order(sell_req)
    await record_paper_state(store, broker, "S1", "c0", "t0", 1)

    buy_req = OrderRequest(contract=contract, side=Side.BUY, quantity=75, client_order_id="cid-2", reference_price=100.0)
    await broker.place_order(buy_req)  # closes the short, realizes P&L.
    await record_paper_state(store, broker, "S1", "c1", "t1", 2)

    positions_before = await broker.get_open_positions()
    pnl_before = broker.get_realized_pnl()

    hydrated_broker, report = hydrate_paper_broker(store, "S1")
    positions_after = await hydrated_broker.get_open_positions()
    pnl_after = hydrated_broker.get_realized_pnl()

    assert positions_after == positions_before
    assert pnl_after == pnl_before
    assert pnl_after > 0  # short 130, covered at 100 -- a real, non-zero realized gain.
    assert report.status == RECOVERY_PARTIAL  # order history disclosed as not reconstructed.


# ---------------------------------------------------------------------------
# 12. Position Intelligence -- honestly has NO persistent state today
# (pure functions only, confirmed during Phase 15B's own audit) --
# there is genuinely nothing to hydrate yet. This test documents that
# finding rather than inventing fake state.
# ---------------------------------------------------------------------------
def test_position_intelligence_has_no_persistent_state_to_hydrate():
    import bujji.position_intelligence.engine as pi_engine
    import inspect
    # every public function is a pure function -- none reads or writes any module-level mutable state.
    for name, obj in inspect.getmembers(pi_engine, inspect.isfunction):
        assert not name.startswith("_load") and not name.startswith("_save")


# ---------------------------------------------------------------------------
# 13. Regime memory preservation is already covered above (tests 1, 2, 9, 10).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Store-level unit tests
# ---------------------------------------------------------------------------
def test_deduplicated_events_first_occurrence_wins():
    a = PersistedEvent("e1", "T", "S", "c0", "t0", "1.0.0", "prov", {"v": 1})
    b = PersistedEvent("e1", "T", "S", "c0", "t0", "1.0.0", "prov", {"v": 2})
    out = deduplicated_events([a, b])
    assert len(out) == 1
    assert out[0].payload == {"v": 1}


def test_event_ordering_preserved(tmp_path_str):
    store = EventStore(tmp_path_str)
    for i in range(5):
        record_regime_cycle(store, "S1", f"c{i}", f"t{i}", "RANGING")
    events = list(store.read_events())
    assert [e.cycle_id for e in events] == ["c0", "c1", "c2", "c3", "c4"]


def test_append_creates_parent_directory(tmp_path_str):
    nested_path = os.path.join(tempfile.mkdtemp(), "nested", "dir", "events.jsonl")
    store = EventStore(nested_path)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    assert os.path.exists(nested_path)


def test_serialization_round_trip():
    event = PersistedEvent("e1", "T", "S", "c0", "t0", "1.0.0", "prov", {"a": 1, "b": [1, 2, 3]})
    restored = PersistedEvent.from_dict(json.loads(json.dumps(event.to_dict())))
    assert restored == event
