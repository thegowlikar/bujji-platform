"""Tests — Live Shadow Real-Time Paper Execution sprint: Portfolio
Valuation Journal (Part 6) and TradeLifecycleTracker peak/trough."""
from __future__ import annotations

from datetime import datetime

from bujji.journal.portfolio_valuation_journal import PortfolioValuationJournal, TradeLifecycleTracker
from bujji.trading_brain.portfolio_valuation.engine import revalue

FIXED_CLOCK = lambda: datetime(2026, 7, 30, 9, 20, 0)


def _position(symbol="NSE:NIFTY24250CE", side="BUY", avg_price=100.0):
    return {"symbol": symbol, "side": side, "qty": 150, "avg_price": avg_price, "entry_timestamp": "2026-07-30T09:20:00"}


def test_record_and_read_back_valuation(tmp_path):
    journal = PortfolioValuationJournal(tmp_path / "pv.jsonl")
    v = revalue([_position()], {"NSE:NIFTY24250CE": 110.0}, {}, clock=FIXED_CLOCK)
    journal.record_valuation(v)
    records = journal.read_all()
    assert len(records) == 1
    assert records[0]["type"] == "VALUATION"
    assert records[0]["valuation"]["valuation_id"] == v.valuation_id


def test_record_exit_carries_all_requested_fields(tmp_path):
    journal = PortfolioValuationJournal(tmp_path / "pv.jsonl")
    journal.record_exit(
        symbol="NSE:NIFTY24250CE", entry_ltp=100.0, exit_ltp=115.0, running_mtm=2250.0,
        max_profit=2500.0, max_drawdown=-300.0, exit_reason="TARGET_HIT",
        entry_timestamp="2026-07-30T09:20:00", exit_timestamp="2026-07-30T11:00:00",
    )
    records = journal.read_all()
    assert records[0]["type"] == "EXIT"
    for field in ("entry_ltp", "exit_ltp", "running_mtm", "max_profit", "max_drawdown", "exit_reason", "entry_timestamp", "exit_timestamp"):
        assert field in records[0]


def test_tracker_records_max_profit_and_drawdown_across_multiple_observations():
    tracker = TradeLifecycleTracker()
    prices = [100.0, 110.0, 90.0, 105.0]  # peak at 110 (+10*150=1500), trough at 90 (-10*150=-1500)
    for p in prices:
        v = revalue([_position(avg_price=100.0)], {"NSE:NIFTY24250CE": p}, {}, clock=FIXED_CLOCK)
        tracker.observe(v)
    summary = tracker.summary("NSE:NIFTY24250CE")
    assert summary["max_profit"] == (110.0 - 100.0) * 150
    assert summary["max_drawdown"] == (90.0 - 100.0) * 150
    assert summary["last_ltp"] == 105.0


def test_tracker_returns_none_for_unobserved_symbol():
    tracker = TradeLifecycleTracker()
    assert tracker.summary("NEVER_SEEN") is None


def test_tracker_ignores_legs_with_no_observed_price():
    tracker = TradeLifecycleTracker()
    v = revalue([_position()], {}, {}, clock=FIXED_CLOCK)  # no price -> unrealized_pnl is None
    tracker.observe(v)
    assert tracker.summary("NSE:NIFTY24250CE") is None
