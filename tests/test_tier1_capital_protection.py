"""Tier 1 capital-protection tests (C1–C4).

These exercise the safety machinery only — no strategy behaviour is asserted
here beyond what is needed to open a position to protect.

  C1  crash recovery of an open position (resume / already-flat / orphan)
  C2  guaranteed end-of-day square-off
  C3  order idempotency (no duplicate orders)
  C4  partial-fill handling (size off actual fill; flatten fully on exit)
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import Side, State
from bujji.core.orchestrator import Orchestrator
from bujji.core.position_codec import position_from_dict, position_to_dict
from bujji.core.runtime_status import RuntimeStatus
from bujji.core.session_state import SessionSnapshot, SessionStore
from bujji.execution.engine import ExecutionEngine
from bujji.journal.journal import TradeJournal
from bujji.signal.engine import SignalEngine
from bujji.trade.manager import TradeManager
from tests.conftest import c


def _fast_broker_cfg(config):
    # Keep partial-fill polling from blocking the test suite.
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    config.broker.retry_backoff_seconds = 0.001


def build_orch(config, logger, broker, tmp_path):
    """Assemble a real orchestrator over a paper broker with temp artifacts."""
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    status = RuntimeStatus()
    execn = ExecutionEngine(broker, config, logger)
    orch = Orchestrator(
        config, logger, SignalEngine(config, logger), TradeManager(config, logger),
        execn, TradeJournal(config.paths.journal_csv, config.paths.database),
        SessionStore(config.paths.state_file), status,
    )
    return orch, status


async def _enter_position(orch):
    """Drive the straddle entry at 09:20 (no ORB required)."""
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))


# ---------------------------------------------------------------------- #
# Serialization round-trip (foundation of C1)
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_position_codec_roundtrip(config, logger, tmp_path):
    broker = PaperBroker()
    orch, _ = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    pos = orch._trade.position  # noqa: SLF001 - test introspection.
    restored = position_from_dict(position_to_dict(pos))
    assert restored.contract.symbol == pos.contract.symbol
    assert restored.quantity == pos.quantity
    assert restored.direction == pos.direction
    assert restored.entry_price == pos.entry_price
    assert restored.orb is None                 # Straddle has no opening range.
    assert restored.ce_contract is not None  # CE leg serialized and restored.
    assert restored.pe_contract is not None  # PE leg serialized and restored.


# ---------------------------------------------------------------------- #
# C1 — recovery
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c1_resume_open_position(config, logger, tmp_path):
    broker = PaperBroker()  # shared across the "restart".
    a, _ = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await _enter_position(a)
    assert a.state is State.IN_POSITION
    symbol = a._trade.position.contract.symbol  # noqa: SLF001

    # Simulate a process restart: brand-new orchestrator, same broker + store.
    b, _ = build_orch(config, logger, broker, tmp_path)
    await b.startup()
    assert b.state is State.IN_POSITION            # position was NOT abandoned.
    assert b.has_open_position()
    assert b._trade.position.contract.symbol == symbol  # noqa: SLF001

    # And it can be managed to a clean exit after recovery.
    ok = await b.square_off("post_recovery_exit")
    assert ok and b.state is State.DONE_FOR_DAY
    assert not b.has_open_position()
    assert not await broker.get_open_positions()


@pytest.mark.asyncio
async def test_c1_position_already_flat(config, logger, tmp_path):
    broker = PaperBroker()
    a, _ = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await _enter_position(a)
    # Broker-side position vanished (e.g. exit filled just before crash).
    broker._positions.clear()  # noqa: SLF001

    b, _ = build_orch(config, logger, broker, tmp_path)
    await b.startup()
    assert b.state is State.DONE_FOR_DAY   # finalized, not resumed.
    assert not b.has_open_position()


@pytest.mark.asyncio
async def test_c1_orphan_position_flattened(config, logger, tmp_path):
    broker = PaperBroker()
    # Broker holds a short we have NO snapshot for (unknown/orphan).
    broker.seed_position("NIFTY22000PE", Side.SELL.value, 75, 120.0)

    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    assert orch.state is State.DONE_FOR_DAY
    assert not await broker.get_open_positions()  # flattened for safety.
    assert status.healthy is False                # operator is alerted.


# ---------------------------------------------------------------------- #
# C2 — guaranteed end-of-day square-off
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c2_end_of_day_squares_off(config, logger, tmp_path):
    broker = PaperBroker()
    orch, _ = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch.has_open_position()

    ok = await orch.end_of_day()
    assert ok and orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()
    assert not await broker.get_open_positions()   # flat, no overnight risk.


@pytest.mark.asyncio
async def test_c2_end_of_day_no_position(config, logger, tmp_path):
    broker = PaperBroker()
    orch, _ = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    ok = await orch.end_of_day()
    assert ok and orch.state is State.DONE_FOR_DAY


# ---------------------------------------------------------------------- #
# C3 — order idempotency
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c3_no_duplicate_when_place_errors_after_accept(config, logger):
    _fast_broker_cfg(config)
    from bujji.core.enums import OptionType
    from bujji.core.models import OptionContract, OrderRequest

    broker = PaperBroker(raise_on_place_after_record=True)
    engine = ExecutionEngine(broker, config, logger)
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "WEEKLY", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-DUP")
    result = await engine.submit_and_confirm(req)
    assert result.filled_quantity == 75
    assert broker.place_calls == 1   # accepted exactly once, no duplicate.


@pytest.mark.asyncio
async def test_c3_duplicate_submit_same_cid_is_adopted(config, logger):
    _fast_broker_cfg(config)
    from bujji.core.enums import OptionType
    from bujji.core.models import OptionContract, OrderRequest

    broker = PaperBroker()
    engine = ExecutionEngine(broker, config, logger)
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "WEEKLY", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-SAME")
    r1 = await engine.submit_and_confirm(req)
    r2 = await engine.submit_and_confirm(req)  # e.g. a retry after restart.
    assert r1.broker_order_id == r2.broker_order_id
    assert broker.place_calls == 1


# ---------------------------------------------------------------------- #
# C4 — partial fills
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_c4_entry_sizes_off_actual_fill(config, logger, tmp_path):
    _fast_broker_cfg(config)
    config.risk.lots = 2                       # requested = 150.
    broker = PaperBroker(partial_fill_qty=75)  # only 75 fills.
    orch, _ = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    # Position must reflect what actually filled, not what was requested.
    assert orch._trade.position.quantity == 75  # noqa: SLF001


@pytest.mark.asyncio
async def test_c4_exit_flattens_fully_through_partials(config, logger, tmp_path):
    _fast_broker_cfg(config)
    config.risk.lots = 2                        # position size 150.
    broker = PaperBroker()
    orch, _ = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch._trade.position.quantity == 150  # noqa: SLF001

    # Now force exits to fill only 75 at a time; square-off must still end flat.
    broker._partial_fill_qty = 75  # noqa: SLF001
    ok = await orch.square_off("partial_exit_test")
    assert ok and orch.state is State.DONE_FOR_DAY
    assert not orch.has_open_position()
    assert not await broker.get_open_positions()  # fully flattened.


# ---------------------------------------------------------------------- #
# One-leg entry failure — the CE leg fills but the PE leg's placement
# raises. Must never leave a naked, unmonitored short sitting at the broker
# for the rest of the day; the filled leg is bought back same-cycle.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_partial_entry_failure_auto_flattens_the_filled_leg(
    config, logger, tmp_path
):
    _fast_broker_cfg(config)

    class PeFailsBroker(PaperBroker):
        """CE sells fine; the PE leg's place_order always raises."""

        async def place_order(self, request):
            if request.contract.option_type.value == "PE" and request.side.value == "SELL":
                raise RuntimeError("simulated PE leg rejection")
            return await super().place_order(request)

    broker = PeFailsBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))

    # Entry must NOT be considered open — the PE leg never filled.
    assert not orch.has_open_position()
    assert orch.state is State.READY  # Rolled back, not stuck IN_POSITION.

    # The CE leg that DID fill must have been bought back immediately —
    # not left naked at the broker until the next restart's reconciliation.
    assert not await broker.get_open_positions()
    # Two SELLs (CE, rejected PE never recorded) + one BUY unwind = 2 recorded
    # broker-side placements (the failed PE raise never reaches place_calls
    # bookkeeping since it raises before returning a result).
    assert broker.place_calls == 2  # CE sell + CE unwind buy.


# ---------------------------------------------------------------------- #
# ATM contract resolution — CE/PE pairing and failure handling.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_ce_and_pe_always_resolve_to_the_same_strike(config, logger, tmp_path):
    """The two legs must never drift to different strikes — both are
    resolved from the SAME candle close, through the SAME atm_strike()
    formula, in the same entry cycle."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    pos = orch._trade.position  # noqa: SLF001
    assert pos.ce_contract is not None and pos.pe_contract is not None
    assert pos.ce_contract.strike == pos.pe_contract.strike
    # And both must be genuinely ATM to the entry candle's spot (22005 close,
    # 50-point grid -> 22000).
    assert pos.ce_contract.strike == 22000
    assert pos.pe_contract.strike == 22000


@pytest.mark.asyncio
async def test_contract_resolution_failure_rolls_back_to_ready_not_stuck(
    config, logger, tmp_path
):
    """A missing-contract/missing-strike failure during resolve_atm_contract
    raises LookupError, NOT ExecutionError/AuthenticationError — it must
    still roll the FSM back to READY (not get wedged at CONFIRMED with no
    other code path ever revisiting it for the rest of the day)."""
    _fast_broker_cfg(config)

    class NoContractsBroker(PaperBroker):
        async def resolve_atm_contract(self, underlying, spot, direction,
                                       strike_interval, lot_size):
            raise LookupError("simulated: no contracts found at this expiry")

    broker = NoContractsBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))

    assert not orch.has_open_position()
    assert orch.state is State.READY  # Not stuck at CONFIRMED.
    assert status.healthy is False
    assert "entry_failed_contract_resolution" in status.health_detail
