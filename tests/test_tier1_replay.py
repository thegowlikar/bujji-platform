"""Replay-based Tier 1 tests.

Verify the capital-protection paths behave correctly when candles flow through
the *exact* live decision engine used in production (via the Replay Engine),
not just through isolated unit calls.
"""
import pytest

from bujji.core.enums import State
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
async def test_replay_normal_exit_still_works(config, logger, tmp_path):
    """Regression: the refactored exit path still closes and journals a trade."""
    _cfg(config, tmp_path)
    candles = [
        c(9, 15, 22000, 22010, 21990, 22005, vol=1000),   # ORB
        c(9, 20, 22006, 22080, 22005, 22079, vol=1000),   # breakout -> enter
        c(9, 25, 22078, 22079, 21950, 21951, vol=1000),   # lose VWAP -> exit
    ]
    engine = ReplayEngine(config, logger)
    result = await engine.run(candles)
    assert result.final_state == "DONE_FOR_DAY"
    assert len(result.trades) == 1


@pytest.mark.asyncio
async def test_replay_eod_square_off_when_position_left_open(config, logger, tmp_path):
    """C2 via replay: a position that never triggers a rule-based exit is still
    force-flattened at end of day — no candle-driven exit required."""
    _cfg(config, tmp_path)
    # ORB + breakout only; the trend stays intact so no exit signal fires.
    candles = [
        c(9, 15, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 20, 22006, 22080, 22005, 22079, vol=1000),
        c(9, 25, 22079, 22150, 22078, 22149, vol=1000),   # keeps trending up
    ]
    engine = ReplayEngine(config, logger)
    result = await engine.run(candles)
    orch = engine.orchestrator
    assert orch.state is State.IN_POSITION      # still holding after replay.
    assert result.final_state == "IN_POSITION"

    # End-of-day enforcement flattens it deterministically.
    ok = await orch.end_of_day()
    assert ok and orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()
    assert engine._journal.all_trades()          # exit journaled. noqa: SLF001
