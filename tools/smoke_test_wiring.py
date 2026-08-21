import asyncio
from datetime import datetime
import sys
sys.path.insert(0, "/opt/bujji/app")

from bujji.broker.paper import PaperBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.journal.portfolio_valuation_journal import PortfolioValuationJournal, TradeLifecycleTracker
from bujji.trading_brain.exit_engine.config import ExitRuleConfig
from bujji.trading_brain.exit_engine.engine import evaluate as evaluate_exit
from bujji.trading_brain.exit_engine.order_builder import build_closing_orders
from bujji.trading_brain.exit_engine.dashboard import render_exit_completion
from bujji.trading_brain.portfolio_valuation.engine import revalue

CONTRACT = OptionContract("NSE:NIFTY24250CE", "NIFTY", 24250, OptionType.CE, "2026-08-04", 150)

paper_broker = PaperBroker()
journal = PortfolioValuationJournal("/tmp/wiring_smoketest.jsonl")
tracker = TradeLifecycleTracker()
exit_config = ExitRuleConfig(hard_time_exit=None, max_loss=None, profit_target=5000.0)

asyncio.run(paper_broker.connect())
asyncio.run(paper_broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-ENTRY", reference_price=100.0)))

# Simulate the exact per-tick block in run_live_shadow.py's new code.
positions = asyncio.run(paper_broker.get_open_positions())
valuation = revalue(positions, {CONTRACT.symbol: 140.0}, {}, triggering_symbol=CONTRACT.symbol,
                    triggering_tick_timestamp="2026-07-31T09:50:00", clock=lambda: datetime(2026, 7, 31, 9, 50, 0))
journal.record_valuation(valuation)
tracker.observe(valuation)

exit_decision = evaluate_exit(valuation, positions, exit_config, now_ist_time="09:50")
print("exit_decision:", exit_decision.should_exit, exit_decision.reason)

if exit_decision.should_exit:
    closing_orders = build_closing_orders(positions, valuation)
    for closing_order in closing_orders:
        exit_result = asyncio.run(paper_broker.place_order(closing_order))
        symbol = closing_order.contract.symbol
        summary = tracker.summary(symbol)
        journal.record_exit(
            symbol=symbol, entry_ltp=summary["entry_ltp"], exit_ltp=exit_result.average_price,
            running_mtm=valuation.total_pnl or 0.0, max_profit=summary["max_profit"], max_drawdown=summary["max_drawdown"],
            exit_reason=exit_decision.reason, entry_timestamp=summary["entry_timestamp"], exit_timestamp="2026-07-31T09:50:00",
            final_mtm=paper_broker.get_realized_pnl(symbol), exit_decision_timestamp=exit_decision.timestamp,
        )
        print(render_exit_completion(symbol=symbol, exit_reason=exit_decision.reason, exit_timestamp="2026-07-31T09:50:00",
                                     exit_price=exit_result.average_price, final_pnl=paper_broker.get_realized_pnl(symbol)))

final_positions = asyncio.run(paper_broker.get_open_positions())
print("final_positions:", final_positions)
print("journal records:", len(journal.read_all()))
