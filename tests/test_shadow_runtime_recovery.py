"""Tests -- Phase 15C Runtime Recovery Integration. FakeBroker only, no
live calls, no credentials. Deterministic clock/spot -> deterministic
regime readings, so an uninterrupted reference run and a
kill/restart/hydrate run can be compared for EXACT state equivalence,
not just "recovery didn't crash"."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from bujji.core.enums import OptionType, Side
from bujji.core.models import Candle, OptionContract
from bujji.shadow_runtime.recovery import count_persisted_cycles, recover_shadow_session
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
from bujji.state_persistence.models import (
    RECOVERY_COMPLETE, RECOVERY_PARTIAL, PersistedEvent,
)
from bujji.state_persistence.regime_memory import hydrate_regime_memory, record_regime_cycle
from bujji.state_persistence.store import EventStore

CE_CONTRACT = OptionContract("NIFTY24450CE", "NIFTY", 24450, OptionType.CE, "2026-08-04", 65)
PE_CONTRACT = OptionContract("NIFTY24450PE", "NIFTY", 24450, OptionType.PE, "2026-08-04", 65)
FIXED_NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


def clock():
    return FIXED_NOW


async def no_sleep(_seconds):
    return None


class FakeBroker:
    """Deterministic, static responses every cycle -- same shape used
    by test_intelligence_cycle_recorder.py. Real order methods are
    deliberately absent -- constructing a call to them would be an
    AttributeError, proving the runner never even tries."""

    def __init__(self):
        self.connect_calls = 0

    async def connect(self):
        self.connect_calls += 1

    async def get_quote(self, contract):
        return {"bid": 98.0, "ask": 100.0, "spread": 2.0}

    async def get_spot(self, underlying):
        return 24400.0

    async def get_vix(self):
        return {"level": 13.0, "prev_close": 13.5}

    async def get_option_chain(self, underlying, spot, strike_count=5):
        return [(24450.0, 12000.0, 15000.0)]

    async def get_futures_quote(self, underlying):
        return {"symbol": "NSE:NIFTY26AUGFUT", "ltp": 24650.0, "volume": 1000.0, "oi": None}

    async def get_recent_candles(self, underlying, minutes, count):
        out = []
        c = 24300.0
        for i in range(20):
            c += 5
            out.append(Candle(timestamp=FIXED_NOW, open=c - 5, high=c + 2, low=c - 7, close=c, volume=1000))
        return out


def make_runner(broker, tmp_path, session_id="OBS-RECOVERY-TEST", **overrides):
    defaults = dict(
        broker=broker, watchlist=[(CE_CONTRACT, Side.SELL), (PE_CONTRACT, Side.SELL)],
        storage_path=str(tmp_path / "quotes.jsonl"), session_id=session_id, clock=clock,
        max_cycles=2, max_consecutive_failures=2, sleep_seconds=0.0, sleep_fn=no_sleep,
        market_perception_enabled=True,
        market_snapshot_path=str(tmp_path / "market_snapshots.jsonl"),
        intelligence_snapshot_path=str(tmp_path / "intelligence_snapshots.jsonl"),
        intelligence_cycle_enabled=True,
        intelligence_cycle_path=str(tmp_path / "intelligence_cycle.jsonl"),
    )
    defaults.update(overrides)
    return ShadowSessionRunner(**defaults)


def _read_jsonl(path):
    with open(path) as f:
        return [json.loads(l) for l in f]


# ---------------------------------------------------------------------------
# 1. Clean startup, no persisted state -- recovery requested, genuinely
#    nothing to recover.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clean_startup_with_no_persisted_state(tmp_path):
    broker = FakeBroker()
    regime_store = str(tmp_path / "regime_events.jsonl")
    runner = make_runner(broker, tmp_path, max_cycles=2, regime_memory_event_store_path=regime_store)
    artifact = await runner.start()

    assert artifact.recovery_report is not None
    assert artifact.recovery_report["status"] == RECOVERY_COMPLETE
    assert artifact.recovery_report["events_discovered"] == 0
    records = _read_jsonl(tmp_path / "intelligence_cycle.jsonl")
    assert len(records) == 2


# ---------------------------------------------------------------------------
# 2. Recovery not requested at all -- byte-identical to pre-Phase-15C
#    behavior: recovery_report stays None.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recovery_not_requested_leaves_report_none(tmp_path):
    broker = FakeBroker()
    runner = make_runner(broker, tmp_path, max_cycles=2)  # regime_memory_event_store_path omitted
    artifact = await runner.start()
    assert artifact.recovery_report is None


# ---------------------------------------------------------------------------
# 3. Normal restart -- mid-session restart simulation. Compare an
#    uninterrupted N+M run against a killed-and-restarted N then M run.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_mid_session_restart_matches_uninterrupted_reference(tmp_path):
    session_id = "OBS-RESTART-TEST"

    # Reference: one continuous 5-cycle run.
    ref_dir = tmp_path / "reference"
    ref_dir.mkdir()
    ref_broker = FakeBroker()
    ref_runner = make_runner(
        ref_broker, ref_dir, session_id=session_id, max_cycles=5,
        regime_memory_event_store_path=str(ref_dir / "regime_events.jsonl"),
    )
    ref_artifact = await ref_runner.start()
    ref_records = _read_jsonl(ref_dir / "intelligence_cycle.jsonl")

    # Split: 2 cycles, "kill" (drop the runner object -- no in-process
    # state carries over), restart, 3 more cycles.
    split_dir = tmp_path / "split"
    split_dir.mkdir()
    intelligence_path = str(split_dir / "intelligence_cycle.jsonl")
    regime_store_path = str(split_dir / "regime_events.jsonl")

    broker1 = FakeBroker()
    runner1 = make_runner(
        broker1, split_dir, session_id=session_id, max_cycles=2,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    await runner1.start()
    del runner1  # simulate process death -- nothing carries over but the files on disk.

    broker2 = FakeBroker()
    runner2 = make_runner(
        broker2, split_dir, session_id=session_id, max_cycles=3,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    split_artifact2 = await runner2.start()

    split_records = _read_jsonl(intelligence_path)

    # Cycle/event count equivalence.
    assert len(split_records) == len(ref_records) == 5
    assert split_artifact2.recovery_report["status"] == RECOVERY_COMPLETE
    assert split_artifact2.recovery_report["events_discovered"] == 2  # the 2 pre-restart cycles.
    assert split_artifact2.recovery_report["events_replayed"] == 2

    # Regime memory state equivalence -- hydrate both final stores and compare.
    ref_state, _ = hydrate_regime_memory(EventStore(str(ref_dir / "regime_events.jsonl")), session_id)
    split_state, _ = hydrate_regime_memory(EventStore(regime_store_path), session_id)
    assert split_state == ref_state

    # Final persisted regime_memory snapshot (what the live recorder
    # itself computed and wrote) also matches, cycle for cycle.
    assert split_records[-1]["regime_memory"] == ref_records[-1]["regime_memory"]
    assert [r["regime_memory"]["duration_cycles"] for r in split_records] == \
           [r["regime_memory"]["duration_cycles"] for r in ref_records]

    # No cycle-ID collision or replay: 5 distinct cycle ids in the regime store.
    events = list(EventStore(regime_store_path).read_events())
    assert sorted(e.cycle_id for e in events) == [f"c{i}" for i in range(5)]


# ---------------------------------------------------------------------------
# 4. Restart with a partially written (torn) final event -- degrades to
#    RECOVERY_PARTIAL, does not crash the runner, still continues.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restart_with_torn_final_event_degrades_safely(tmp_path):
    session_id = "OBS-TORN-TEST"
    intelligence_path = str(tmp_path / "intelligence_cycle.jsonl")
    regime_store_path = str(tmp_path / "regime_events.jsonl")

    broker1 = FakeBroker()
    runner1 = make_runner(
        broker1, tmp_path, session_id=session_id, max_cycles=2,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    await runner1.start()

    with open(regime_store_path, "a") as f:
        f.write('{"event_id": "torn", "event_type": "REGIME_OBS')  # deliberately incomplete, no newline.

    broker2 = FakeBroker()
    runner2 = make_runner(
        broker2, tmp_path, session_id=session_id, max_cycles=1,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    artifact = await runner2.start()

    assert artifact.recovery_report["status"] == RECOVERY_PARTIAL
    assert artifact.recovery_report["events_skipped_malformed"] == 1
    assert artifact.recovery_report["events_replayed"] == 2  # the 2 valid pre-restart cycles.
    assert len(_read_jsonl(intelligence_path)) == 3  # session still continued -- 2 + 1 more.


# ---------------------------------------------------------------------------
# 5. Duplicate final event present at restart -- never double-applied.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_restart_with_duplicate_event_is_not_double_applied(tmp_path):
    session_id = "OBS-DUP-TEST"
    intelligence_path = str(tmp_path / "intelligence_cycle.jsonl")
    regime_store_path = str(tmp_path / "regime_events.jsonl")

    broker1 = FakeBroker()
    runner1 = make_runner(
        broker1, tmp_path, session_id=session_id, max_cycles=2,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    await runner1.start()

    store = EventStore(regime_store_path)
    original_events = list(store.read_events())
    store.append(original_events[-1])  # re-append the real last event verbatim -- same event_id.

    state_before, report_before = hydrate_regime_memory(store, session_id)
    assert report_before.events_skipped_duplicate == 1
    assert state_before.duration_cycles == 2  # NOT tripled/doubled.

    broker2 = FakeBroker()
    runner2 = make_runner(
        broker2, tmp_path, session_id=session_id, max_cycles=1,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    artifact = await runner2.start()
    assert artifact.recovery_report["events_skipped_duplicate"] == 1
    assert artifact.recovery_report["status"] == RECOVERY_COMPLETE  # a duplicate is not an error/malformed condition.


# ---------------------------------------------------------------------------
# 6. Malformed persistence record (valid JSON, wrong shape).
# ---------------------------------------------------------------------------
def test_malformed_record_is_skipped_at_recovery_layer(tmp_path):
    store_path = str(tmp_path / "regime_events.jsonl")
    store = EventStore(store_path)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    with open(store_path, "a") as f:
        f.write(json.dumps({"not": "a real event"}) + "\n")
    state, report, = hydrate_regime_memory(store, "S1")
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_malformed == 1


# ---------------------------------------------------------------------------
# 7. Schema mismatch present at restart.
# ---------------------------------------------------------------------------
def test_schema_mismatch_is_skipped_at_recovery_layer(tmp_path):
    store_path = str(tmp_path / "regime_events.jsonl")
    store = EventStore(store_path)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    store.append(PersistedEvent(
        event_id="future", event_type="REGIME_OBSERVED", session_id="S1", cycle_id="c1",
        timestamp="t1", schema_version="99.0.0", provenance="test", payload={"regime": "TRENDING"},
    ))
    state, report = hydrate_regime_memory(store, "S1")
    assert report.events_skipped_schema_mismatch == 1
    assert state.current_regime == "RANGING"


# ---------------------------------------------------------------------------
# 8. Missing component state (regime store never created) -- cycle
#    numbering must still resume correctly from real intelligence_cycle
#    history, independent of whether regime recovery found anything.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_regime_component_state_still_resumes_cycle_numbering(tmp_path):
    session_id = "OBS-MISSING-COMPONENT"
    intelligence_path = str(tmp_path / "intelligence_cycle.jsonl")

    broker1 = FakeBroker()
    # First run WITHOUT regime recovery enabled at all -- no regime store ever created.
    runner1 = make_runner(
        broker1, tmp_path, session_id=session_id, max_cycles=2, intelligence_cycle_path=intelligence_path,
    )
    await runner1.start()
    assert not (tmp_path / "regime_events.jsonl").exists()

    # Second run turns regime recovery ON for the first time -- the
    # store is missing entirely, but intelligence_cycle_path already has
    # 2 real cycles; cycle numbering must resume at c2, not collide with c0/c1.
    broker2 = FakeBroker()
    regime_store_path = str(tmp_path / "regime_events.jsonl")
    runner2 = make_runner(
        broker2, tmp_path, session_id=session_id, max_cycles=1,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    artifact = await runner2.start()
    assert artifact.recovery_report["status"] == RECOVERY_COMPLETE
    assert artifact.recovery_report["events_discovered"] == 0

    events = list(EventStore(regime_store_path).read_events())
    assert [e.cycle_id for e in events] == ["c2"]  # correctly resumed, no collision with c0/c1.


# ---------------------------------------------------------------------------
# 9. Partially recoverable session -- some malformed, some valid; valid
#    ones still applied at the runtime layer.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_partially_recoverable_session_still_replays_valid_events(tmp_path):
    session_id = "OBS-PARTIAL-TEST"
    regime_store_path = str(tmp_path / "regime_events.jsonl")
    intelligence_path = str(tmp_path / "intelligence_cycle.jsonl")

    store = EventStore(regime_store_path)
    record_regime_cycle(store, session_id, "c0", "t0", "RANGING")
    record_regime_cycle(store, session_id, "c1", "t1", "RANGING")
    with open(regime_store_path, "a") as f:
        f.write(json.dumps({"garbage": True}) + "\n")

    # intelligence_cycle_path needs 2 real prior lines too, so cycle
    # numbering and regime-event count agree (as a real prior runner
    # would have produced).
    with open(intelligence_path, "w") as f:
        f.write(json.dumps({"stub": "cycle-0"}) + "\n")
        f.write(json.dumps({"stub": "cycle-1"}) + "\n")

    broker = FakeBroker()
    runner = make_runner(
        broker, tmp_path, session_id=session_id, max_cycles=1,
        intelligence_cycle_path=intelligence_path, regime_memory_event_store_path=regime_store_path,
    )
    artifact = await runner.start()
    assert artifact.recovery_report["status"] == RECOVERY_PARTIAL
    assert artifact.recovery_report["events_replayed"] == 2
    assert artifact.recovery_report["events_skipped_malformed"] == 1


# ---------------------------------------------------------------------------
# 10. Restart immediately after a real state transition -- previous_regime
#     must be preserved exactly, not lost or blended.
# ---------------------------------------------------------------------------
def test_restart_immediately_after_a_transition_preserves_previous_regime(tmp_path):
    store_path = str(tmp_path / "regime_events.jsonl")
    store = EventStore(store_path)
    record_regime_cycle(store, "S1", "c0", "t0", "RANGING")
    record_regime_cycle(store, "S1", "c1", "t1", "RANGING")
    record_regime_cycle(store, "S1", "c2", "t2", "TRENDING")  # the transition itself, cycle 1 of new regime.

    state, report = hydrate_regime_memory(store, "S1")
    assert state.current_regime == "TRENDING"
    assert state.previous_regime == "RANGING"
    assert state.duration_cycles == 1
    assert report.status == RECOVERY_COMPLETE


# ---------------------------------------------------------------------------
# 11 & 12. PaperBroker / Position Intelligence restart, and multiple
# historical sessions -- documented findings, not fabricated integration.
# ---------------------------------------------------------------------------
def test_paper_broker_is_not_wired_into_shadow_runtime_today():
    """Honest audit finding (Phase 15C Step 1): ShadowSessionRunner
    never constructs or references a PaperBroker anywhere -- confirmed
    by source inspection, not assumed. PaperBroker restart is already
    fully covered at the state_persistence layer (Phase 15B, test 11 in
    test_state_persistence.py); there is no runtime-level PaperBroker
    lifecycle to restart yet, so no such integration is faked here."""
    import inspect
    from bujji.shadow_runtime import shadow_session_runner as mod
    source = inspect.getsource(mod)
    assert "PaperBroker" not in source


def test_position_intelligence_is_not_wired_into_shadow_runtime_today():
    """Same honest-finding pattern as above, for Position Intelligence --
    it has no persistent state at all (established in Phase 15B), and
    is not called from the runtime either."""
    import inspect
    from bujji.shadow_runtime import shadow_session_runner as mod
    source = inspect.getsource(mod)
    assert "position_intelligence" not in source


def test_multiple_historical_sessions_are_isolated_at_runtime_recovery(tmp_path):
    store_path = str(tmp_path / "regime_events.jsonl")
    store = EventStore(store_path)
    record_regime_cycle(store, "SESSION-A", "c0", "t0", "RANGING")
    record_regime_cycle(store, "SESSION-A", "c1", "t1", "RANGING")
    record_regime_cycle(store, "SESSION-B", "c0", "t0", "TRENDING")

    state_a, _, report_a = recover_shadow_session(None, store_path, "SESSION-A")
    state_b, _, report_b = recover_shadow_session(None, store_path, "SESSION-B")
    assert state_a.current_regime == "RANGING"
    assert state_a.duration_cycles == 2
    assert state_b.current_regime == "TRENDING"
    assert state_b.duration_cycles == 1
    assert report_a.events_discovered == 2
    assert report_b.events_discovered == 1


# ---------------------------------------------------------------------------
# count_persisted_cycles / recover_shadow_session unit coverage
# ---------------------------------------------------------------------------
def test_count_persisted_cycles_zero_for_missing_file(tmp_path):
    assert count_persisted_cycles(str(tmp_path / "nope.jsonl")) == 0


def test_count_persisted_cycles_zero_for_none_path():
    assert count_persisted_cycles(None) == 0


def test_recover_shadow_session_report_none_when_not_requested(tmp_path):
    state, next_idx, report = recover_shadow_session(None, None, "S1")
    assert report is None
    assert next_idx == 0
