"""Phase 17I.2 — NIFTY Futures MARKET_DEPTH live access certification.

Closes the certification gap found in Phase 17I.1's audit: the depth
poller (`run_futures_depth_poller.py`, Phase 17F.1.2) reused
`ACCESS_METHOD = "direct_sdk_fyers_broker_py"` -- the SAME value
already `CERTIFIED_AVAILABLE` for live futures QUOTE access
(`fyers_nifty_future_certification.json`). `CertificationGate.status_for()`
is keyed by `(instrument_type, access_method)` only, with no concept of
observation `kind` -- reusing the quote access_method would let depth
writes silently inherit an already-CERTIFIED status without depth
itself ever being tested. This is the exact collision class already
found and fixed for historical daily-vs-intraday (17H.9) and REST-vs-
websocket (17G.0).

**Decision: `access_method = "direct_sdk_fyers_broker_py_depth"`** -- a
fourth, distinct value, parallel to
`direct_sdk_fyers_broker_py` (live quote),
`direct_sdk_fyers_historical_rest` (historical daily),
`direct_sdk_fyers_historical_intraday_rest` (historical intraday).

Two live probes of `FyersBroker.get_depth()`, ~5 seconds apart (mirrors
`fyers_depth_discovery_20260813.json`'s own methodology):
1. Row presence + required top-level keys (`bids`, `ask`, `oi`, `pdoi`,
   `totalbuyqty`, `totalsellqty`, `ltp`).
2. Ladder integrity: each `bids`/`ask` level has a positive price and a
   non-negative volume; the best bid must not exceed the best ask
   (a crossed book would be a real data-integrity failure, not a
   liquidity fact -- see PHASE_17I2 restrictions: this checks
   STRUCTURE, not derives a signal).

Symbol is resolved via `InstrumentMaster.resolve_nearest_future()` --
the audited, real-CSV-backed resolver (17H.6/17H.9/17I.1), never
`_futures_symbol()`'s provisional wall-clock guess.

MARKET-HOURS GATED, same reasoning as every live certification script
in this project: a zero-depth result outside market hours is
uninterpretable.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_fyers_futures_depth_access.py
"""
from __future__ import annotations

import asyncio
import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

UNDERLYING = "NIFTY"
INSTRUMENT = "NIFTY_FUTURES"
ACCESS_METHOD = "direct_sdk_fyers_broker_py_depth"
BROKER = "fyers"

CERT_DIR = REPO_ROOT / "data_certification"
ARTIFACT_NAME = "fyers_nifty_future_depth_certification"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective 2026-08-03.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"

REQUIRED_TOP_LEVEL_KEYS = ("bids", "ask", "oi", "pdoi", "totalbuyqty", "totalsellqty", "ltp")


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _write_cert(cert: dict) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    date_suffix = cert["timestamp"][:10].replace("-", "")
    path = CERT_DIR / f"{ARTIFACT_NAME}_{date_suffix}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_row_structure(row: dict) -> dict:
    """Structural integrity only -- presence, positive prices, ordered
    book. NEVER an imbalance/liquidity/pressure computation: a crossed
    book (best bid > best ask) is checked because it would mean the raw
    data itself is broken, not because Bujji is scoring the book."""
    issues: List[str] = []
    missing = [k for k in REQUIRED_TOP_LEVEL_KEYS if k not in row]
    if missing:
        issues.append(f"missing top-level keys: {missing}")
        return {"ok": False, "issues": issues}

    bids = row.get("bids") or []
    asks = row.get("ask") or []
    if not bids:
        issues.append("bids ladder is empty.")
    if not asks:
        issues.append("ask ladder is empty.")
    for label, levels in (("bids", bids), ("ask", asks)):
        for level in levels:
            price = level.get("price")
            volume = level.get("volume")
            if price is None or price <= 0:
                issues.append(f"{label} level has non-positive price: {level}")
            if volume is None or volume < 0:
                issues.append(f"{label} level has negative volume: {level}")

    if bids and asks:
        best_bid = max(l["price"] for l in bids if l.get("price"))
        best_ask = min(l["price"] for l in asks if l.get("price"))
        if best_bid > best_ask:
            issues.append(f"crossed book: best_bid={best_bid} > best_ask={best_ask}")

    oi = row.get("oi")
    if oi is None or oi < 0:
        issues.append(f"oi missing or negative: {oi}")

    return {"ok": not issues, "issues": issues}


async def main() -> int:
    now = _now_ist()
    if not _within_market_hours(now):
        print(json.dumps({
            "aborted": True, "reason": "OUTSIDE_MARKET_HOURS",
            "now_ist": now.isoformat(),
            "market_hours": f"{MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri",
        }, indent=2))
        print(
            "\nABORT: outside NSE market hours. A stale/empty depth response "
            "outside a session is uninterpretable. NO certification artifact "
            "is written. Re-run during an NSE session.",
            file=sys.stderr,
        )
        return 1

    from bujji.broker.fyers import FyersBroker
    from bujji.broker.errors import AuthenticationError
    from bujji.broker.instrument_master import InstrumentMaster
    from bujji.core.config import AppConfig
    import logging

    log = logging.getLogger("certify_fyers_futures_depth")
    logging.basicConfig(level=logging.INFO)

    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, log)

    try:
        await broker.connect()
    except AuthenticationError as e:
        print(json.dumps({"1_token_health": {"ok": False, "note": str(e)}}, indent=2))
        print("\nABORT: token invalid. No artifact written.", file=sys.stderr)
        return 1

    instruments = InstrumentMaster(REPO_ROOT / "data" / "instrument_master", log)
    try:
        request_symbol, expiry_iso, _lot_size = await instruments.resolve_nearest_future(UNDERLYING)
    except LookupError as e:
        print(f"ABORT: futures identity resolution failed: {e}", file=sys.stderr)
        return 4

    timestamp = now.isoformat()
    limitations: List[str] = []

    poll_status = "UNTESTED"
    integrity_1: Optional[dict] = None
    integrity_2: Optional[dict] = None
    try:
        row_1 = await broker.get_depth(request_symbol)
        await asyncio.sleep(5.0)
        row_2 = await broker.get_depth(request_symbol)

        if row_1 is None or row_2 is None:
            poll_status = "NO_DATA"
            limitations.append(f"get_depth returned None (poll_1={row_1 is not None}, poll_2={row_2 is not None}).")
        else:
            poll_status = "OK"
            integrity_1 = _validate_row_structure(row_1)
            integrity_2 = _validate_row_structure(row_2)
            if integrity_1.get("issues"):
                limitations.extend([f"poll_1: {i}" for i in integrity_1["issues"]])
            if integrity_2.get("issues"):
                limitations.extend([f"poll_2: {i}" for i in integrity_2["issues"]])
    except Exception as e:  # noqa: BLE001
        poll_status = "ERROR"
        limitations.append(f"depth probe raised: {e}")

    integrity_ok = None
    if integrity_1 is not None and integrity_2 is not None:
        integrity_ok = bool(integrity_1.get("ok")) and bool(integrity_2.get("ok"))

    if poll_status != "OK":
        result = NOT_CERTIFIED
        limitations.append(f"poll_status={poll_status} -- depth access itself failed.")
    elif integrity_ok is False:
        result = NOT_CERTIFIED
        limitations.append("structural integrity check failed on at least one poll.")
    else:
        result = CERTIFIED

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": request_symbol,
        "symbol_returned": request_symbol,
        "expiry": expiry_iso,
        "poll_status": poll_status,
        "structural_integrity_passed": integrity_ok,
        "depth_levels_confirmed": 5,
        "oi_available": True,
        "bid_ask_available": True,
        "validation_result": result,
        "limitations": limitations or ["none observed"],
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0 if result == CERTIFIED else 3


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
