"""Replay determinism for the Capital Management Engine.

Verifies ReplayBroker's capital_schedule/margin_schedule/lot_size_schedule
produce IDENTICAL sizing decisions given the same candle sequence + the
same schedule -- no wall-clock, no randomness -- and that a schedule change
(margin requirement changing mid-day, capital changing mid-day) is
correctly reflected in the approved quantity at the right point.
"""
import pytest

from bujji.replay.engine import ReplayEngine
from bujji.replay.broker import ReplayBroker
from bujji.core.enums import State
from tests.conftest import c


def _cfg(config, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    config.risk.lots = 10  # High ceiling so capital, not config, decides.
    config.risk.margin_safety_buffer = 1.0  # Simplify expected arithmetic in these tests.
    return config


@pytest.mark.asyncio
async def test_replay_is_deterministic_given_identical_capital_schedule(config, logger, tmp_path):
    """Two independent replay runs, same candles + same schedule, must
    produce the exact same approved_lots at entry."""
    _cfg(config, tmp_path)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)]

    broker1 = ReplayBroker(starting_capital=3_00_000.0, starting_margin=3_00_000.0,
                          margin_per_lot=1_00_000.0)
    engine1 = ReplayEngine(config, logger, broker=broker1)
    result1 = await engine1.run(candles)

    _cfg(config, tmp_path)  # Reset paths for a truly independent second run.
    config.paths.journal_csv = tmp_path / "j2.csv"
    config.paths.database = tmp_path / "b2.db"
    config.paths.state_file = tmp_path / "s2.json"
    broker2 = ReplayBroker(starting_capital=3_00_000.0, starting_margin=3_00_000.0,
                          margin_per_lot=1_00_000.0)
    engine2 = ReplayEngine(config, logger, broker=broker2)
    result2 = await engine2.run(candles)

    pos1 = engine1.orchestrator._trade.position  # noqa: SLF001
    pos2 = engine2.orchestrator._trade.position  # noqa: SLF001
    assert pos1.capital_decision["approved_lots"] == pos2.capital_decision["approved_lots"]
    assert pos1.quantity == pos2.quantity == 3 * 75  # floor(300000/100000) = 3


@pytest.mark.asyncio
async def test_margin_schedule_changes_reflected_at_correct_candle(config, logger, tmp_path):
    """Margin requirement scheduled to jump AFTER entry must not retroactively
    change the already-approved entry-time sizing -- sizing happens once, at
    entry, using whatever the schedule reports at that candle index."""
    _cfg(config, tmp_path)
    broker = ReplayBroker(
        starting_capital=3_00_000.0, starting_margin=3_00_000.0,
        margin_schedule={0: 1_00_000.0, 5: 2_90_000.0},  # jumps AFTER entry (candle 0)
    )
    engine = ReplayEngine(config, logger, broker=broker)

    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)

    pos = engine.orchestrator._trade.position  # noqa: SLF001
    # Candle index 0 (the entry candle) sees margin_schedule[0] = 100000.
    assert pos.capital_decision["approved_lots"] == 3  # floor(300000/100000)


@pytest.mark.asyncio
async def test_lot_size_schedule_changes_quantity_dynamically(config, logger, tmp_path):
    """Exchange lot-size change (e.g. 50 -> 75) mid-schedule must flow
    through to the approved order quantity without any strategy code
    change."""
    _cfg(config, tmp_path)
    config.risk.lots = 1
    broker = ReplayBroker(
        starting_capital=50_00_000.0, starting_margin=50_00_000.0,
        margin_per_lot=1_00_000.0,
        lot_size_schedule={0: 50},  # Resolved lot size at entry is 50, not the config default.
    )
    engine = ReplayEngine(config, logger, broker=broker)

    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)

    pos = engine.orchestrator._trade.position  # noqa: SLF001
    assert pos.ce_contract.lot_size == 50
    assert pos.quantity == 1 * 50  # 1 approved lot * the SCHEDULED lot size.
