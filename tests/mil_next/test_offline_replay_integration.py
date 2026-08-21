"""End-to-end offline replay integration test -- must call
snapshot_builder.build_snapshot() exclusively, never hand-assemble a
MarketIntelligenceSnapshot."""
from datetime import datetime, timedelta, timezone

from bujji.mil_next.models import MarketDataInputs, MarketDataPoint, SourceHealth, TimeframeConfig
from bujji.mil_next.snapshot_builder import build_snapshot
from bujji.mil_next.snapshot_journal import MILSnapshotJournal


def _synthetic_session_fixture():
    """A small, self-contained, deterministic replay fixture -- an
    intraday price path with a clear uptrend, standing in for a real
    historical replay source. Real historical-fixture wiring (e.g. from
    bujji/replay/) is out of scope for this offline-laboratory phase."""
    base = datetime(2026, 7, 31, 9, 15, 0, tzinfo=timezone.utc)
    points = tuple(
        MarketDataPoint(event_time=base + timedelta(minutes=i), price=24800.0 + i * 2.5)
        for i in range(20)
    )
    return MarketDataInputs(
        session_id="REPLAY-SESSION-1",
        points_by_symbol={"NIFTY": points},
        source_health={"underlying_tick": SourceHealth(connected=True)},
        max_silence_ms={"underlying_tick": 600_000.0},
    )


def test_full_replay_produces_deterministic_snapshot(tmp_path):
    inputs = _synthetic_session_fixture()
    cutoff = datetime(2026, 7, 31, 9, 30, 0, tzinfo=timezone.utc)
    config = TimeframeConfig(config_version="replay-v1")

    def _run(receipt_offset_sec):
        return build_snapshot(
            session_id=inputs.session_id, cadence_id="CADENCE-REPLAY-1",
            decision_cutoff_event_time=cutoff, market_data_inputs=inputs,
            timeframe_config=config, calendar_sources=(), active_config_versions={"replay": "v1"},
            clock=lambda: cutoff + timedelta(seconds=receipt_offset_sec),
        )

    run1 = _run(1)
    run2 = _run(3)  # different wall-clock receipt time, same market data

    assert run1.content_hash == run2.content_hash
    assert run1.idempotency_key == run2.idempotency_key
    assert run1.posture in ("NORMAL", "REDUCED", "DEFINED_RISK_ONLY", "MANAGE_ONLY", "NO_TRADE")

    journal = MILSnapshotJournal(tmp_path / "replay.db")
    result1 = journal.record(run1)
    result2 = journal.record(run2)  # exact replay under the same key/hash
    assert result1 is not None
    assert result2 is None
