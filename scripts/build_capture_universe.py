"""Print today's capture universe, built from the REAL exchange master.

Read-only. Downloads the public NSE_FO symbol master (no authentication --
`public.fyers.in/sym_details/NSE_FO.csv`), resolves the expiry roles, and
prints the exact instrument set capture would subscribe to, plus its
measured storage cost.

  python scripts/build_capture_universe.py --spot 24366
  python scripts/build_capture_universe.py --spot 24366 --json /tmp/universe.json

`--spot` is mandatory and explicit: the band must centre on a real observed
level, and this script deliberately makes no broker call to fetch one.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/bujji/app")

from bujji.broker.instrument_master import InstrumentMaster  # noqa: E402
from bujji.capture_universe.builder import (  # noqa: E402
    DEFAULT_TIERS, KIND_OPTION, build_capture_universe,
)

# Measured on the real 2026-08-14 capture: payload 246 B + record envelope
# 1,593 B. The envelope is 87% of the row and is the single biggest lever
# on storage -- see docs, and the "normalised" column below.
BYTES_PER_ROW_TODAY = 1880
BYTES_PER_ROW_NORMALISED = 300
ROWS_PER_DAY = 74            # five-minute bars actually observed per contract
SESSIONS_PER_YEAR = 250


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot", type=float, required=True,
                    help="real observed NIFTY level to centre the bands on")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD (default: today IST)")
    ap.add_argument("--json", default=None, help="write the universe to this path")
    args = ap.parse_args()

    as_of = (datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of
             else datetime.now(ZoneInfo("Asia/Kolkata")).date())

    log = logging.getLogger("capture-universe")
    logging.basicConfig(level=logging.WARNING)
    master = InstrumentMaster(Path("/tmp/capture_universe_cache"), log, exchange="NSE")
    asyncio.run(master._ensure_fresh())
    rows = master._rows_for(args.underlying)
    if not rows:
        raise SystemExit(f"symbol master returned no rows for {args.underlying}")

    universe = build_capture_universe(rows, args.spot, as_of)

    print(f"as_of={universe.as_of_date}  spot={universe.spot}  ATM={universe.atm_strike}")
    print(f"symbol master rows: {len(rows)}   expiries available: {universe.expiries_available}"
          f"   excluded: {universe.expiries_excluded}")
    print()
    print("ROLES RESOLVED")
    for role, expiry in sorted(universe.roles_resolved.items(), key=lambda kv: kv[1]):
        band = DEFAULT_TIERS.get(role)
        n = sum(1 for i in universe.instruments if i.role == role and i.kind == KIND_OPTION)
        print(f"  {role:8} {expiry}  band=+/-{band:<5} contracts={n}")
    for note in universe.notes:
        print(f"  NOTE: {note}")
    print()

    by_expiry = defaultdict(list)
    for i in universe.by_kind(KIND_OPTION):
        by_expiry[i.expiry].append(i)
    print("OPTION CONTRACTS BY EXPIRY")
    for expiry in sorted(by_expiry):
        legs = by_expiry[expiry]
        strikes = sorted({i.strike for i in legs})
        print(f"  {expiry}  strikes={len(strikes):>3}  "
              f"range {strikes[0]:.0f}..{strikes[-1]:.0f}  contracts={len(legs)}")
    print()
    print(f"SUMMARY  {universe.summary()}")

    n_opt = len(universe.by_kind(KIND_OPTION))
    for label, per_row in (("current row format", BYTES_PER_ROW_TODAY),
                           ("envelope normalised", BYTES_PER_ROW_NORMALISED)):
        per_day = n_opt * ROWS_PER_DAY * per_row
        print(f"  storage ({label:19}): {per_day/1e6:7.1f} MB/day   "
              f"{per_day*SESSIONS_PER_YEAR/1e9:6.2f} GB/year")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "as_of_date": universe.as_of_date, "spot": universe.spot,
            "atm_strike": universe.atm_strike,
            "roles_resolved": universe.roles_resolved,
            "collapsed_roles": list(universe.collapsed_roles),
            "notes": list(universe.notes),
            "instruments": [
                {"symbol": i.symbol, "kind": i.kind, "role": i.role, "expiry": i.expiry,
                 "strike": i.strike, "option_type": i.option_type, "lot_size": i.lot_size}
                for i in universe.instruments
            ],
        }, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
