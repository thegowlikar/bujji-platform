"""What would zero-gap actually cost, in subscriptions?

Two candidate definitions of "no contract can enter canonical unseen":
  A) buffer the provisional bands by a justified maximum opening move
  B) subscribe the entire relevant option master

Both are measured here against the real master. Neither is asserted.
"""
import asyncio, logging, sys
from datetime import datetime, date
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, "/opt/bujji/work-m4")
from bujji.broker.instrument_master import InstrumentMaster
from bujji.capture_universe.builder import (
    DEFAULT_TIERS, ROLE_FRONT, ROLE_SECOND, ROLE_MONTHLY,
    build_capture_universe, select_expiries, upcoming_expiries)

PRIOR = 24252.0


async def main():
    log = logging.getLogger("cap"); logging.basicConfig(level=logging.WARNING)
    m = InstrumentMaster(Path("/opt/bujji/gate1/master_cache"), log, exchange="NSE")
    await m._ensure_fresh()
    rows = m._rows_for("NIFTY")

    all_expiries = sorted({r.expiry_date for r in rows})
    today = datetime.now().date()
    roles = select_expiries(all_expiries, today)
    role_expiries = set(roles.values())

    print(f"NIFTY option master: {len(rows)} contracts across "
          f"{len(all_expiries)} expiries")
    print(f"Canonical expiry roles: "
          + ", ".join(f"{r}={e}" for r, e in sorted(roles.items())))
    print()

    # (B) the full master, and the part of it that can EVER matter -- only the
    # three role expiries can enter the canonical universe, whatever the move.
    in_roles = [r for r in rows if r.expiry_date in role_expiries]
    print("OPTION B -- subscribe the whole relevant master")
    print(f"  entire NIFTY option master        : {len(rows):>6} contracts")
    print(f"  only the 3 canonical role expiries: {len(in_roles):>6} contracts")
    print(f"  + spot, VIX, futures              : {len(in_roles)+3:>6} subscriptions")
    print("  (a contract in a NON-role expiry can never enter the canonical")
    print("   universe, so covering the 3 role expiries fully IS zero-gap")
    print("   with respect to opening moves of ANY size)")
    print()

    # (A) buffer sizing against a max opening move
    print("OPTION A -- buffered bands, by maximum opening move covered")
    print(f"  {'move':>7} {'move %':>7} {'buffer':>7} {'symbols':>8}  {'vs canonical':>12}")
    base = build_capture_universe(rows, spot=PRIOR, as_of=today,
                                  futures_symbol=None, include_vix=True)
    n_canon = len(base.instruments)
    for move in (250, 500, 750, 1000, 1500, 2000, 3000):
        tiers = {k: v + move for k, v in DEFAULT_TIERS.items()}
        u = build_capture_universe(rows, spot=PRIOR, as_of=today, tiers=tiers,
                                   futures_symbol=None, include_vix=True)
        print(f"  {move:>7} {100*move/PRIOR:>6.2f}% {move:>7} {len(u.instruments):>8}"
              f"  {len(u.instruments)/n_canon:>11.2f}x")
    print(f"  canonical (no buffer) = {n_canon} symbols")
    print()

    # Where does the master itself run out? A buffer wider than the listed
    # strike range cannot add contracts that do not exist.
    for e in sorted(role_expiries):
        ks = sorted({r.strike for r in rows if r.expiry_date == e})
        print(f"  {e}: {len(ks)} strikes listed, {ks[0]:.0f}-{ks[-1]:.0f} "
              f"(prior {PRIOR:.0f} is {100*(PRIOR-ks[0])/PRIOR:.0f}% above the floor)")

asyncio.run(main())
