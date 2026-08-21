"""Phase 17I.6.2 — Live FYERS Raw Response Discovery.

DISCOVERY ONLY. Uses the Phase 17I.6.1 raw broker methods
(`get_spot_raw()`/`get_futures_quote_raw()`/`get_vix_raw()`) to inspect
what FYERS actually sends, before Bujji interprets or normalizes
anything.

Writes NOTHING to Layer 0. Creates no `RawObservation`. Does not touch
`taxonomy.py`/`models.py`/`capture.py`. This script exists to answer
exactly one question, honestly, from real data:

    "Does FYERS provide a broker/exchange event timestamp that can
    populate RawObservation.event_timestamp?"

Does NOT assume field names. Every response is scanned for candidate
timestamp-like keys by keyword match (time/ts/date/epoch/feed), at any
nesting depth, rather than checking for a specific name decided in
advance -- the whole point is to find out what's really there.

MARKET-HOURS GATED, MANUAL LAUNCH ONLY, same discipline as every other
discovery/certification script in this project.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/discover_fyers_raw_responses.py
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Any, List

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

UNDERLYING = "NIFTY"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Keyword match, not an exact-name check -- deliberately broad so nothing
# plausible gets missed just because it wasn't the name we expected.
#
# "tt" added after the first live run of this script (2026-08-13) missed
# two real fields purely because the original keyword list didn't cover
# them: `tt` (present on every plain 'ltp' quote response) and `ltt`
# (present on 'depth' responses -- FYERS's own "last traded time",
# already informally seen in Gate B's depth discovery). Both were sitting
# in the printed raw JSON the whole time; only the automated candidate
# list missed them. Fixing the scanner's own blind spot, not adding new
# architecture -- see docs/PHASE_17I6_2_RAW_FYERS_RESPONSE_DISCOVERY.md.
_TIMESTAMP_KEYWORDS = ("time", "ts", "date", "epoch", "feed", "tt")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _all_keys(obj: Any, prefix: str = "") -> List[str]:
    """Every key path in a nested dict/list structure, e.g.
    "d[0].v.exch_feed_time" -- flat, so a human can scan the full real
    shape at a glance rather than pretty-printing nested JSON."""
    paths: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            paths.append(path)
            paths.extend(_all_keys(v, path))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            path = f"{prefix}[{i}]"
            paths.extend(_all_keys(item, path))
    return paths


def _candidate_timestamp_keys(obj: Any) -> List[str]:
    """Key paths whose LAST segment contains a timestamp-like keyword --
    a candidate list for a human to inspect, not a conclusion. Matches by
    substring on the key name only (case-insensitive); does not inspect
    values, since a plausible-looking key with a clearly non-timestamp
    value is still worth a human's eyes, not a script's guess."""
    candidates = []
    for path in _all_keys(obj):
        last_segment = path.rsplit(".", 1)[-1].split("[")[0]
        if any(kw in last_segment.lower() for kw in _TIMESTAMP_KEYWORDS):
            candidates.append(path)
    return candidates


def _report_section(title: str, response: Any) -> dict:
    return {
        "title": title,
        "all_keys": _all_keys(response),
        "candidate_timestamp_keys": _candidate_timestamp_keys(response),
        "raw_response": response,
    }


def _print_section(section: dict) -> None:
    print(f"\n{'=' * 70}\n{section['title']}\n{'=' * 70}")
    print(f"All key paths ({len(section['all_keys'])}):")
    for k in section["all_keys"]:
        print(f"  {k}")
    print(f"\nCandidate timestamp-like keys ({len(section['candidate_timestamp_keys'])}):")
    if section["candidate_timestamp_keys"]:
        for k in section["candidate_timestamp_keys"]:
            print(f"  {k}")
    else:
        print("  (none found)")
    print(f"\nFull raw response:\n{json.dumps(section['raw_response'], indent=2, default=str)}")


async def run() -> int:
    now = now_ist()
    if not within_market_hours(now):
        print(
            f"ABORT: outside NSE market hours (now={now.isoformat()}, "
            f"window={MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri). A response "
            "fetched outside a session is stale/closed-market and would "
            "make this discovery run uninterpretable. Re-run during an "
            "NSE session.",
            file=sys.stderr,
        )
        return 1

    import logging

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig

    log = logging.getLogger("discover_fyers_raw_responses")
    logging.basicConfig(level=logging.WARNING)  # Keep noise out of the report.

    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, log)

    try:
        await broker.connect()
    except AuthenticationError as exc:
        print(f"ABORT: token invalid, aborting: {exc}", file=sys.stderr)
        return 2

    sections = []

    try:
        spot_raw = await broker.get_spot_raw(UNDERLYING)
        sections.append(_report_section("SPOT -- get_spot_raw()", spot_raw))
    except Exception as exc:  # noqa: BLE001
        print(f"spot_raw fetch failed: {exc}", file=sys.stderr)

    try:
        futures_raw = await broker.get_futures_quote_raw(UNDERLYING)
        sections.append(_report_section(
            "FUTURES -- get_futures_quote_raw().ltp_response", futures_raw["ltp_response"],
        ))
        sections.append(_report_section(
            "FUTURES -- get_futures_quote_raw().depth_response", futures_raw["depth_response"],
        ))
    except Exception as exc:  # noqa: BLE001
        print(f"futures_quote_raw fetch failed: {exc}", file=sys.stderr)

    try:
        vix_raw = await broker.get_vix_raw()
        sections.append(_report_section("VIX -- get_vix_raw()", vix_raw))
    except Exception as exc:  # noqa: BLE001
        print(f"vix_raw fetch failed: {exc}", file=sys.stderr)

    print(f"\n{'#' * 70}")
    print(f"# Phase 17I.6.2 Raw FYERS Response Discovery -- {now.isoformat()}")
    print(f"{'#' * 70}")
    for section in sections:
        _print_section(section)

    print(f"\n{'=' * 70}\nSUMMARY\n{'=' * 70}")
    for section in sections:
        found = "YES" if section["candidate_timestamp_keys"] else "NO"
        print(f"{section['title']}: candidate timestamp keys found = {found}")

    return 0


def main() -> int:
    import asyncio
    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
