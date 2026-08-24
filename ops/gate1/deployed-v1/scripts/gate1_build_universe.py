"""Gate 1 — build THE authoritative capture universe. One builder, no second model.

WHY THIS FILE EXISTS AND THE OLD ONE DOES NOT. `scripts/gate1/build_universe.py`
in the live checkout carried its own universe model: fixed strike counts
(STRIKES_EACH_SIDE), expiry selection by broker list order, its own symbol
formatting, and a scratch output at /tmp. That is a SECOND definition of what
Bujji captures, and measuring one universe while the runtime trades another
proves nothing about the runtime. This builds through
`bujji.capture_universe.builder` -- the same code the architecture branch uses
-- so the thing measured is the thing defined.

The canonical model is not a strike count. It is:
  * POINT-BASED BANDS per expiry role: FRONT +/-1500, SECOND +/-1000,
    MONTHLY +/-500 index points. A band in points holds its meaning when the
    strike grid or the index level changes; a strike COUNT does not.
  * EXPIRY ROLES resolved from the real expiry list, with MONTHLY reaching
    FORWARD past a weekly it would otherwise duplicate. Broker list order is
    not consulted anywhere.
  * Roles that collapse onto one expiry emit each contract ONCE, widest band
    winning -- double-subscribing would silently inflate every per-symbol rate.

RUNS FROM THE ARCHITECTURE WORKTREE, not the live checkout, and records which.
Read-only with respect to trading: no broker order path is imported, and the
only network call is the PUBLIC symbol master CSV, which needs no credential.

The universe and its manifest are written to a DURABLE Gate 1 output
directory and passed to the harness unchanged. Nothing regenerates it later:
a universe rebuilt between planning and measuring is a different experiment.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(worktree: Path, *args) -> str:
    try:
        return subprocess.run(["git", "-C", str(worktree), *args],
                              capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


async def build(args) -> int:
    from bujji.broker.instrument_master import InstrumentMaster
    from bujji.capture_universe.builder import (
        DEFAULT_SELECTION_BAND_POINTS, DEFAULT_TIERS, KIND_OPTION,
        UniverseConstructionError, build_capture_universe)

    log = logging.getLogger("gate1-universe")
    logging.basicConfig(level=logging.WARNING)

    master = InstrumentMaster(Path(args.master_cache), log, exchange="NSE")
    await master._ensure_fresh()
    rows = master._rows_for(args.underlying)
    if not rows:
        raise SystemExit("symbol master returned no option rows -- refusing to build")

    cache_file = Path(args.master_cache) / "fyers_fo_NSE.csv"
    master_stat = cache_file.stat()

    # The futures leg comes from the master too, never constructed from today's
    # date -- a guessed symbol is a symbol that silently does not exist.
    futures_symbol = futures_expiry = None
    futures_lot = None
    try:
        futures_symbol, futures_expiry, futures_lot = await master.resolve_nearest_future(
            args.underlying)
    except Exception as exc:
        print(f"  ! futures leg unresolved ({exc}); universe will omit it", file=sys.stderr)

    as_of = (date.fromisoformat(args.as_of) if args.as_of
             else datetime.now(IST).date())

    # PROVISIONAL vs CANONICAL. Same builder, same expiry-role logic, same
    # point-based bands -- the ONLY difference is that a provisional universe
    # widens every tier by an opening buffer.
    #
    # WHY A BUFFER AT ALL. The provisional set is built BEFORE the open, from a
    # prior-session reference price, so that subscriptions are live at 09:15:00
    # and nothing is missed while a post-open spot is fetched. If the market
    # gaps, the canonical band centred on the real open would sit partly
    # outside a provisional band of the same width. The buffer is what makes
    # containment survive a gap -- and containment is the whole point: it is
    # what lets us say the canonical universe was covered continuously FROM the
    # open, rather than from whenever the refinement finished.
    tiers = dict(DEFAULT_TIERS)
    if args.provisional:
        tiers = {role: width + args.opening_buffer_points
                 for role, width in DEFAULT_TIERS.items()}
    try:
        universe = build_capture_universe(
            rows, spot=args.spot, as_of=as_of, tiers=tiers,
            futures_symbol=futures_symbol, include_vix=True,
            selection_band_points=args.selection_band_points)
    except UniverseConstructionError as exc:
        raise SystemExit(f"UNIVERSE REFUSED: {exc}")

    out_dir = Path(args.out) / args.session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    instruments = [
        {"symbol": i.symbol, "kind": i.kind, "role": i.role, "expiry": i.expiry,
         "strike": i.strike, "option_type": i.option_type, "lot_size": i.lot_size}
        for i in universe.instruments]

    # The universe file the harness consumes. `instruments` + `symbol` is the
    # shape the harness reads; everything else is provenance travelling with it.
    universe_doc = {
        "schema": "gate1.universe/1",
        "session_id": args.session_id,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "built_at_ist": datetime.now(IST).isoformat(),
        "as_of_date": universe.as_of_date,
        "underlying": args.underlying,
        "spot": universe.spot,
        "spot_source": args.spot_source,
        "atm": universe.atm_strike,
        "atm_strike": universe.atm_strike,
        "instruments": instruments,
        # MEASURED, never asserted. No expected count appears anywhere.
        "measured_symbol_count": len(instruments),
        "counts_by_kind": universe.summary(),
        "expiry_roles": dict(universe.roles_resolved),
        "collapsed_roles": list(universe.collapsed_roles),
        "expiries_available": universe.expiries_available,
        "expiries_excluded": universe.expiries_excluded,
        "role": "PROVISIONAL" if args.provisional else "CANONICAL",
        "authoritative": not args.provisional,
        "provisional_disclaimer": (
            "PROVISIONAL: built pre-open from a prior-session reference price "
            "with an opening buffer, to avoid a capture gap at 09:15. It is "
            "NOT authoritative and must never be used for trading or for "
            "selection. The canonical universe is built post-open from an "
            "observed live spot." if args.provisional else None),
        "construction": {
            "builder": "bujji.capture_universe.builder.build_capture_universe",
            "tiers_points": dict(tiers),
            "canonical_tiers_points": dict(DEFAULT_TIERS),
            "opening_buffer_points": (args.opening_buffer_points
                                      if args.provisional else 0),
            "selection_band_points": universe.selection_band_points,
            "default_selection_band_points": DEFAULT_SELECTION_BAND_POINTS,
            "strike_step": 50,
            "include_vix": True,
            "futures_symbol": futures_symbol,
            "futures_expiry": futures_expiry,
            "futures_lot_size": futures_lot,
        },
        "source_master": {
            "url": "https://public.fyers.in/sym_details/NSE_FO.csv",
            "cache_file": str(cache_file),
            "bytes": master_stat.st_size,
            "mtime_utc": datetime.fromtimestamp(master_stat.st_mtime, timezone.utc).isoformat(),
            "mtime_ist": datetime.fromtimestamp(master_stat.st_mtime, IST).isoformat(),
            "sha256": sha256_file(cache_file),
            "option_rows_for_underlying": len(rows),
        },
        "notes": list(universe.notes),
        "retired_model_not_used": (
            "scripts/gate1/build_universe.py, STRIKES_EACH_SIDE, fixed strike "
            "counts, broker list-order expiry selection, and /tmp scratch "
            "output are deliberately not used here"),
    }

    stem = "provisional_universe" if args.provisional else "universe"
    universe_path = out_dir / f"{stem}.json"
    tmp = universe_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(universe_doc, indent=1, sort_keys=True))
    tmp.replace(universe_path)                       # atomic

    worktree = Path(__file__).resolve().parent
    manifest = {
        "schema": "gate1.universe_manifest/1",
        "session_id": args.session_id,
        "built_at": universe_doc["built_at"],
        "universe_file": str(universe_path),
        "universe_sha256": sha256_file(universe_path),
        "measured_symbol_count": len(instruments),
        "role": "PROVISIONAL" if args.provisional else "CANONICAL",
        "opening_buffer_points": (args.opening_buffer_points
                                  if args.provisional else 0),
        "spot_used": universe.spot,
        "spot_source": args.spot_source,
        "expiry_roles": dict(universe.roles_resolved),
        "collapsed_roles": list(universe.collapsed_roles),
        "construction": universe_doc["construction"],
        "source_master": universe_doc["source_master"],
        "built_from": {
            "code_root": args.code_root,
            "git_sha": git(Path(args.code_root), "rev-parse", "HEAD"),
            "git_branch": git(Path(args.code_root), "rev-parse", "--abbrev-ref", "HEAD"),
            "git_dirty": bool(git(Path(args.code_root), "status", "--porcelain")),
            "is_live_checkout": args.code_root.rstrip("/") == "/opt/bujji/app",
            "builder_script": str(Path(__file__).resolve()),
            "builder_sha256": sha256_file(Path(__file__).resolve()),
        },
    }
    manifest_path = out_dir / f"{stem}_manifest.json"
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    tmp.replace(manifest_path)

    print("GATE 1 " + ("PROVISIONAL (pre-open, buffered)"
                       if args.provisional else "AUTHORITATIVE") + " UNIVERSE")
    print(f"  builder        : bujji.capture_universe.builder (canonical)")
    print(f"  code root      : {args.code_root} @ {manifest['built_from']['git_sha'][:8]} "
          f"({manifest['built_from']['git_branch']})")
    if manifest["built_from"]["is_live_checkout"]:
        print("  ! WARNING: built from the LIVE CHECKOUT, not the architecture worktree")
    print(f"  spot used      : {universe.spot}  [{args.spot_source}]")
    print(f"  ATM strike     : {universe.atm_strike}")
    print(f"  expiry roles   : "
          + ", ".join(f"{r}={e}" for r, e in sorted(universe.roles_resolved.items())))
    if universe.collapsed_roles:
        print(f"  collapsed      : {list(universe.collapsed_roles)} (emitted once)")
    print(f"  tiers (points) : {dict(tiers)}"
          + (f"  [canonical {dict(DEFAULT_TIERS)} + buffer "
             f"{args.opening_buffer_points}]" if args.provisional else ""))
    print(f"  selection band : {universe.selection_band_points} points")
    print(f"  source master  : {cache_file.name} "
          f"{master_stat.st_size} B  mtime={manifest['source_master']['mtime_ist'][:19]} IST")
    print(f"                   sha256={manifest['source_master']['sha256'][:16]}...")
    print(f"  MEASURED COUNT : {len(instruments)} symbols")
    for kind, n in sorted(universe.summary().items()):
        print(f"      {kind:16} {n}")
    print(f"  universe       : {universe_path}")
    print(f"                   sha256={manifest['universe_sha256'][:16]}...")
    print(f"  manifest       : {manifest_path}")
    print()
    print("  Pass this file UNCHANGED to the harness. Rebuilding between "
          "planning and\n  measuring makes it a different experiment.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spot", type=float, required=True,
                    help="REAL observed post-open spot. No default and no "
                         "proxy: a median-strike guess centres the band on "
                         "illiquid strikes and under-measures the feed.")
    ap.add_argument("--provisional", action="store_true",
                    help="build a PRE-OPEN provisional set: same builder, every "
                         "tier widened by --opening-buffer-points. Not "
                         "authoritative; exists only to avoid an opening gap.")
    ap.add_argument("--opening-buffer-points", type=int, default=500,
                    help="index points added to EVERY tier in provisional mode. "
                         "500 on a ~24,000 index is ~2%%, which covers a large "
                         "overnight gap. Containment holds while |open - prior| "
                         "stays inside this.")
    ap.add_argument("--spot-source", required=True,
                    help="How the spot was observed, recorded verbatim in the "
                         "manifest (e.g. 'NSE:NIFTY50-INDEX LTP 09:16:04 IST').")
    ap.add_argument("--session-id", required=True)
    ap.add_argument("--out", default="/opt/bujji/gate1")
    ap.add_argument("--code-root", default="/opt/bujji/work-m4")
    ap.add_argument("--master-cache", default="/opt/bujji/gate1/master_cache")
    ap.add_argument("--underlying", default="NIFTY")
    ap.add_argument("--as-of", default=None)
    ap.add_argument("--selection-band-points", type=int, default=None)
    args = ap.parse_args()

    sys.path.insert(0, args.code_root)
    if args.selection_band_points is None:
        from bujji.capture_universe.builder import DEFAULT_SELECTION_BAND_POINTS
        args.selection_band_points = DEFAULT_SELECTION_BAND_POINTS
    return asyncio.run(build(args))


if __name__ == "__main__":
    raise SystemExit(main())
