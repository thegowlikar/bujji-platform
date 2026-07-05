import asyncio
from datetime import datetime

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side, State
from bujji.core.models import OptionContract, OrderRequest
from bujji.execution.engine import ExecutionEngine
from bujji.core.orchestrator import Orchestrator
from bujji.core.runtime_status import RuntimeStatus
from bujji.core.session_state import SessionStore
from bujji.journal.journal import TradeJournal
from bujji.signal.engine import SignalEngine
from bujji.trade.manager import TradeManager
from tests.conftest import c


def test_state_machine_transitions(logger):
    from bujji.core.state_machine import IllegalTransition, StateMachine

    fsm = StateMachine(logger)
    fsm.transition(State.READY)
    fsm.transition(State.CONFIRMED)
    fsm.transition(State.IN_POSITION)
    with pytest.raises(IllegalTransition):
        fsm.transition(State.READY)


@pytest.mark.asyncio
async def test_execution_idempotent_fill(config, logger):
    broker = PaperBroker()
    eng = ExecutionEngine(broker, config, logger)
    await eng.connect()
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "WEEKLY", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-1")
    r1 = await eng.submit_and_confirm(req)
    r2 = await eng.submit_and_confirm(req)  # Retry same client id.
    assert r1.is_filled and r2.broker_order_id == r1.broker_order_id


@pytest.mark.asyncio
async def test_end_to_end_paper(config, logger, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    broker = PaperBroker()
    status = RuntimeStatus()
    execn = ExecutionEngine(broker, config, logger)
    signal = SignalEngine(config, logger)
    trade = TradeManager(config, logger)
    journal = TradeJournal(config.paths.journal_csv, config.paths.database)
    store = SessionStore(config.paths.state_file)
    orch = Orchestrator(config, logger, signal, trade, execn, journal, store, status)

    await orch.startup()
    # ORB then a strong bullish breakout, then a VWAP loss forcing an exit.
    await orch.on_candle(c(9, 15, 100, 110, 90, 105))
    await orch.on_candle(c(9, 20, 111, 130, 110, 129))
    assert orch.state is State.IN_POSITION
    await orch.on_candle(c(9, 25, 128, 129, 95, 96))  # loses VWAP
    assert orch.state is State.DONE_FOR_DAY
    assert journal.all_trades()  # A trade was journaled.
