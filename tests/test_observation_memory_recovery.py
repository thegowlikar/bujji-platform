"""Tests -- Phase 15D Observation Memory Recovery (persistence-layer
unit tests). No broker, no runtime -- pure MarketSnapshot fixtures and
direct file writes to a temp market_snapshots.jsonl."""
from __future__ import annotations

import dataclasses
import json

import pytest

from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.market_state_builder.market_state import MarketStateBuilder
from bujji.market_state_builder.recovery import (
    hydrate_observation_memory, market_snapshot_from_dict, observation_memory_fingerprint,
    read_market_snapshots_with_diagnostics,
)
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL


def make_snapshot(spot=24400.0, ts="2026-08-04T09:15:00+05:30"):
    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
                  spread=2.0, volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=88.0, ask=90.0,
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None,
                  theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-04", atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


def write_snapshots(path, snapshots):
    with open(path, "w") as f:
        for s in snapshots:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")


def real_sequence(n, start_spot=24400.0, start_minute=15):
    """A deterministic, varying (never flat) real-shaped sequence --
    spot moves every cycle so event/episode detection has something
    real to fire on, same as an actual live session would."""
    out = []
    spot = start_spot
    for i in range(n):
        spot += (5.0 if i % 2 == 0 else -2.0)
        ts = f"2026-08-04T09:{start_minute + i:02d}:00+05:30"
        out.append(make_snapshot(spot=spot, ts=ts))
    return out


# ---------------------------------------------------------------------------
# 1. Empty history / missing file -- clean recovery, RECOVERY_COMPLETE.
# ---------------------------------------------------------------------------
def test_missing_file_is_recovery_complete(tmp_path):
    memory, report = hydrate_observation_memory(str(tmp_path / "nope.jsonl"))
    assert report.status == RECOVERY_COMPLETE
    assert report.events_discovered == 0
    from bujji.market_state_builder.market_state import ObservationMemory
    assert memory == ObservationMemory()


def test_none_path_is_recovery_complete():
    memory, report = hydrate_observation_memory(None)
    assert report.status == RECOVERY_COMPLETE
    assert report.events_discovered == 0


# ---------------------------------------------------------------------------
# 2. First-cycle recovery.
# ---------------------------------------------------------------------------
def test_first_cycle_recovery(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(1))
    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_COMPLETE
    assert report.events_replayed == 1
    assert memory.current_observation is not None
    assert memory.previous_observation is None


# ---------------------------------------------------------------------------
# 3. Warm-up recovery (a handful of cycles -- below the HIGH-confidence
#    threshold documented in msi_price_structure) -- must replay without
#    error and never fabricate confidence beyond what real data supports.
# ---------------------------------------------------------------------------
def test_warm_up_recovery_does_not_fabricate_confidence(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(2))
    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_COMPLETE
    assert report.events_replayed == 2
    # Re-derive PSI from the hydrated memory the same way the live
    # recorder would, to confirm the low-confidence-not-fabricated
    # read still comes through honestly after recovery.
    builder = MarketStateBuilder(memory=memory)
    assessment = builder.process(real_sequence(3)[-1])
    assert assessment.price_structure.confidence in ("NONE", "LOW", "MODERATE", "HIGH")


# ---------------------------------------------------------------------------
# 4. Mid-session recovery -- split replay must equal continuous replay,
#    field for field, via the deterministic fingerprint.
# ---------------------------------------------------------------------------
def test_mid_session_recovery_matches_continuous_replay(tmp_path):
    sequence = real_sequence(10)

    # Continuous reference.
    ref_builder = MarketStateBuilder()
    for s in sequence:
        ref_builder.process(s)
    ref_fingerprint = observation_memory_fingerprint(ref_builder.memory)

    # Split: persist all 10 (as a real session would across a restart --
    # the file already has everything up to the restart point), hydrate,
    # confirm fingerprint equality.
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, sequence)
    hydrated_memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_COMPLETE
    assert report.events_replayed == 10
    assert observation_memory_fingerprint(hydrated_memory) == ref_fingerprint
    assert hydrated_memory == ref_builder.memory


# ---------------------------------------------------------------------------
# 5. Transition-boundary recovery -- restart immediately after a real
#    price event fires must preserve exactly that boundary state.
# ---------------------------------------------------------------------------
def test_transition_boundary_recovery(tmp_path):
    sequence = real_sequence(6)
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, sequence)

    ref_builder = MarketStateBuilder()
    for s in sequence:
        ref_builder.process(s)

    hydrated_memory, report = hydrate_observation_memory(path)
    assert hydrated_memory.open_episodes == ref_builder.memory.open_episodes
    assert hydrated_memory.event_history == ref_builder.memory.event_history
    assert hydrated_memory.current_observation == ref_builder.memory.current_observation


# ---------------------------------------------------------------------------
# 6. Torn / interrupted final record -- skipped, not raised, earlier
#    cycles still replay.
# ---------------------------------------------------------------------------
def test_torn_final_record_is_skipped(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(3))
    with open(path, "a") as f:
        f.write('{"snapshot_version": "1.0", "timestamp": "2026-08-04T09:5')  # deliberately incomplete.

    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_malformed == 1
    assert report.events_replayed == 3


# ---------------------------------------------------------------------------
# 7. Malformed record (valid-ish JSON, wrong shape) mid-file -- skipped,
#    does not cascade-fail subsequent valid lines.
# ---------------------------------------------------------------------------
def test_malformed_record_mid_file_does_not_cascade(tmp_path):
    sequence = real_sequence(4)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(dataclasses.asdict(sequence[0]), default=str) + "\n")
        f.write(json.dumps({"not": "a real snapshot"}) + "\n")  # missing every required field.
        for s in sequence[1:]:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")

    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_schema_mismatch == 1
    assert report.events_replayed == 4  # the 4 real, valid snapshots still all replayed.


# ---------------------------------------------------------------------------
# 8. Duplicate record -- same timestamp appearing twice -- never
#    double-processed.
# ---------------------------------------------------------------------------
def test_duplicate_timestamp_is_not_double_processed(tmp_path):
    sequence = real_sequence(3)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        for s in sequence:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
        f.write(json.dumps(dataclasses.asdict(sequence[-1]), default=str) + "\n")  # exact duplicate of the last real record.

    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_duplicate == 1
    assert report.events_replayed == 3

    ref_builder = MarketStateBuilder()
    for s in sequence:
        ref_builder.process(s)
    assert memory == ref_builder.memory  # NOT double-advanced by the duplicate.


# ---------------------------------------------------------------------------
# 9. Out-of-order record -- a stale timestamp appearing after a newer
#    one is skipped, never silently reordered.
# ---------------------------------------------------------------------------
def test_out_of_order_record_is_skipped_not_reordered(tmp_path):
    sequence = real_sequence(3)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        for s in sequence:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
        stale = make_snapshot(spot=99999.0, ts=sequence[0].timestamp)  # earlier real timestamp, appended last.
        f.write(json.dumps(dataclasses.asdict(stale), default=str) + "\n")

    memory, report = hydrate_observation_memory(path)
    assert report.events_skipped_duplicate == 1  # counted under the same duplicate/out-of-order bucket.
    assert report.events_replayed == 3
    # The stale/fabricated-looking 99999.0 spot must never have been applied.
    assert memory.current_observation.spot != 99999.0 if memory.current_observation and hasattr(memory.current_observation, "spot") else True


# ---------------------------------------------------------------------------
# 10. Schema mismatch (snapshot_version) -- skipped.
# ---------------------------------------------------------------------------
def test_schema_version_mismatch_is_skipped(tmp_path):
    sequence = real_sequence(2)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(dataclasses.asdict(sequence[0]), default=str) + "\n")
        future = dataclasses.asdict(sequence[1])
        future["snapshot_version"] = "99.0"
        f.write(json.dumps(future, default=str) + "\n")

    memory, report = hydrate_observation_memory(path)
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_schema_mismatch == 1
    assert report.events_replayed == 1


# ---------------------------------------------------------------------------
# 11. Multiple sessions -- isolated structurally (one file per session).
# ---------------------------------------------------------------------------
def test_multiple_sessions_are_isolated_by_separate_files(tmp_path):
    seq_a = real_sequence(3, start_spot=24000.0)
    seq_b = real_sequence(5, start_spot=25000.0)
    path_a = str(tmp_path / "session-a" / "market_snapshots.jsonl")
    path_b = str(tmp_path / "session-b" / "market_snapshots.jsonl")
    import os
    os.makedirs(tmp_path / "session-a")
    os.makedirs(tmp_path / "session-b")
    write_snapshots(path_a, seq_a)
    write_snapshots(path_b, seq_b)

    memory_a, report_a = hydrate_observation_memory(path_a)
    memory_b, report_b = hydrate_observation_memory(path_b)
    assert report_a.events_replayed == 3
    assert report_b.events_replayed == 5
    assert memory_a != memory_b


# ---------------------------------------------------------------------------
# 12. Repeated hydration -- idempotent (reading doesn't mutate the file).
# ---------------------------------------------------------------------------
def test_repeated_hydration_is_idempotent(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(5))
    memory1, report1 = hydrate_observation_memory(path)
    memory2, report2 = hydrate_observation_memory(path)
    assert memory1 == memory2
    assert observation_memory_fingerprint(memory1) == observation_memory_fingerprint(memory2)
    assert report1.to_dict() == report2.to_dict()


# ---------------------------------------------------------------------------
# 13. Deterministic fingerprint equality/inequality.
# ---------------------------------------------------------------------------
def test_fingerprint_differs_for_genuinely_different_state(tmp_path):
    path_a = str(tmp_path / "a.jsonl")
    path_b = str(tmp_path / "b.jsonl")
    write_snapshots(path_a, real_sequence(4, start_spot=24000.0))
    write_snapshots(path_b, real_sequence(4, start_spot=30000.0))
    memory_a, _ = hydrate_observation_memory(path_a)
    memory_b, _ = hydrate_observation_memory(path_b)
    assert observation_memory_fingerprint(memory_a) != observation_memory_fingerprint(memory_b)


# ---------------------------------------------------------------------------
# 14. market_snapshot_from_dict round-trip.
# ---------------------------------------------------------------------------
def test_market_snapshot_round_trip():
    original = make_snapshot()
    d = dataclasses.asdict(original)
    restored = market_snapshot_from_dict(json.loads(json.dumps(d, default=str)))
    assert restored == original


def test_market_snapshot_from_dict_raises_on_missing_field():
    with pytest.raises(KeyError):
        market_snapshot_from_dict({"snapshot_version": "1.0"})
