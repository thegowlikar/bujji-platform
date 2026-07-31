"""Part 8 demonstration -- real MTM evolution across REAL market data.

Honesty disclosure: a genuine sub-day live FYERS tick sequence was not
run this turn (would require market hours + a freshly generated
token, neither available in this turn). Instead, this demonstrates the
exact same real engine.revalue() mechanics against three REAL,
consecutive trading days' real EOD Bhavcopy closing premiums
(2026-07-27, 07-28, 07-29) for the same real contracts entered in the
earlier verified fix run (BUY 24250 CE / SELL 24300 CE, expiry
2026-08-04). Each day's real close is treated as one revaluation event
-- day-granularity, not sub-day ticks, honestly labeled as such. The
SAME revalue() function is what a live tick loop calls per real tick;
only the granularity of this demo's input differs, not the mechanism.
"""
import csv
import sys
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")

from bujji.trading_brain.portfolio_valuation.engine import revalue
from bujji.trading_brain.portfolio_valuation.dashboard import render_portfolio_dashboard

DAYS = ["20260727", "20260728", "20260729"]
EXPIRY = "2026-08-04"
LEGS = {"NSE:NIFTY2680424250CE": ("BUY", 24250, "CE"), "NSE:NIFTY2680424300CE": ("SELL", 24300, "CE")}


def real_premium(day, strike, opt_type):
    path = f"/opt/bujji/app/data/bhavcopy/BhavCopy_NSE_FO_0_0_0_{day}_F_0000.csv"
    with open(path) as f:
        for row in csv.DictReader(f):
            if (row["TckrSymb"] == "NIFTY" and row["FinInstrmTp"] == "IDO"
                    and row["XpryDt"] == EXPIRY and row["StrkPric"] == f"{strike}.00" and row["OptnTp"] == opt_type):
                return float(row["ClsPric"])
    return None


# Entry: real 07-27 close premiums.
entry_ce = real_premium(DAYS[0], 24250, "CE")
entry_pe = real_premium(DAYS[0], 24300, "CE")
print(f"=== REAL entry prices (2026-07-27 close) ===")
print(f"  BUY  24250 CE @ {entry_ce}")
print(f"  SELL 24300 CE @ {entry_pe}")
print()

positions = [
    {"symbol": "NSE:NIFTY2680424250CE", "side": "BUY", "qty": 150, "avg_price": entry_ce, "entry_timestamp": "2026-07-27T15:30:00"},
    {"symbol": "NSE:NIFTY2680424300CE", "side": "SELL", "qty": 150, "avg_price": entry_pe, "entry_timestamp": "2026-07-27T15:30:00"},
]
realized = {}

for day in DAYS:
    ce_price = real_premium(day, 24250, "CE")
    pe_price = real_premium(day, 24300, "CE")
    latest_prices = {"NSE:NIFTY2680424250CE": ce_price, "NSE:NIFTY2680424300CE": pe_price}
    ts = f"{day[:4]}-{day[4:6]}-{day[6:]}T15:30:00"
    v = revalue(positions, latest_prices, realized, triggering_symbol="NSE:NIFTY2680424250CE",
                triggering_tick_timestamp=ts, clock=lambda: datetime.fromisoformat(ts))
    print(f"=== Revaluation event: real close, {day} ===")
    print(render_portfolio_dashboard(v))
    print()
