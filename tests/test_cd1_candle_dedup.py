"""C_D1 — duplicate/missing candle detection.

Detection only: a duplicate/stale candle is ignored outright (never
double-processed); a gap larger than expected is logged and surfaced on
`RuntimeStatus` for operator visibility. Neither backfills data nor halts
trading — this is observability, not remediation.
"""
import pytest

from bujji.core.enums import State
from bujji.broker.paper import PaperBroker
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg


@pytest.mark.asyncio
async def test_exact_duplicate_candle_is_ignored(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    candle = c(9, 15, 22000, 22010, 21990, 22005, vol=1000)
    await orch.on_candle(candle)
    await orch.on_candle(candle)  # Exact repeat — must be ignored.

    assert status.duplicate_candles_ignored == 1


@pytest.mark.asyncio
async def test_stale_earlier_candle_is_ignored(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))
    # An out-of-order/replayed earlier candle arrives after a later one.
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))

    assert status.duplicate_candles_ignored == 1


@pytest.mark.asyncio
async def test_duplicate_does_not_double_count_position_state(config, logger, tmp_path):
    """The capital-relevant proof: a duplicate candle while IN_POSITION must
    not double-increment excursion tracking or trigger a second reassessment."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    hold_candle = c(9, 25, 22079, 22150, 22078, 22149, vol=1000)
    await orch.on_candle(hold_candle)
    candles_held_after_first = orch._trade.position.candles_held  # noqa: SLF001

    await orch.on_candle(hold_candle)  # Exact repeat.
    candles_held_after_second = orch._trade.position.candles_held  # noqa: SLF001

    assert candles_held_after_second == candles_held_after_first
    assert status.duplicate_candles_ignored == 1


@pytest.mark.asyncio
async def test_normal_5_minute_cadence_does_not_flag_a_gap(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))

    assert status.last_candle_gap_seconds is None


@pytest.mark.asyncio
async def test_missed_candle_gap_is_detected(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    # The 9:20 candle never arrives (feed stall); 9:25 shows up next.
    await orch.on_candle(c(9, 25, 22079, 22150, 22078, 22149, vol=1000))

    assert status.last_candle_gap_seconds == pytest.approx(600.0)  # 10 minutes.


@pytest.mark.asyncio
async def test_duplicate_detection_survives_through_replay(config, logger, tmp_path):
    from bujji.replay.engine import ReplayEngine

    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    engine = ReplayEngine(config, logger)
    orch = engine.orchestrator
    await orch.startup()

    broker = engine._broker  # noqa: SLF001
    candle = c(9, 15, 22000, 22010, 21990, 22005, vol=1000)
    broker.set_market(candle.close)
    await orch.on_candle(candle)
    broker.set_market(candle.close)
    await orch.on_candle(candle)  # Same candle fed twice through replay.

    assert engine.status.duplicate_candles_ignored == 1
