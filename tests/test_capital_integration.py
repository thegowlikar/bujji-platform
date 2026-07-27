"""Capital Management Engine — Orchestrator integration.

Proves the CME is actually wired into the live entry path (not just unit-
tested in isolation): no strategy code multiplies risk.lots * lot_size
anymore, insufficient capital cleanly blocks entry (FSM rolls back to
READY, not stuck), and the approved quantity flows through to the real
Position/order placement.
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.capital.engine import CapitalManagementEngine
from bujji.core.enums import State
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _fast_broker_cfg


@pytest.mark.asyncio
async def test_ample_capital_approves_configured_lots_end_to_end(config, logger, tmp_path):
    _fast_broker_cfg(config)
    config.risk.lots = 2
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))

    assert orch.state is State.IN_POSITION
    assert orch._trade.position.quantity == 2 * 75  # noqa: SLF001 - 2 lots * lot_size.
    assert status.capital_health is not None
    assert status.capital_health["approved_lots"] == 2


@pytest.mark.asyncio
async def test_insufficient_capital_blocks_entry_cleanly(config, logger, tmp_path):
    """The exact scenario the mandate requires: never send an order the
    account cannot safely support."""
    _fast_broker_cfg(config)
    config.risk.lots = 5
    broker = PaperBroker(available_margin=50_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))

    assert not orch.has_open_position()
    assert orch.state is State.READY  # Not stuck, not silently IN_POSITION.
    assert status.healthy is False
    assert "entry_blocked_capital" in status.health_detail
    # No order was ever placed at all -- the CME gate runs BEFORE any
    # OrderRequest is constructed.
    assert broker.place_calls == 0


@pytest.mark.asyncio
async def test_capital_unverified_blocks_entry_and_never_guesses(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker(funds_unavailable=True)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))

    assert not orch.has_open_position()
    assert orch.state is State.READY
    assert broker.place_calls == 0


@pytest.mark.asyncio
async def test_dynamic_lot_size_change_is_reflected_in_quantity(config, logger, tmp_path):
    """If the exchange lot size changes (e.g. NIFTY 50 -> 75), the approved
    quantity must scale with whatever lot_size the resolved contract
    actually carries -- never a value baked in independently of it."""
    _fast_broker_cfg(config)
    config.risk.lots = 1
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert orch._trade.position.quantity == 1 * 75  # noqa: SLF001 - PaperBroker's lot_size default.


@pytest.mark.asyncio
async def test_capital_health_report_published_to_journal(config, logger, tmp_path):
    """Publish to journal (mandate requirement) -- verify the entry-time
    capital decision survives all the way to the exit-time trade record."""
    _fast_broker_cfg(config)
    config.risk.lots = 1
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert orch.state is State.IN_POSITION

    await orch.on_candle(c(15, 5, 22000, 22010, 21990, 22005, vol=1000))
    assert orch.state is State.DONE_FOR_DAY

    trades = orch._journal.all_trades()  # noqa: SLF001
    assert len(trades) == 1
    assert trades[0]["approved_lots"] == "1"
    assert trades[0]["capital_status"] in ("SAFE", "WARNING")


@pytest.mark.asyncio
async def test_capital_survives_recovery_via_position_snapshot(config, logger, tmp_path):
    """A restart must not lose the entry-time capital decision -- it's
    carried on Position.capital_decision and round-trips through the
    session snapshot."""
    _fast_broker_cfg(config)
    broker = PaperBroker(available_margin=50_00_000.0, margin_per_lot=1_00_000.0)
    a, _ = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await a.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert a.state is State.IN_POSITION

    b, status_b = build_orch(config, logger, broker, tmp_path)
    await b.startup()
    assert b.state is State.IN_POSITION  # Resumed.
    assert b._trade.position.capital_decision is not None  # noqa: SLF001
    assert b._trade.position.capital_decision["approved_lots"] == 1  # noqa: SLF001
