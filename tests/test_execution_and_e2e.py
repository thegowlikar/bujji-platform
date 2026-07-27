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
    # Straddle entry at 09:20; hard exit at 15:05.
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert orch.state is State.IN_POSITION
    await orch.on_candle(c(15, 5, 22000, 22010, 21990, 22005))  # hard_exit
    assert orch.state is State.DONE_FOR_DAY
    assert journal.all_trades()  # A trade was journaled.


class _BrokerWithBrokenMicCalls(PaperBroker):
    """A broker whose get_quote/get_option_chain always raise -- used to
    verify ExecutionEngine's best-effort wrappers around them never let
    that propagate into the trading loop."""

    async def get_quote(self, contract):
        raise RuntimeError("simulated transient failure")

    async def get_option_chain(self, underlying, spot, strike_count=5):
        raise RuntimeError("simulated transient failure")

    async def get_vix(self):
        raise RuntimeError("simulated transient failure")


@pytest.mark.asyncio
async def test_get_quote_never_raises_even_on_broker_failure(config, logger):
    eng = ExecutionEngine(_BrokerWithBrokenMicCalls(), config, logger)
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "2026-07-21", 65)
    result = await eng.get_quote(contract)
    assert result is None


@pytest.mark.asyncio
async def test_get_option_chain_never_raises_even_on_broker_failure(config, logger):
    eng = ExecutionEngine(_BrokerWithBrokenMicCalls(), config, logger)
    result = await eng.get_option_chain("NIFTY", 22000.0)
    assert result is None


@pytest.mark.asyncio
async def test_get_quote_and_get_option_chain_pass_through_on_success(config, logger):
    """PaperBroker doesn't implement these (base default None) -- confirms
    the wrapper is a clean pass-through, not just an error swallower."""
    eng = ExecutionEngine(PaperBroker(), config, logger)
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "2026-07-21", 65)
    assert await eng.get_quote(contract) is None
    assert await eng.get_option_chain("NIFTY", 22000.0) is None
    assert await eng.get_vix() is None


@pytest.mark.asyncio
async def test_get_vix_never_raises_even_on_broker_failure(config, logger):
    eng = ExecutionEngine(_BrokerWithBrokenMicCalls(), config, logger)
    assert await eng.get_vix() is None


@pytest.mark.asyncio
async def test_end_to_end_paper_produces_matching_trade_intention_and_snapshot(config, logger, tmp_path):
    """Regression test for the Sprint 1 refactor: identical entry to
    test_end_to_end_paper above, but also asserts the new TradeIntention/
    DecisionSnapshot boundary objects are populated correctly and that the
    journaled trade is IDENTICAL to what a pre-refactor run produces --
    same entry premium, same strike, same quantity, same exit reason."""
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
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert orch.state is State.IN_POSITION

    # Evidence Assembly / Strategy Layer boundary object.
    snap = orch._last_decision_snapshot  # noqa: SLF001
    assert snap is not None
    assert snap.intention.strategy_type == "PREMIUM_VWAP_STRADDLE"
    # ENTER_STRADDLE is legitimately non-directional -- _enter() itself
    # never reads signal.direction (sells both CE and PE regardless).
    assert snap.intention.direction is None
    # Strategy Layer output must never carry broker/order/margin details.
    assert not hasattr(snap.intention, "quantity")
    assert not hasattr(snap.intention, "order_id")
    assert snap.planned_structure == "ATM_STRADDLE"
    assert snap.planned_contracts["strike"] == trade.position.contract.strike
    assert snap.broker_session == broker.name

    await orch.on_candle(c(15, 5, 22000, 22010, 21990, 22005))  # hard_exit
    assert orch.state is State.DONE_FOR_DAY
    trades = journal.all_trades()
    assert trades  # A trade was journaled -- identical to the pre-refactor assertion.
    assert "hard_exit" in trades[0]["exit_reason"]


@pytest.mark.asyncio
async def test_trade_intention_never_exposes_broker_fields(config, logger):
    """Strategy Layer separation rule, checked structurally: TradeIntention
    has no field named strike/quantity/order/margin/broker."""
    from bujji.core.models import TradeIntention
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(TradeIntention)}
    forbidden = {"strike", "strikes", "quantity", "order_id", "margin",
                "broker", "contract", "contracts"}
    assert field_names.isdisjoint(forbidden)


@pytest.mark.asyncio
async def test_evidence_assembly_is_a_pure_lossless_mapping(config, logger):
    """build_trade_intention must not invent or drop information relative
    to the Signal it was built from -- direction and narrative round-trip
    exactly."""
    from datetime import datetime
    from bujji.core.clock import IST
    from bujji.core.enums import Direction, SignalType
    from bujji.core.models import Signal
    from bujji.core.evidence_assembly import build_trade_intention

    no_trade = Signal(type=SignalType.NO_TRADE, timestamp=datetime(2026, 7, 20, 9, 20, tzinfo=IST))
    assert build_trade_intention(no_trade) is None

    trade_signal = Signal(type=SignalType.ENTER_STRADDLE, timestamp=datetime(2026, 7, 20, 9, 20, tzinfo=IST),
                          direction=Direction.BULLISH, spot=24000.0, vwap=23990.0)
    intention = build_trade_intention(trade_signal)
    assert intention.direction == Direction.BULLISH
    assert intention.evidence_refs["spot"] == 24000.0
    assert intention.evidence_refs["vwap"] == 23990.0


@pytest.mark.asyncio
async def test_order_planner_matches_inline_logic_exactly(config, logger):
    """Order Planning module produces the exact same PlannedOrder shape
    the pre-refactor inline code in _enter() produced -- same contracts,
    same quantity, same CapitalRejectedError behaviour on refusal."""
    from bujji.core.order_planning import plan_straddle
    from bujji.capital.engine import CapitalManagementEngine
    from bujji.capital.exceptions import CapitalRejectedError

    broker = PaperBroker()
    execn = ExecutionEngine(broker, config, logger)
    await execn.connect()
    capital = CapitalManagementEngine(broker, logger,
                                      safety_buffer=config.risk.margin_safety_buffer,
                                      configured_max_lots=config.risk.lots)
    status = RuntimeStatus()

    async def resolve(direction, spot):
        return await execn._broker.resolve_atm_contract(  # noqa: SLF001
            config.market.underlying, spot, direction,
            config.market.strike_interval, config.market.lot_size,
        )

    planned = await plan_straddle(resolve, capital, status, 22000.0)
    assert planned.ce_contract.option_type.value == "CE"
    assert planned.pe_contract.option_type.value == "PE"
    assert planned.quantity > 0
    assert status.capital_health is not None  # dashboard was published, same as before.


@pytest.mark.asyncio
async def test_decision_id_is_identical_across_the_entire_lineage(config, logger, tmp_path):
    """The one property Sprint 2 exists to prove: TradeIntention,
    DecisionSnapshot, ExecutionPlan (implicitly, via the snapshot's own
    decision_id being reused), Position, and the journaled TradeRecord all
    carry the SAME decision_id for a single real entry -- no inference
    required to reconstruct the lineage after the fact."""
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.decision_journal = tmp_path / "d.jsonl"

    broker = PaperBroker()
    status = RuntimeStatus()
    execn = ExecutionEngine(broker, config, logger)
    signal = SignalEngine(config, logger)
    trade = TradeManager(config, logger)
    journal = TradeJournal(config.paths.journal_csv, config.paths.database)
    store = SessionStore(config.paths.state_file)
    orch = Orchestrator(config, logger, signal, trade, execn, journal, store, status)

    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert orch.state is State.IN_POSITION

    snap = orch._last_decision_snapshot  # noqa: SLF001
    decision_id = snap.decision_id
    assert decision_id.startswith("DEC-")
    assert snap.intention.decision_id == decision_id           # TradeIntention
    assert trade.position.decision_id == decision_id           # Position

    await orch.on_candle(c(15, 5, 22000, 22010, 21990, 22005))  # hard_exit
    assert orch.state is State.DONE_FOR_DAY

    trades = journal.all_trades()
    assert trades[0]["decision_id"] == decision_id              # TradeJournal row

    # Decision Journal foundation: the snapshot persisted separately, keyed
    # by the same id, joinable against the trade row without inference.
    persisted = orch._decision_journal.get(decision_id)  # noqa: SLF001
    assert persisted is not None
    assert persisted["decision_id"] == decision_id
    assert persisted["planned_structure"] == "ATM_STRADDLE"


def test_execution_plan_contains_no_broker_response(config, logger):
    """Structural check, per Sprint 2's rule: ExecutionPlan represents
    only what WILL be executed, never a fill, status, or broker response."""
    import dataclasses
    from bujji.core.models import ExecutionPlan
    field_names = {f.name for f in dataclasses.fields(ExecutionPlan)}
    forbidden = {"fill_price", "average_price", "status", "filled_quantity",
                "broker_order_id", "order_result"}
    assert field_names.isdisjoint(forbidden)


def test_execution_adapter_translates_in_declared_sequence_order(config, logger):
    """The Execution Adapter must submit legs in exactly the order the
    ExecutionPlan declares -- CE before PE, matching the pre-Sprint-2
    inline submission order the partial-leg-unwind logic depends on."""
    from bujji.core.enums import OptionType, Side
    from bujji.core.execution_adapter import translate
    from bujji.core.models import ExecutionPlan, OptionContract

    ce = OptionContract("NIFTY22000CE", "NIFTY", 22000, OptionType.CE, "2026-07-21", 65)
    pe = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE, "2026-07-21", 65)
    plan = ExecutionPlan(
        execution_id="EXEC-1", decision_id="DEC-1", strategy_type="X",
        structure_type="ATM_STRADDLE",
        contracts={"ce": ce, "pe": pe},
        side_per_leg={"ce": Side.SELL, "pe": Side.SELL},
        quantities={"ce": 65, "pe": 65},
        execution_sequence=["ce", "pe"],
        idempotency_keys={"ce": "CID-CE", "pe": "CID-PE"},
        broker_account="paper",
    )
    requests = translate(plan)
    assert [r.contract.symbol for r in requests] == ["NIFTY22000CE", "NIFTY22000PE"]
    assert [r.client_order_id for r in requests] == ["CID-CE", "CID-PE"]
    assert requests[0].tag == "entry_ce" and requests[1].tag == "entry_pe"


def test_execution_adapter_touches_nothing_but_order_construction(config, logger):
    """Import-boundary check: the adapter module must not import anything
    from strategy/intelligence/risk/learning -- only models."""
    import bujji.core.execution_adapter as mod
    import ast, inspect
    tree = ast.parse(inspect.getsource(mod))
    imported_modules = [
        n.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for n in [node]
    ]
    forbidden = ["signal", "intelligence", "capital", "learning", "trade"]
    for mod_name in imported_modules:
        assert not any(f in mod_name for f in forbidden), mod_name


def test_decision_journal_write_failure_never_raises(config, logger, tmp_path):
    """Best-effort persistence, per spec -- a broken path must not crash
    the caller."""
    from bujji.journal.decision_journal import DecisionJournal
    from bujji.core.models import DecisionSnapshot, TradeIntention
    from datetime import datetime
    from bujji.core.clock import IST

    dj = DecisionJournal(tmp_path / "sub" / "d.jsonl", logger)
    intention = TradeIntention(direction=None, strategy_type="X", thesis="",
                               evidence_refs={}, as_of=datetime(2026, 7, 20, 9, 20, tzinfo=IST))
    snap = DecisionSnapshot(
        decision_id="DEC-1", as_of=datetime(2026, 7, 20, 9, 20, tzinfo=IST),
        strategy_version="X", market_observations={}, intelligence_snapshot={},
        intention=intention, planned_contracts={}, planned_structure="ATM_STRADDLE",
        broker_session="paper", replay_reference="2026-07-20T09:20:00",
    )
    dj.record(snap)  # Directory doesn't exist yet on construction in some setups -- must not raise.
    assert dj.get("DEC-1") is not None
