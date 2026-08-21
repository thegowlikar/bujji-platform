"""Tests — Exit Engine v1 sprint, Part 10: full lifecycle integration
(entry -> live MTM -> exit decision -> exit execution -> position
closed -> journal complete) and Part 8 (replay/determinism
consistency), all against the REAL PaperBroker, REAL portfolio
valuation engine, REAL ExitEngine, and REAL journal -- no mocks."""
from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.journal.portfolio_valuation_journal import PortfolioValuationJournal, TradeLifecycleTracker
from bujji.trading_brain.exit_engine.config import ExitRuleConfig
from bujji.trading_brain.exit_engine.engine import evaluate
from bujji.trading_brain.exit_engine.order_builder import build_closing_orders
from bujji.trading_brain.exit_engine import taxonomy
from bujji.trading_brain.portfolio_valuation.engine import revalue

CONTRACT = OptionContract("NSE:NIFTY24250CE", "NIFTY", 24250, OptionType.CE, "2026-08-04", 150)


async def _run_full_lifecycle(price_sequence, config, tmp_path, journal_name="pv.jsonl"):
    """Real entry -> real tick-by-tick revaluation -> real exit
    evaluation -> (on trigger) real closing order execution -> real
    journal record. Returns (broker, journal, tracker, exit_decision,
    final_positions)."""
    broker = PaperBroker()
    await broker.connect()
    journal = PortfolioValuationJournal(tmp_path / journal_name)
    tracker = TradeLifecycleTracker()

    # Real entry.
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-ENTRY", reference_price=price_sequence[0][1]))
    entry_timestamp = (await broker.get_open_positions())[0]["entry_timestamp"]

    exit_decision = None
    for now_time, price in price_sequence:
        clock = lambda ts=now_time: datetime.fromisoformat(f"2026-07-31T{ts}:00")
        positions = await broker.get_open_positions()
        if not positions:
            break  # Already flat -- nothing left to revalue/evaluate.
        v = revalue(positions, {CONTRACT.symbol: price}, {}, triggering_symbol=CONTRACT.symbol,
                    triggering_tick_timestamp=f"2026-07-31T{now_time}:00", clock=clock)
        journal.record_valuation(v)
        tracker.observe(v)
        d = evaluate(v, positions, config, now_ist_time=now_time, clock=clock)

        if d.should_exit:
            exit_decision = d
            orders = build_closing_orders(positions, v, clock=clock)
            for order in orders:
                await broker.place_order(order)
            summary = tracker.summary(CONTRACT.symbol)
            journal.record_exit(
                symbol=CONTRACT.symbol, entry_ltp=summary["entry_ltp"], exit_ltp=price,
                running_mtm=v.total_pnl or 0.0, max_profit=summary["max_profit"], max_drawdown=summary["max_drawdown"],
                exit_reason=d.reason, entry_timestamp=entry_timestamp, exit_timestamp=f"2026-07-31T{now_time}:00",
                final_mtm=broker.get_realized_pnl(CONTRACT.symbol), exit_decision_timestamp=d.timestamp,
            )
            break

    final_positions = await broker.get_open_positions()
    return broker, journal, tracker, exit_decision, final_positions


@pytest.mark.asyncio
async def test_full_lifecycle_profit_target_exit(tmp_path):
    prices = [("09:20", 100.0), ("09:35", 105.0), ("09:50", 140.0)]  # +6000 at 09:50, 150 qty
    config = ExitRuleConfig(hard_time_exit=None, profit_target=5000.0)
    broker, journal, tracker, decision, final_positions = await _run_full_lifecycle(prices, config, tmp_path)

    assert decision is not None
    assert decision.should_exit is True
    assert decision.reason == taxonomy.EXIT_REASON_PROFIT_TARGET
    assert final_positions == []  # Ledger is flat.
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((140.0 - 100.0) * 150)

    records = journal.read_all()
    exit_records = [r for r in records if r["type"] == "EXIT"]
    assert len(exit_records) == 1
    exit_record = exit_records[0]
    for field in ("entry_ltp", "exit_ltp", "running_mtm", "max_profit", "max_drawdown",
                  "exit_reason", "entry_timestamp", "exit_timestamp", "final_mtm", "exit_decision_timestamp"):
        assert field in exit_record and exit_record[field] is not None
    assert exit_record["exit_reason"] == taxonomy.EXIT_REASON_PROFIT_TARGET
    assert exit_record["final_mtm"] == pytest.approx((140.0 - 100.0) * 150)


@pytest.mark.asyncio
async def test_full_lifecycle_max_loss_exit(tmp_path):
    prices = [("09:20", 100.0), ("09:35", 90.0), ("09:50", 60.0)]  # -6000 at 09:50
    config = ExitRuleConfig(hard_time_exit=None, max_loss=-5000.0)
    broker, journal, tracker, decision, final_positions = await _run_full_lifecycle(prices, config, tmp_path)

    assert decision.reason == taxonomy.EXIT_REASON_MAXIMUM_LOSS
    assert final_positions == []
    assert broker.get_realized_pnl(CONTRACT.symbol) == pytest.approx((60.0 - 100.0) * 150)


@pytest.mark.asyncio
async def test_full_lifecycle_hard_time_exit(tmp_path):
    prices = [("09:20", 100.0), ("14:00", 102.0), ("15:15", 103.0)]
    config = ExitRuleConfig(hard_time_exit="15:15", max_loss=None, profit_target=None)
    broker, journal, tracker, decision, final_positions = await _run_full_lifecycle(prices, config, tmp_path)

    assert decision.reason == taxonomy.EXIT_REASON_HARD_TIME_EXIT
    assert final_positions == []


@pytest.mark.asyncio
async def test_no_exit_leaves_position_open(tmp_path):
    prices = [("09:20", 100.0), ("09:35", 101.0), ("09:50", 99.0)]
    config = ExitRuleConfig(hard_time_exit=None, max_loss=-5000.0, profit_target=5000.0)
    broker, journal, tracker, decision, final_positions = await _run_full_lifecycle(prices, config, tmp_path)

    assert decision is None
    assert len(final_positions) == 1  # Still open.
    records = journal.read_all()
    assert not any(r["type"] == "EXIT" for r in records)


@pytest.mark.asyncio
async def test_tracker_max_profit_and_drawdown_reflected_in_journal(tmp_path):
    """The peak (before the eventual profit-target close) must be
    correctly recorded even though the position didn't close AT the
    peak."""
    prices = [("09:20", 100.0), ("09:30", 120.0), ("09:40", 90.0), ("09:50", 140.0)]  # +3000, then -1500, then close at +6000 (the new peak)
    config = ExitRuleConfig(hard_time_exit=None, profit_target=5000.0)
    broker, journal, tracker, decision, final_positions = await _run_full_lifecycle(prices, config, tmp_path)

    exit_record = [r for r in journal.read_all() if r["type"] == "EXIT"][0]
    # The closing tick itself (+6000) is the highest value observed, so it
    # IS the recorded max_profit -- the tracker observes every tick,
    # including the one that triggers the exit, before the exit is acted on.
    assert exit_record["max_profit"] == pytest.approx((140.0 - 100.0) * 150)
    assert exit_record["max_drawdown"] == pytest.approx((90.0 - 100.0) * 150)


class TestReplayConsistency:
    @pytest.mark.asyncio
    async def test_identical_price_sequence_produces_identical_outcome(self, tmp_path):
        """Part 8: replaying the exact same session must reproduce the
        exact same entry, MTM evolution, exit timestamp, exit price, and
        final P&L -- run the full lifecycle twice with identical inputs
        and prove the outputs are identical, not merely similar."""
        prices = [("09:20", 100.0), ("09:35", 105.0), ("09:50", 140.0)]
        config = ExitRuleConfig(hard_time_exit=None, profit_target=5000.0)

        _, journal_a, _, decision_a, final_a = await _run_full_lifecycle(prices, config, tmp_path, "run_a.jsonl")
        broker_b, journal_b, _, decision_b, final_b = await _run_full_lifecycle(prices, config, tmp_path, "run_b.jsonl")

        assert decision_a.reason == decision_b.reason
        assert decision_a.timestamp == decision_b.timestamp
        assert decision_a.affected_positions == decision_b.affected_positions
        assert final_a == final_b == []

        exit_a = [r for r in journal_a.read_all() if r["type"] == "EXIT"][0]
        exit_b = [r for r in journal_b.read_all() if r["type"] == "EXIT"][0]
        assert exit_a["exit_timestamp"] == exit_b["exit_timestamp"]
        assert exit_a["exit_ltp"] == exit_b["exit_ltp"]
        assert exit_a["final_mtm"] == exit_b["final_mtm"]

    @pytest.mark.asyncio
    async def test_valuation_snapshots_are_identical_across_replay(self, tmp_path):
        prices = [("09:20", 100.0), ("09:35", 110.0)]
        config = ExitRuleConfig(hard_time_exit=None, max_loss=None, profit_target=None)  # never exits -- pure MTM evolution check.

        _, journal_a, _, _, _ = await _run_full_lifecycle(prices, config, tmp_path, "va.jsonl")
        _, journal_b, _, _, _ = await _run_full_lifecycle(prices, config, tmp_path, "vb.jsonl")

        vals_a = [r["valuation"]["total_unrealized_pnl"] for r in journal_a.read_all() if r["type"] == "VALUATION"]
        vals_b = [r["valuation"]["total_unrealized_pnl"] for r in journal_b.read_all() if r["type"] == "VALUATION"]
        assert vals_a == vals_b
