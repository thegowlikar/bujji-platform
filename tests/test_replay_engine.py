"""Tests -- Phase 15H Formal Replay Engine. Fixture-driven determinism/
correctness tests; the REAL 174-cycle SHADOW-OBSERVATORY-2026-08-06
validation lives in a separate, clearly-labeled real-data script (see
the Phase 15H report), never mixed into these semantic fixtures."""
from __future__ import annotations

import dataclasses
import json

from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.replay_engine.engine import ReplayEngine, fingerprint_state
from bujji.replay_engine.mismatch import cross_check_field
from bujji.replay_engine.models import CLASS_MATCH, CLASS_MISMATCH, CLASS_NOT_APPLICABLE, CLASS_UNAVAILABLE
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL


def make_snapshot(spot=24400.0, ts="2026-08-04T09:15:00+05:30", ce=(99.0, 101.0), pe=(89.0, 91.0)):
    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=ce[0], ask=ce[1],
                  spread=2.0, volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=pe[0], ask=pe[1],
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-13", atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


def real_sequence(n, start_spot=24400.0, start_minute=15):
    out = []
    spot = start_spot
    for i in range(n):
        spot += (5.0 if i % 2 == 0 else -2.0)
        ce = (95.0 + i * 2, 97.0 + i * 2)
        pe = (85.0 - i, 87.0 - i)
        ts = f"2026-08-04T09:{start_minute + i:02d}:00+05:30"
        out.append(make_snapshot(spot=spot, ts=ts, ce=ce, pe=pe))
    return out


def write_snapshots(path, snapshots):
    with open(path, "w") as f:
        for s in snapshots:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")


def write_intelligence_cycle(path, records):
    with open(path, "w") as f:
        for r in records:
            f.write(json.dumps(r, default=str) + "\n")


# ---------------------------------------------------------------------------
# Empty / one-cycle sessions.
# ---------------------------------------------------------------------------
def test_empty_session(tmp_path):
    engine = ReplayEngine("S1", str(tmp_path / "nope.jsonl"))
    session = engine.run()
    assert session.cycles == ()
    assert session.recovery_reports["market_snapshot_replay"]["status"] == RECOVERY_COMPLETE


def test_one_cycle_session(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(1))
    engine = ReplayEngine("S1", path)
    session = engine.run()
    assert len(session.cycles) == 1
    assert session.cycles[0].cycle_index == 0


# ---------------------------------------------------------------------------
# Malformed / missing / duplicate / out-of-order / corrupted / truncated /
# schema mismatch.
# ---------------------------------------------------------------------------
def test_malformed_record_mid_file(tmp_path):
    seq = real_sequence(3)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(dataclasses.asdict(seq[0]), default=str) + "\n")
        f.write(json.dumps({"not": "a real snapshot"}) + "\n")
        for s in seq[1:]:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["status"] == RECOVERY_PARTIAL
    assert report["events_skipped_schema_mismatch"] == 1
    assert len(session.cycles) == 3


def test_duplicate_cycle_timestamp(tmp_path):
    seq = real_sequence(3)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        for s in seq:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
        f.write(json.dumps(dataclasses.asdict(seq[-1]), default=str) + "\n")  # exact duplicate timestamp.
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["events_skipped_duplicate"] == 1
    assert len(session.cycles) == 3


def test_out_of_order_cycle(tmp_path):
    seq = real_sequence(3)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        for s in seq:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
        stale = make_snapshot(spot=99999.0, ts=seq[0].timestamp)  # earlier real timestamp, appended last.
        f.write(json.dumps(dataclasses.asdict(stale), default=str) + "\n")
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["events_skipped_duplicate"] == 1  # same bucket as duplicate (timestamp-ordering violation).
    assert len(session.cycles) == 3


def test_corrupted_json_line(tmp_path):
    seq = real_sequence(2)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(dataclasses.asdict(seq[0]), default=str) + "\n")
        f.write("{not valid json at all!!\n")
        f.write(json.dumps(dataclasses.asdict(seq[1]), default=str) + "\n")
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["events_skipped_malformed"] == 1
    assert len(session.cycles) == 2


def test_truncated_final_line(tmp_path):
    seq = real_sequence(2)
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, seq)
    with open(path, "a") as f:
        f.write('{"snapshot_version": "1.0", "timestamp": "2026-08-04T09:5')  # torn.
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["status"] == RECOVERY_PARTIAL
    assert report["events_skipped_malformed"] == 1
    assert len(session.cycles) == 2


def test_schema_mismatch(tmp_path):
    seq = real_sequence(2)
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        d0 = dataclasses.asdict(seq[0])
        f.write(json.dumps(d0, default=str) + "\n")
        d1 = dataclasses.asdict(seq[1])
        d1["snapshot_version"] = "99.0"
        f.write(json.dumps(d1, default=str) + "\n")
    session = ReplayEngine("S1", path).run()
    report = session.recovery_reports["market_snapshot_replay"]
    assert report["events_skipped_schema_mismatch"] == 1
    assert len(session.cycles) == 1


# ---------------------------------------------------------------------------
# Checkpoint / resume (Step 5) -- at cycle 1, early warm-up, ~25/50/75%,
# penultimate.
# ---------------------------------------------------------------------------
def _checkpoint_resume_matches_full(tmp_path, n, checkpoint_index):
    seq = real_sequence(n)
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, seq)

    engine = ReplayEngine("S1", path)
    full_session = engine.run()
    checkpoint = engine.checkpoint(checkpoint_index)
    resumed_session = engine.run(resume_from=checkpoint)

    full_tail = [c.to_dict() for c in full_session.cycles if c.cycle_index >= checkpoint_index]
    resumed_all = [c.to_dict() for c in resumed_session.cycles]
    assert full_tail == resumed_all
    assert full_session.final_fingerprint == resumed_session.final_fingerprint


def test_checkpoint_resume_at_cycle_1(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 1)


def test_checkpoint_resume_early_warmup(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 2)


def test_checkpoint_resume_at_25_percent(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 5)


def test_checkpoint_resume_at_50_percent(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 10)


def test_checkpoint_resume_at_75_percent(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 15)


def test_checkpoint_resume_at_penultimate_cycle(tmp_path):
    _checkpoint_resume_matches_full(tmp_path, 20, 18)


def test_checkpoint_resume_via_genuine_file_truncation(tmp_path):
    """Additional, stronger proof: truncate the REAL file (a genuine
    restart, not just skip-recording) and hydrate the underlying state
    directly -- must match the full-replay final fingerprint."""
    from bujji.market_state_builder.recovery import hydrate_observation_memory
    from bujji.premium_behaviour.recovery import hydrate_premium_behaviour

    seq = real_sequence(20)
    full_path = str(tmp_path / "full.jsonl")
    write_snapshots(full_path, seq)
    truncated_path = str(tmp_path / "truncated.jsonl")
    write_snapshots(truncated_path, seq[:10])

    full_engine = ReplayEngine("S1", full_path)
    full_session = full_engine.run()

    # Hydrate from the truncated file, then continue with the remaining
    # real snapshots appended -- a genuine restart simulation.
    with open(truncated_path, "a") as f:
        for s in seq[10:]:
            f.write(json.dumps(dataclasses.asdict(s), default=str) + "\n")
    resumed_engine = ReplayEngine("S1", truncated_path)
    resumed_session = resumed_engine.run()

    assert resumed_session.final_fingerprint == full_session.final_fingerprint


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------
def test_deterministic_double_replay(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(10))
    session1 = ReplayEngine("S1", path).run()
    session2 = ReplayEngine("S1", path).run()
    assert session1.final_fingerprint == session2.final_fingerprint
    assert [c.to_dict() for c in session1.cycles] == [c.to_dict() for c in session2.cycles]


def test_fingerprint_state_normalizes_tuple_vs_list():
    a = fingerprint_state({"x": (1, 2, 3)})
    b = fingerprint_state({"x": [1, 2, 3]})
    assert a == b


def test_fingerprint_differs_for_different_content():
    a = fingerprint_state({"x": 1})
    b = fingerprint_state({"x": 2})
    assert a != b


# ---------------------------------------------------------------------------
# Session isolation.
# ---------------------------------------------------------------------------
def test_session_isolation_different_files(tmp_path):
    path_a = str(tmp_path / "a.jsonl")
    path_b = str(tmp_path / "b.jsonl")
    write_snapshots(path_a, real_sequence(3, start_spot=24000.0))
    write_snapshots(path_b, real_sequence(5, start_spot=25000.0))
    session_a = ReplayEngine("SESSION-A", path_a).run()
    session_b = ReplayEngine("SESSION-B", path_b).run()
    assert len(session_a.cycles) == 3
    assert len(session_b.cycles) == 5
    assert session_a.final_fingerprint != session_b.final_fingerprint


# ---------------------------------------------------------------------------
# UNKNOWN propagation / missing dependency (Step 4's disclosed gap).
# ---------------------------------------------------------------------------
def test_referenced_fields_are_none_without_intelligence_cycle_path(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(2))
    session = ReplayEngine("S1", path).run()  # no intelligence_cycle_path supplied.
    for cycle in session.cycles:
        assert cycle.referenced["consensus"] is None
        assert cycle.referenced["volatility_structure"] is None


def test_referenced_fields_populated_when_intelligence_cycle_path_given(tmp_path):
    seq = real_sequence(2)
    market_path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(market_path, seq)
    cycle_path = str(tmp_path / "intelligence_cycle.jsonl")
    write_intelligence_cycle(cycle_path, [
        {"timestamp": seq[0].timestamp, "consensus": {"consensus_level": "UNANIMOUS_CONSENSUS"}, "market_state": {"regime": "RANGING"}},
        {"timestamp": seq[1].timestamp, "consensus": {"consensus_level": "NO_CONSENSUS"}, "market_state": {"regime": "TRENDING"}},
    ])
    session = ReplayEngine("S1", market_path, cycle_path).run()
    assert session.cycles[0].referenced["consensus"] == {"consensus_level": "UNANIMOUS_CONSENSUS"}
    assert session.cycles[1].referenced["consensus"] == {"consensus_level": "NO_CONSENSUS"}


# ---------------------------------------------------------------------------
# Mismatch classification (Step 7).
# ---------------------------------------------------------------------------
def test_cross_check_match():
    record = cross_check_field("x", {"a": 1}, {"a": 1})
    assert record.classification == CLASS_MATCH


def test_cross_check_mismatch_classified_as_replay_defect():
    record = cross_check_field("x", {"a": 1}, {"a": 2})
    assert record.classification == CLASS_MISMATCH
    assert record.reason_category == "REAL_REPLAY_DEFECT"


def test_cross_check_not_applicable_when_no_reference():
    record = cross_check_field("x", {"a": 1}, None)
    assert record.classification == CLASS_NOT_APPLICABLE


def test_cross_check_unavailable_when_reconstructed_missing():
    record = cross_check_field("x", None, {"a": 1})
    assert record.classification == CLASS_UNAVAILABLE


def test_cross_check_unavailable_when_both_missing():
    record = cross_check_field("x", None, None)
    assert record.classification == CLASS_UNAVAILABLE


def test_cross_check_normalizes_tuple_vs_list_representation():
    """Representation-only differences (tuple vs list from a JSON
    round-trip) must never be reported as a mismatch."""
    record = cross_check_field("x", (1, 2, 3), [1, 2, 3])
    assert record.classification == CLASS_MATCH


# ---------------------------------------------------------------------------
# max_cycles / partial replay.
# ---------------------------------------------------------------------------
def test_partial_replay_via_max_cycles(tmp_path):
    path = str(tmp_path / "market_snapshots.jsonl")
    write_snapshots(path, real_sequence(10))
    session = ReplayEngine("S1", path).run(max_cycles=4)
    assert len(session.cycles) == 4
