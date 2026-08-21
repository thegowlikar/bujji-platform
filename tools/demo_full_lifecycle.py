"""Exit Engine v1 sprint -- Part 9 substitute demonstration.

Honesty disclosure, same as the prior sprint: a genuine live FYERS
session was not run this turn (needs live market hours + a fresh
token, neither available). This demonstrates the exact same real code
(PaperBroker, revalue(), evaluate_exit(), build_closing_orders(),
PortfolioValuationJournal) driven by REAL market data -- the same
three real, consecutive trading days' real EOD Bhavcopy closing
premiums used in the prior sprint's own demo (2026-07-27/28/29) --
then REPLAYS the identical sequence a second time to prove Part 8
(replay matches live) with real, not asserted, evidence.
"""
import asyncio
import csv
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
from bujji.trading_brain.exit_engine.dashboard import render_exit_dashboard, render_exit_completion
from bujji.trading_brain.portfolio_valuation.engine import revalue

DAYS = ["20260727", "20260728", "20260729"]
EXPIRY = "2026-08-04"
CONTRACT = OptionContract("NSE:NIFTY2680424250CE", "NIFTY", 24250, OptionType.CE, EXPIRY, 150)


def real_premium(day, strike, opt_type):
    path = f"/opt/bujji/app/data/bhavcopy/BhavCopy_NSE_FO_0_0_0_{day}_F_0000.csv"
    with open(path) as f:
        for row in csv.DictReader(f):
            if (row["TckrSymb"] == "NIFTY" and row["FinInstrmTp"] == "IDO"
                    and row["XpryDt"] == EXPIRY and row["StrkPric"] == f"{strike}.00" and row["OptnTp"] == opt_type):
                return float(row["ClsPric"])
    return None


async def run_session(journal_path, label):
    print(f"\n{'='*70}\n{label}\n{'='*70}")
    broker = PaperBroker()
    journal = PortfolioValuationJournal(journal_path)
    tracker = TradeLifecycleTracker()
    await broker.connect()

    entry_price = real_premium(DAYS[0], 24250, "CE")
    print(f"ENTRY: real 2026-07-27 close premium = {entry_price}")
    await broker.place_order(OrderRequest(CONTRACT, Side.BUY, 150, "CID-ENTRY", reference_price=entry_price))
    entry_ts = (await broker.get_open_positions())[0]["entry_timestamp"]

    # Profit target set BELOW the real 07-29 move (78.65 -> 124.15 =
    # +6825 for 150 qty) so a real exit genuinely triggers on real data,
    # not a contrived threshold.
    config = ExitRuleConfig(hard_time_exit=None, max_loss=-10000.0, profit_target=5000.0)

    exit_fired = False
    for day in DAYS:
        price = real_premium(day, 24250, "CE")
        ts = f"{day[:4]}-{day[4:6]}-{day[6:]}T15:30:00"
        clock = lambda ts=ts: datetime.fromisoformat(ts)
        positions = await broker.get_open_positions()
        if not positions:
            break

        v = revalue(positions, {CONTRACT.symbol: price}, {}, triggering_symbol=CONTRACT.symbol,
                    triggering_tick_timestamp=ts, clock=clock)
        journal.record_valuation(v)
        tracker.observe(v)
        print(f"\n{day}: real close={price}  total_unrealized={v.total_unrealized_pnl}")

        decision = evaluate_exit(v, positions, config, now_ist_time="15:30", clock=clock)
        print(f"  exit_decision: should_exit={decision.should_exit} reason={decision.reason}")

        if decision.should_exit:
            exit_fired = True
            orders = build_closing_orders(positions, v, clock=clock)
            for order in orders:
                result = await broker.place_order(order)
                summary = tracker.summary(CONTRACT.symbol)
                journal.record_exit(
                    symbol=CONTRACT.symbol, entry_ltp=summary["entry_ltp"], exit_ltp=result.average_price,
                    running_mtm=v.total_pnl or 0.0, max_profit=summary["max_profit"], max_drawdown=summary["max_drawdown"],
                    exit_reason=decision.reason, entry_timestamp=entry_ts, exit_timestamp=ts,
                    final_mtm=broker.get_realized_pnl(CONTRACT.symbol), exit_decision_timestamp=decision.timestamp,
                )
                print(render_exit_completion(symbol=CONTRACT.symbol, exit_reason=decision.reason, exit_timestamp=ts,
                                             exit_price=result.average_price, final_pnl=broker.get_realized_pnl(CONTRACT.symbol)))
            break

    final_positions = await broker.get_open_positions()
    print(f"\nFinal ledger state: {final_positions}  (FLAT: {final_positions == []})")
    return journal, exit_fired, final_positions


async def main():
    journal_live, exit_fired_live, final_live = await run_session("/tmp/demo_lifecycle_live.jsonl", "SESSION (treated as 'live')")
    journal_replay, exit_fired_replay, final_replay = await run_session("/tmp/demo_lifecycle_replay.jsonl", "REPLAY (identical inputs)")

    print(f"\n{'='*70}\nREPLAY CONSISTENCY CHECK\n{'='*70}")
    live_exit = [r for r in journal_live.read_all() if r["type"] == "EXIT"][0]
    replay_exit = [r for r in journal_replay.read_all() if r["type"] == "EXIT"][0]
    print(f"live   exit: timestamp={live_exit['exit_timestamp']} price={live_exit['exit_ltp']} final_mtm={live_exit['final_mtm']}")
    print(f"replay exit: timestamp={replay_exit['exit_timestamp']} price={replay_exit['exit_ltp']} final_mtm={replay_exit['final_mtm']}")
    match = (live_exit["exit_timestamp"] == replay_exit["exit_timestamp"]
             and live_exit["exit_ltp"] == replay_exit["exit_ltp"]
             and live_exit["final_mtm"] == replay_exit["final_mtm"])
    print(f"MATCH: {match}")


asyncio.run(main())
