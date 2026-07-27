"""Production Engineering Sprint 6 -- Observability Coverage Completion.

Proves the exit-side order_dispatch stage instrumentation added this
sprint actually fires during a real replay-driven exit, closing the gap
where Sprint 1's stage() wrapping only ever covered entry-side order
submission.
"""
import logging

import pytest

from bujji.replay.engine import ReplayEngine
from tests.conftest import c


def _cfg(config, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    return config


@pytest.mark.asyncio
async def test_order_dispatch_stage_fires_on_both_entry_and_exit(config, logger, tmp_path, caplog):
    """Regression: tests/test_tier1_replay.py::test_replay_normal_exit_still_works's
    exact scenario -- entry then a rule-based exit -- but this time
    asserting the order_dispatch STAGE (not just the trade) fires twice:
    once for entry (no direction tag) and once for exit (direction="exit")."""
    _cfg(config, tmp_path)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    with caplog.at_level(logging.INFO):
        engine = ReplayEngine(config, logger)
        result = await engine.run(candles)

    assert len(result.trades) == 1  # Unchanged behaviour.

    dispatch_starts = [r for r in caplog.records
                       if r.message == "pipeline_stage_start" and hasattr(r, "data")
                       and r.data.get("stage") == "order_dispatch"]
    entry_events = [r for r in dispatch_starts if "direction" not in r.data]
    exit_events = [r for r in dispatch_starts if r.data.get("direction") == "exit"]
    assert len(entry_events) == 1
    assert len(exit_events) == 1


@pytest.mark.asyncio
async def test_exit_order_dispatch_finish_carries_decision_id_for_lineage(config, logger, tmp_path, caplog):
    """The exit-side stage must carry the SAME decision_id as the entry
    that opened the position -- proving decision lineage survives the
    new instrumentation, unchanged."""
    _cfg(config, tmp_path)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    with caplog.at_level(logging.INFO):
        engine = ReplayEngine(config, logger)
        await engine.run(candles)

    exit_finish = next(
        r for r in caplog.records
        if r.message == "pipeline_stage_finish" and hasattr(r, "data")
        and r.data.get("stage") == "order_dispatch" and r.data.get("direction") == "exit"
    )
    assert exit_finish.data["decision_id"] == "DEC-20260705092000"
    assert exit_finish.data["outcome"] == "ok"
    assert "duration_ms" in exit_finish.data


@pytest.mark.asyncio
async def test_eod_square_off_exit_also_emits_order_dispatch_stage(config, logger, tmp_path, caplog):
    """The other exit path -- end_of_day() -> square_off() -> _do_exit()
    -- must also be covered, not just the rule-based exit."""
    _cfg(config, tmp_path)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 25, 22000, 22010, 21990, 22005, vol=1000),  # No exit signal fires.
    ]
    engine = ReplayEngine(config, logger)
    await engine.run(candles)
    assert engine.orchestrator.has_open_position()

    with caplog.at_level(logging.INFO):
        ok = await engine.orchestrator.end_of_day()
    assert ok
    assert not engine.orchestrator.has_open_position()

    exit_events = [r for r in caplog.records
                  if r.message == "pipeline_stage_start" and hasattr(r, "data")
                  and r.data.get("stage") == "order_dispatch" and r.data.get("direction") == "exit"]
    assert len(exit_events) == 1
