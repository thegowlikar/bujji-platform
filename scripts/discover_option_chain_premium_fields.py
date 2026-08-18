"""Phase 17F.7.1 -- FYERS option chain premium field discovery.

NOT collection. NOT storage in Layer 0. NOT a live MarketDataProvider
implementation. Same discipline as `discover_depth_response_shape.py`
(17F.5): this script exists to answer exactly one question with a REAL,
dated response -- **what does FYERS's 'optionchain' action actually
return, field by field, for every strike in a real chain?**

WHY THIS EXISTS: `FyersBroker.get_option_chain()` was written for one
purpose (OI reconciliation, Phase 17B) and has only ever read
`strike_price`/`option_type`/`oi` from each row -- every other key in
the real response has been silently discarded since that method was
written. The 17F.7 design audit found this is a real blocker: a live
`MarketDataProvider` must construct `OptionObservation` rows carrying a
premium price (`open`/`high`/`low`/`close`/`settlement`), and nothing in
this codebase has ever confirmed FYERS's `optionchain` response carries
those fields, under what names, or in what shape. Guessing them and
writing a `LiveChainProvider` against the guess would repeat the exact
mistake `get_depth()`/`discover_depth_response_shape.py` were built to
prevent for futures depth.

`FyersBroker.get_option_chain_raw()` is the raw, unmodified pass-through
this script calls -- it does not rename, restructure, or discard any
field FYERS returns, specifically so this script can report the truth
rather than confirm an assumption.

WHAT THIS SCRIPT DOES NOT DO: normalize, rename, or interpret ANY field.
The raw response is captured byte-for-byte (as parsed JSON) into
`data_certification/fyers_option_chain_discovery_<YYYYMMDD>.json`. A
structural REPORT is printed/logged alongside it (key names present
across the full strike range, types, example values, a CE-vs-PE
comparison, and which of the fields `OptionObservation`/
`ALL_OPTIONS_OBSERVATION_FIELDS` need are actually present) -- but the
report is observational commentary on the raw capture, never a
substitute for it.

MARKET-HOURS GATED, same discipline as every other collector/
certification/discovery script in this project: outside 09:15-15:30 IST
Mon-Fri, this aborts without opening a connection or writing anything.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/discover_option_chain_premium_fields.py
    PYTHONPATH=/opt/bujji/app python scripts/discover_option_chain_premium_fields.py --strike-count 10
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

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

DEFAULT_STRIKE_COUNT = 5
UNDERLYING = "NIFTY"

# The exact fields a real OptionObservation row (bujji.options_observation.
# models.OptionObservation, per taxonomy.ALL_OPTIONS_OBSERVATION_FIELDS)
# needs to carry a usable premium -- this list is NOT a guess at FYERS's
# field names, it is what the CONSUMER needs, so the report can say
# plainly whether the real response satisfies it or not.
_OPTION_OBSERVATION_FIELDS_NEEDED = (
    "open", "high", "low", "close", "settlement",
    "volume", "open_interest", "change_in_open_interest", "underlying_price",
)

LOG = logging.getLogger("discover_option_chain_premium_fields")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _describe(value: Any, depth: int = 0, max_depth: int = 3) -> Any:
    """Pure, read-only structural description -- never a normalization,
    never a rename. Mirrors discover_depth_response_shape.py's own
    helper exactly."""
    if depth >= max_depth:
        return f"<{type(value).__name__}, depth-limited>"
    if isinstance(value, dict):
        return {k: _describe(v, depth + 1, max_depth) for k, v in value.items()}
    if isinstance(value, list):
        if not value:
            return "<empty list>"
        return [_describe(value[0], depth + 1, max_depth), f"...({len(value)} items total)"]
    return type(value).__name__


def _extract_rows(raw: Optional[dict]) -> list:
    """Read-only navigation to the rows list -- does NOT filter, rename,
    or reshape any row. Mirrors get_option_chain()'s own (already
    live-verified, 2026-07-20) knowledge that the payload nests under
    top-level "data" -> "optionsChain", without repeating its narrow
    per-row extraction."""
    if not raw:
        return []
    return raw.get("data", {}).get("optionsChain", []) or []


def _field_presence_report(rows: list) -> dict:
    """For each field this codebase's OptionObservation actually needs,
    report whether ANY real row in this capture carried a non-null value
    for a plausible key name -- checked against the row's own real keys,
    never a fabricated mapping. This does NOT assume FYERS uses the same
    field name as OptionObservation; it reports the raw key set alongside
    so a human can make that mapping decision from fact."""
    all_keys = set()
    for row in rows:
        if isinstance(row, dict):
            all_keys.update(row.keys())
    return {
        "all_raw_keys_seen_across_every_row": sorted(all_keys),
        "option_observation_fields_needed": list(_OPTION_OBSERVATION_FIELDS_NEEDED),
        "note": (
            "This does NOT claim a mapping between FYERS's raw keys and "
            "OptionObservation's field names -- that mapping is a human "
            "decision to make from the raw keys listed above, once read. "
            "It only reports what raw keys actually exist."
        ),
    }


def _ce_pe_comparison(rows: list) -> dict:
    ce_keys = set()
    pe_keys = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        opt_type = row.get("option_type")
        if opt_type == "CE":
            ce_keys.update(row.keys())
        elif opt_type == "PE":
            pe_keys.update(row.keys())
    return {
        "ce_keys": sorted(ce_keys),
        "pe_keys": sorted(pe_keys),
        "keys_only_in_ce": sorted(ce_keys - pe_keys),
        "keys_only_in_pe": sorted(pe_keys - ce_keys),
    }


async def run(*, strike_count: int) -> int:
    started = now_ist()
    if not within_market_hours(started):
        LOG.warning(
            "Outside market hours (%s IST) -- aborting without opening a "
            "connection or writing anything.", started.isoformat(),
        )
        return 1

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
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

    LOG.info("Discovery target: underlying=%s strike_count=%s", UNDERLYING, strike_count)

    try:
        raw = await broker.get_option_chain_raw(UNDERLYING, strike_count=strike_count)
    except Exception as exc:  # noqa: BLE001
        LOG.error("get_option_chain_raw() raised: %s", exc)
        return 2

    rows = _extract_rows(raw)
    underlying_rows = [r for r in rows if isinstance(r, dict) and r.get("strike_price") == -1]
    strike_rows = [r for r in rows if isinstance(r, dict) and r.get("strike_price") != -1]

    report = {
        "captured_at_ist": started.isoformat(),
        "underlying": UNDERLYING,
        "requested_strike_count": strike_count,
        "top_level_keys": sorted(raw.keys()) if raw else [],
        "row_count_total": len(rows),
        "row_count_underlying": len(underlying_rows),
        "row_count_strikes": len(strike_rows),
        "example_underlying_row": underlying_rows[0] if underlying_rows else None,
        "example_strike_row": strike_rows[0] if strike_rows else None,
        "shape": _describe(raw),
        "field_presence": _field_presence_report(strike_rows),
        "ce_vs_pe_key_comparison": _ce_pe_comparison(strike_rows),
    }

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = CERT_DIR / f"fyers_option_chain_discovery_{started.strftime('%Y%m%d')}.json"
    out_path.write_text(json.dumps({"report": report, "raw_response": raw}, indent=2, default=str))
    LOG.info("wrote %s", out_path)

    print(json.dumps({
        "top_level_keys": report["top_level_keys"],
        "row_count_strikes": report["row_count_strikes"],
        "all_raw_keys_seen_across_every_row": report["field_presence"]["all_raw_keys_seen_across_every_row"],
        "ce_vs_pe_key_comparison": report["ce_vs_pe_key_comparison"],
        "full_report_written_to": str(out_path),
    }, indent=2, default=str))

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strike-count", type=int, default=DEFAULT_STRIKE_COUNT,
        help="Strikes on each side of ATM to request (passed through to strikecount).",
    )
    args = parser.parse_args()
    return asyncio.run(run(strike_count=args.strike_count))


if __name__ == "__main__":
    raise SystemExit(main())
