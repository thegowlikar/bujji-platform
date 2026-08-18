"""Phase 17F.1.2.2 -- FYERS depth() response shape discovery.

NOT collection. NOT storage in Layer 0. NOT materialization. NOT a
certification (it produces no CERTIFIED_AVAILABLE/NOT_CERTIFIED verdict
-- that is a separate, later step once the shape below is known). This
script exists to answer exactly one question with a REAL, dated
response: **what does FYERS's 'depth' action actually return, field by
field, for a futures contract and for an option contract?**

WHY THIS EXISTS: `FyersBroker.get_depth()` (bujji/broker/fyers.py) is a
deliberate raw pass-through -- it does not rename or reshape anything,
specifically so that no one (including a future session of this
assistant) fabricates a `bids`/`asks` field mapping from a guess. Layer
0's `MARKET_DEPTH` payload REQUIRES exactly `bids`/`asks` keys
(`market_reality.taxonomy.REQUIRED_PAYLOAD_FIELDS`), and this codebase
has only ever live-verified `oi`/`pdoi`/`ltp` in a depth response
(`get_futures_quote`, 2026-08-12). Everything else -- the bid/ask ladder
shape, per-level fields, number of levels, timestamp semantics, and
critically whether a depth response is a FULL snapshot or an
INCREMENTAL update -- is unverified. This script's entire job is to make
that verification real, once, on both a futures and an option contract,
and write down exactly what came back.

WHAT THIS SCRIPT DOES NOT DO: normalize, rename, or interpret ANY field.
The raw response is captured byte-for-byte (as parsed JSON) into
`data_certification/fyers_depth_discovery_<YYYYMMDD>.json`. A structural
REPORT is printed/logged alongside it (key names, types, example values,
a futures-vs-option comparison table) -- but the report is observational
commentary on the raw capture, never a substitute for it. Anyone
building the real MARKET_DEPTH field mapping afterward should read the
raw JSON, not just this script's report.

SNAPSHOT VS INCREMENTAL: this script polls the SAME symbol twice, a few
seconds apart, and diffs the two raw responses' key sets and (where
present) an ordered list field's length -- enough to give a strong
signal ("the second poll's bid/ask arrays are structurally similar in
length/shape to the first, both apparently reflecting the full current
book" vs "the second response is missing most of the first response's
keys, suggesting an incremental delta"), but this is evidence toward an
answer, not a proof. A human should read BOTH raw captures before
concluding either way -- the materializer logic for a full-snapshot feed
and an incremental-delta feed are fundamentally different, and getting
this wrong would silently corrupt every OI/depth observation built on
top of it.

MARKET-HOURS GATED, same discipline as every other collector/
certification script in this project: outside 09:15-15:30 IST Mon-Fri,
this aborts without opening a connection or writing anything.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/discover_depth_response_shape.py \\
        --option-symbol NSE:NIFTY24500CE

The futures symbol is resolved automatically (via the same
`_futures_symbol()` FYERS convention `get_futures_quote()` uses). The
OPTION symbol is NOT auto-constructed -- this codebase has no
live-verified option-symbol builder (only `get_option_chain()`, which
takes an underlying + strike count, never an individual tradable
symbol), so guessing one would repeat the exact mistake this script
exists to prevent. Pass the real, currently-tradable option symbol
explicitly; the operator is expected to source it from a real option
chain response or the FYERS symbol master.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERT_DIR = REPO_ROOT / "data_certification"

# A second poll this many seconds after the first, for the
# snapshot-vs-incremental signal described in the module docstring.
SECOND_POLL_DELAY_SECONDS = 5.0

LOG = logging.getLogger("discover_depth_response_shape")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _describe(value: Any, depth: int = 0, max_depth: int = 4) -> Any:
    """A pure, read-only structural description of a JSON-shaped value:
    type name, and for containers, a recursive description of their
    shape -- never a normalization, never a rename."""
    if depth >= max_depth:
        return f"<{type(value).__name__}, depth-limited>"
    if isinstance(value, dict):
        return {k: _describe(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "<empty list>"
        return [_describe(value[0], depth + 1, max_depth), f"...({len(value)} items total)"]
    return type(value).__name__


def _structural_report(label: str, raw_row: Optional[dict]) -> dict:
    if raw_row is None:
        return {"label": label, "row_present": False}
    return {
        "label": label,
        "row_present": True,
        "top_level_keys": sorted(raw_row.keys()),
        "shape": _describe(raw_row),
        "example": raw_row,
    }


def _diff_poll_pair(first: Optional[dict], second: Optional[dict]) -> dict:
    """Evidence (not proof, see module docstring) toward snapshot-vs-
    incremental: compares key sets and, for any list-valued field present
    in both, compares lengths."""
    if first is None or second is None:
        return {"comparable": False, "reason": "one or both polls returned no row"}
    keys_first = set(first.keys())
    keys_second = set(second.keys())
    list_length_deltas = {}
    for key in keys_first & keys_second:
        if isinstance(first[key], list) and isinstance(second[key], list):
            list_length_deltas[key] = {
                "first_len": len(first[key]), "second_len": len(second[key]),
            }
    return {
        "comparable": True,
        "keys_only_in_first": sorted(keys_first - keys_second),
        "keys_only_in_second": sorted(keys_second - keys_first),
        "shared_list_field_length_deltas": list_length_deltas,
        "note": (
            "Structurally similar key sets and list lengths across both "
            "polls are WEAK EVIDENCE the response is a full current-book "
            "snapshot each time. Missing/shrunk keys on the second poll "
            "are WEAK EVIDENCE of an incremental/delta feed. A human must "
            "read both raw captures before concluding either way -- see "
            "the module docstring."
        ),
    }


async def _poll_symbol(broker, symbol: str) -> Optional[dict]:
    try:
        return await broker.get_depth(symbol)
    except Exception as exc:  # noqa: BLE001
        LOG.error("get_depth(%s) raised: %s", symbol, exc)
        return None


async def run(*, option_symbol: Optional[str]) -> int:
    started = now_ist()
    if not within_market_hours(started):
        LOG.warning(
            "Outside market hours (%s IST) -- aborting without opening a "
            "connection or writing anything.", started.isoformat(),
        )
        return 1

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker, _futures_symbol
    from bujji.core.config import AppConfig

    logging.basicConfig(level=logging.INFO)
    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, LOG)

    try:
        await broker.connect()
    except AuthenticationError as exc:
        LOG.error("token invalid, aborting: %s", exc)
        return 3

    futures_symbol = _futures_symbol("NIFTY")
    LOG.info(
        "Discovery targets: futures=%s option=%s",
        futures_symbol, option_symbol or "<none supplied, skipping option leg>",
    )

    # -- Futures: two polls, SECOND_POLL_DELAY_SECONDS apart, for the
    # snapshot-vs-incremental signal.
    futures_first = await _poll_symbol(broker, futures_symbol)
    await asyncio.sleep(SECOND_POLL_DELAY_SECONDS)
    futures_second = await _poll_symbol(broker, futures_symbol)

    option_first = option_second = None
    if option_symbol:
        option_first = await _poll_symbol(broker, option_symbol)
        await asyncio.sleep(SECOND_POLL_DELAY_SECONDS)
        option_second = await _poll_symbol(broker, option_symbol)

    report = {
        "captured_at_ist": started.isoformat(),
        "futures_symbol": futures_symbol,
        "option_symbol": option_symbol,
        "futures": {
            "poll_1": _structural_report("futures_poll_1", futures_first),
            "poll_2": _structural_report("futures_poll_2", futures_second),
            "snapshot_vs_incremental_signal": _diff_poll_pair(futures_first, futures_second),
        },
        "option": (
            {
                "poll_1": _structural_report("option_poll_1", option_first),
                "poll_2": _structural_report("option_poll_2", option_second),
                "snapshot_vs_incremental_signal": _diff_poll_pair(option_first, option_second),
            }
            if option_symbol else {"skipped": "no --option-symbol supplied"}
        ),
        "raw_responses": {
            "futures_poll_1": futures_first, "futures_poll_2": futures_second,
            "option_poll_1": option_first, "option_poll_2": option_second,
        },
    }

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CERT_DIR / f"fyers_depth_discovery_{started.strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    LOG.info("wrote %s", out_path)

    print(json.dumps({
        "futures_top_level_keys": report["futures"]["poll_1"].get("top_level_keys"),
        "option_top_level_keys": report["option"].get("poll_1", {}).get("top_level_keys")
                                   if option_symbol else None,
        "futures_snapshot_vs_incremental_signal": report["futures"]["snapshot_vs_incremental_signal"],
        "option_snapshot_vs_incremental_signal": (
            report["option"]["snapshot_vs_incremental_signal"] if option_symbol else None
        ),
        "full_report_written_to": str(out_path),
    }, indent=2, default=str))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--option-symbol", default=None,
        help="A real, currently-tradable option symbol (e.g. NSE:NIFTY24500CE). "
             "Not auto-constructed -- see the module docstring for why.",
    )
    args = parser.parse_args()
    return asyncio.run(run(option_symbol=args.option_symbol))


if __name__ == "__main__":
    raise SystemExit(main())
