"""Gate 1 — universe construction from the REAL FYERS symbol master.

No authentication required: `public.fyers.in/sym_details/NSE_FO.csv` is a
public file. This produces the exact instrument list the Gate 1 live
measurement will subscribe to, so the universe is validated against real
contracts BEFORE any live session is spent.

Universe (approved): 4 expiries x (ATM +/- 20 strikes) x {CE,PE}
                     + NIFTY spot + NIFTY futures + India VIX
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, "/opt/bujji/app")

from bujji.broker.instrument_master import InstrumentMaster  # noqa: E402

STRIKES_EACH_SIDE = int(__import__("os").environ.get("STRIKES_EACH_SIDE", 25))
UNDERLYING = "NIFTY"
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"


async def main(spot_hint: float | None) -> None:
    log = logging.getLogger("universe")
    logging.basicConfig(level=logging.WARNING)
    master = InstrumentMaster(Path("/tmp/gate1_cache"), log, exchange="NSE")
    await master._ensure_fresh()
    rows = master._rows_for(UNDERLYING)
    print(f"symbol master rows for {UNDERLYING}: {len(rows)}")
    if not rows:
        raise SystemExit("no rows -- symbol master parse failed")

    today = datetime.now(timezone.utc).date()
    by_expiry: dict[date, list] = defaultdict(list)
    for r in rows:
        if r.expiry_date >= today:
            by_expiry[r.expiry_date].append(r)

    expiries = sorted(by_expiry)
    print(f"\nfuture expiries available: {len(expiries)}")
    for e in expiries[:10]:
        n = len(by_expiry[e])
        strikes = sorted({r.strike for r in by_expiry[e]})
        print(f"  {e}  contracts={n:5d}  strikes={len(strikes):4d}  "
              f"range={strikes[0]:.0f}-{strikes[-1]:.0f}  lot={by_expiry[e][0].lot_size}")

    # --- expiry bucket selection: 3 nearest weeklies + the monthly ---------
    # Monthly = the last expiry within its calendar month.
    monthly = []
    seen_month = set()
    for e in expiries:
        key = (e.year, e.month)
        last_in_month = max(x for x in expiries if (x.year, x.month) == key)
        if key not in seen_month:
            seen_month.add(key)
            monthly.append(last_in_month)

    weeklies = [e for e in expiries if e not in monthly][:3]
    chosen = sorted(set(weeklies + monthly[:1]))
    # If the nearest monthly IS one of the weeklies, extend to the next monthly.
    if len(chosen) < 4:
        for m in monthly[1:]:
            if m not in chosen:
                chosen.append(m)
                break
        chosen = sorted(set(chosen))

    print(f"\nSELECTED EXPIRY BUCKETS ({len(chosen)}):")
    for e in chosen:
        label = "MONTHLY" if e in monthly[:2] else "WEEKLY"
        print(f"  {e}  ({label})  dte={(e - today).days}")

    # --- ATM determination -------------------------------------------------
    if spot_hint is None:
        all_strikes = sorted({r.strike for r in by_expiry[chosen[0]]})
        spot_hint = all_strikes[len(all_strikes) // 2]
        print(f"\n[no spot supplied] using median strike as ATM proxy: {spot_hint:.0f}")
    atm = round(spot_hint / 50) * 50
    print(f"ATM (50-grid): {atm:.0f}")

    # --- build the universe ------------------------------------------------
    universe = [
        {"symbol": SPOT_SYMBOL, "kind": "SPOT", "expiry": None, "strike": None, "option_type": None},
        {"symbol": VIX_SYMBOL, "kind": "VIX", "expiry": None, "strike": None, "option_type": None},
    ]
    missing = Counter()
    per_expiry_found = Counter()

    for e in chosen:
        idx = {(r.strike, r.option_type): r for r in by_expiry[e]}
        for i in range(-STRIKES_EACH_SIDE, STRIKES_EACH_SIDE + 1):
            strike = float(atm + i * 50)
            for ot in ("CE", "PE"):
                row = idx.get((strike, ot))
                if row is None:
                    missing[(str(e), ot)] += 1
                    continue
                universe.append({
                    "symbol": row.symbol, "kind": "OPTION", "expiry": str(e),
                    "strike": strike, "option_type": ot, "lot_size": row.lot_size,
                    "atm_distance": i,
                })
                per_expiry_found[str(e)] += 1

    print(f"\n=== UNIVERSE ===")
    print(f"  spot/vix      : 2")
    for e in chosen:
        print(f"  {e}: {per_expiry_found[str(e)]} contracts")
    print(f"  TOTAL         : {len(universe)}")
    if missing:
        print(f"\n  MISSING (strike not listed): {sum(missing.values())}")
        for k, v in list(missing.items())[:6]:
            print(f"    {k}: {v}")

    out = Path("/tmp/gate1_universe.json")
    out.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "atm": atm, "strikes_each_side": STRIKES_EACH_SIDE,
        "expiries": [str(e) for e in chosen],
        "instruments": universe,
    }, indent=1))
    print(f"\nwritten: {out}  ({out.stat().st_size/1024:.1f} KB)")
    print("\nNOTE: NIFTY futures symbol must be added from the futures master "
          "(this file is the OPTIONS master); see report.")


if __name__ == "__main__":
    hint = float(sys.argv[1]) if len(sys.argv) > 1 else None
    asyncio.run(main(hint))
