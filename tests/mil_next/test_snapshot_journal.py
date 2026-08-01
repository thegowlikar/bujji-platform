from datetime import datetime, timedelta, timezone

import pytest

from bujji.mil_next.canonical_hash import compute_content_hash
from bujji.mil_next.models import MarketDataInputs, SourceHealth, TimeframeConfig
from bujji.mil_next.snapshot_builder import build_snapshot
from bujji.mil_next.snapshot_journal import IdempotencyCollisionError, MILSnapshotJournal
from bujji.mil_next.models import MILSnapshotRevision


def _inputs():
    from bujji.mil_next.models import MarketDataPoint
    base = datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc)
    points = tuple(
        MarketDataPoint(event_time=base + timedelta(seconds=i * 5), price=100.0 + i)
        for i in range(5)
    )
    return MarketDataInputs(
        session_id="SESSION-1", points_by_symbol={"NIFTY": points},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )


def _snapshot(receipt_dt):
    cutoff = datetime(2026, 7, 31, 9, 20, 0, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="v1")
    return build_snapshot(
        "SESSION-1", "CADENCE-1", cutoff, _inputs(), config, (), {"x": "1"},
        clock=lambda: receipt_dt,
    )


def test_journal_records_and_reads(tmp_path):
    journal = MILSnapshotJournal(tmp_path / "mil.db")
    snap = _snapshot(datetime(2026, 7, 31, 9, 20, 1, tzinfo=timezone.utc))
    result = journal.record(snap)
    assert result is not None
    row = journal.read(snap.idempotency_key)
    assert row["content_hash"] == snap.content_hash


def test_exact_replay_is_noop(tmp_path):
    journal = MILSnapshotJournal(tmp_path / "mil.db")
    snap = _snapshot(datetime(2026, 7, 31, 9, 20, 1, tzinfo=timezone.utc))
    journal.record(snap)
    # Exact replay: same idempotency_key AND same content_hash (built from
    # a receipt time that keeps quality conclusions -- and therefore hash -- identical).
    snap_replay = _snapshot(datetime(2026, 7, 31, 9, 20, 3, tzinfo=timezone.utc))
    assert snap_replay.idempotency_key == snap.idempotency_key
    assert snap_replay.content_hash == snap.content_hash
    result = journal.record(snap_replay)
    assert result is None


def test_disagreeing_content_raises_collision(tmp_path):
    journal = MILSnapshotJournal(tmp_path / "mil.db")
    snap = _snapshot(datetime(2026, 7, 31, 9, 20, 1, tzinfo=timezone.utc))
    journal.record(snap)
    from dataclasses import replace
    forged = replace(snap, posture="DEFINED_RISK_ONLY")  # disagreeing content, SAME idempotency_key
    forged = replace(forged, content_hash=compute_content_hash(forged))  # recompute to reflect the real change
    assert forged.content_hash != snap.content_hash
    with pytest.raises(IdempotencyCollisionError):
        journal.record(forged)
    row = journal.read(snap.idempotency_key)
    assert row["content_hash"] == snap.content_hash  # original untouched


def test_revision_protocol_appends_without_mutating_original(tmp_path):
    journal = MILSnapshotJournal(tmp_path / "mil.db")
    snap = _snapshot(datetime(2026, 7, 31, 9, 20, 1, tzinfo=timezone.utc))
    journal.record(snap)
    from dataclasses import replace
    revised = replace(snap, posture="DEFINED_RISK_ONLY")
    revision = MILSnapshotRevision(
        original_idempotency_key=snap.idempotency_key,
        revision_id="REV-1",
        revision_reason="late tick correction applied",
        revised_at=datetime(2026, 7, 31, 9, 30, 0, tzinfo=timezone.utc),
        superseding_content_hash=revised.content_hash,
    )
    journal.record_revision(revision, revised)
    original_row = journal.read(snap.idempotency_key)
    assert original_row["content_hash"] == snap.content_hash  # unchanged
    revisions = journal.read_revisions(snap.idempotency_key)
    assert len(revisions) == 1
    assert revisions[0]["revision_id"] == "REV-1"


def test_revision_requires_non_empty_reason(tmp_path):
    journal = MILSnapshotJournal(tmp_path / "mil.db")
    snap = _snapshot(datetime(2026, 7, 31, 9, 20, 1, tzinfo=timezone.utc))
    journal.record(snap)
    revision = MILSnapshotRevision(
        original_idempotency_key=snap.idempotency_key, revision_id="REV-2",
        revision_reason="   ", revised_at=datetime(2026, 7, 31, 9, 30, 0, tzinfo=timezone.utc),
        superseding_content_hash="deadbeef",
    )
    with pytest.raises(ValueError):
        journal.record_revision(revision, snap)
