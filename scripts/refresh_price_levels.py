#!/usr/bin/env python
"""Daily price-levels refresh.

Builds one dated snapshot of levels and zones from the completed bars in the
normalized store, so the trading session reads a map instead of re-deriving
one inside every five-minute decision cycle.

RUNS AFTER THE CLOSE, on purpose. It reads finished bars; running it mid-session
would build a map from a partly-formed final bar, and the last "level" would be
an artifact of when the job happened to run.

OBSERVATION ONLY. Nothing consumes the snapshot yet. This job produces
evidence; wiring it into the thesis is L-5 and then an operator gate.

Exit codes: 0 built (including an honest INSUFFICIENT_HISTORY snapshot),
2 no usable bars at all, 3 unexpected failure.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.clock import now_ist  # noqa: E402
from bujji.price_levels.daily import (  # noqa: E402
    DEFAULT_BAR_LIMIT, DEFAULT_SNAPSHOT_DIR, build_snapshot, write_snapshot,
)
from bujji.price_levels.store_reader import DEFAULT_DB_PATH  # noqa: E402

LOG = logging.getLogger("bujji-price-levels-refresh")

EXIT_OK = 0
EXIT_NO_BARS = 2
EXIT_FAILURE = 3


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", default="NSE:NIFTY50-INDEX")
    parser.add_argument("--resolution", default="FIVE_MINUTE")
    parser.add_argument("--bar-limit", type=int, default=DEFAULT_BAR_LIMIT,
                        help="most recent N bars to build from (cost control, not a decay rule)")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH)
    parser.add_argument("--output-dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--built-for", default=None,
                        help="trading date this snapshot serves (default: today, IST)")
    parser.add_argument("--as-of", default=None,
                        help="no-lookahead cut; bars at or after this instant are ignored")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
    args = parse_args(argv)
    now = now_ist()
    built_for = args.built_for or now.date().isoformat()

    try:
        snapshot = build_snapshot(
            built_for=built_for, built_at=now.isoformat(),
            instrument=args.instrument, resolution=args.resolution,
            db_path=args.db_path, bar_limit=args.bar_limit, as_of=args.as_of,
        )
    except Exception as exc:  # noqa: BLE001 -- process boundary
        LOG.exception("price-levels refresh failed: %s", exc)
        return EXIT_FAILURE

    load = snapshot.load
    if not load["bars"]:
        LOG.error("no usable OHLC bars for %s/%s (rows_seen=%d skipped_not_a_bar=%d) -- "
                  "refusing to write an empty map.",
                  args.instrument, args.resolution, load["rows_seen"], load["skipped_not_a_bar"])
        return EXIT_NO_BARS

    # Loud, because it is the one number that says "you are reading the wrong
    # series": live point samples cannot form structure and are skipped.
    if load["skipped_not_a_bar"] > load["bars"]:
        LOG.warning("skipped %d non-bar rows against only %d usable bars -- check the series; "
                    "the map below is built on thin evidence.",
                    load["skipped_not_a_bar"], load["bars"])

    path = write_snapshot(snapshot, args.output_dir)
    lv, zn = snapshot.levels, snapshot.zones
    LOG.info("levels: status=%s %d published (%d pivots -> %d survived agreement)",
             lv.status, len(lv.levels), lv.swings_before_agreement, lv.swings_after_agreement)
    LOG.info("zones : status=%s %d published (%d live) from %d candidates",
             zn.status, len(zn.zones), len(zn.live_zones), zn.zones_before_agreement)
    LOG.info("snapshot written: %s (bars=%d skipped=%d)",
             path, load["bars"], load["skipped_not_a_bar"])
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
