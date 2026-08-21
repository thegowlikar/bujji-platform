import csv, sys
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")

from bujji.production_runtime.composition_root import build_composition_root
from bujji.production_runtime.config import RuntimeConfig, RUNTIME_MODE_SHADOW
from bujji.production_runtime.runtime import PipelineInput, run_shadow
from bujji.trading_brain.nifty_contract_builder.models import (
    NiftySpotSnapshot, NiftyOptionChainSnapshot, NiftyOptionChainEntry,
)

FIXED_CLOCK = lambda: datetime(2026, 7, 30, 9, 20, 0)
BHAVCOPY = "/opt/bujji/app/data/bhavcopy/BhavCopy_NSE_FO_0_0_0_20260729_F_0000.csv"

with open(BHAVCOPY) as f:
    rows = [r for r in csv.DictReader(f) if r["TckrSymb"] == "NIFTY" and r["FinInstrmTp"] == "IDO" and r["StrkPric"]]

nearest_expiry = sorted(set(r["XpryDt"] for r in rows))[0]
real_spot = float(rows[0]["UndrlygPric"])
near = [r for r in rows if r["XpryDt"] == nearest_expiry and abs(float(r["StrkPric"]) - real_spot) <= 500]

# THIS TIME: populate last_price from the real Bhavcopy ClsPric (real premium).
entries = tuple(
    NiftyOptionChainEntry(
        strike=float(r["StrkPric"]), option_type=r["OptnTp"], expiry=nearest_expiry,
        contract_symbol=f"NSE:{r['FinInstrmNm']}", last_price=float(r["ClsPric"]),
    )
    for r in near
)
spot_snapshot = NiftySpotSnapshot(spot=real_spot, as_of="2026-07-30T09:20:00")
option_chain = NiftyOptionChainSnapshot(expiries=(nearest_expiry,), entries=entries, as_of="2026-07-30T09:20:00")

pipeline_input = PipelineInput(
    market_context="TRENDING_UP", market_opinion="BULLISH", context_stability="STABLE",
    calibration="CALIBRATED", governance="APPROVED", lifecycle="ACTIVE", contract="COMPLETE",
)
root = build_composition_root(RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="paper"))
result = run_shadow(root, pipeline_input, spot_snapshot=spot_snapshot, option_chain=option_chain, clock=FIXED_CLOCK)

print(result.trace)
print()
for c in result.contract_construction_result.contracts:
    print(f"  contract: {c.strike}{c.option_type} {c.side}  last_price(real Bhavcopy premium)={c.last_price}")
for r in result.order_construction_result.requests:
    print(f"  order: {r.contract.strike}{r.contract.option_type} {r.side}  reference_price={r.reference_price}")

import asyncio
positions = asyncio.run(root.broker.get_open_positions())
print()
print("=== REAL PaperBroker fills (should now use real Bhavcopy premiums, NOT 120.0 for both) ===")
for p in positions:
    print(" ", p)
