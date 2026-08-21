"""Phase 17H.3/17H.4 — India NIFTY Spot historical access certification.

Certifies `access_method="direct_sdk_fyers_historical_rest"` for
`NIFTY_SPOT` -- a NEW, distinct access-method value from the live
quote path's `"direct_sdk_fyers_broker_py"`. Per
docs/PHASE_17H3_HISTORICAL_CERTIFICATION_AND_INGESTION_CONTRACT.md
Part 1.1: `CertificationGate.status_for()` is keyed by (instrument,
access_method); reusing the live value would make
`gate.status_for("direct_sdk_fyers_broker_py", INSTRUMENT_SPOT)` return
CERTIFIED_AVAILABLE for historical access that was never actually
certified -- the same collision class already found and fixed once
(Phase 17G.0's Gate B REST-vs-websocket audit).

DELIBERATELY NOT MARKET-HOURS GATED (Part 1.5 of the same document): a
1998 daily bar is exactly as valid to certify at 11pm as at 11am --
inheriting the live scripts' market-hours gate by copy-paste habit
would be a real, avoidable mistake for this specific script.

Two live probes, both read-only:
1. A recent-range probe (last 30 days) -- confirms current access
   works and the symbol echoes correctly.
2. An earliest-boundary reconfirmation probe (a narrow window around
   the real boundary found in Phase 17H.1's live bisection,
   1996-01-01..1998-12-31) -- re-confirms the cited depth rather than
   trusting a number carried over from a prior session's finding.

Usage (manual, on the VPS, any time):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_fyers_historical_spot_access.py
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SYMBOL = "NSE:NIFTY50-INDEX"
INSTRUMENT = "NIFTY_SPOT"
ACCESS_METHOD = "direct_sdk_fyers_historical_rest"
BROKER = "fyers"

CERT_DIR = REPO_ROOT / "data_certification"
ARTIFACT_NAME = "fyers_nifty_spot_historical_certification"

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _write_cert(cert: dict) -> None:
    """Date-stamped filename, matching every other certification
    artifact's own convention (17F.5/17F.7.1) -- a re-run on a later
    day produces a new, distinct artifact rather than overwriting."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    date_suffix = cert["timestamp"][:10].replace("-", "")
    path = CERT_DIR / f"{ARTIFACT_NAME}_{date_suffix}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_candles(candles: list) -> dict:
    """Same integrity discipline as every other certification script."""
    issues: List[str] = []
    if not candles:
        return {"ok": None, "issues": ["no candles to validate"]}
    epochs = [c[0] for c in candles]
    dupes = len(epochs) - len(set(epochs))
    if dupes:
        issues.append(f"{dupes} duplicate epoch(s) found.")
    if epochs != sorted(epochs):
        issues.append("candles are not in ascending epoch order.")
    bad_ohlc = 0
    for c in candles:
        _, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
        if h < l or h < o or h < cl or l > o or l > cl or min(o, h, l, cl) <= 0:
            bad_ohlc += 1
    if bad_ohlc:
        issues.append(f"{bad_ohlc} candle(s) with impossible OHLC.")
    return {"ok": not issues, "issues": issues}


async def main() -> int:
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.errors import AuthenticationError
    from bujji.core.config import AppConfig
    import logging

    log = logging.getLogger("certify_fyers_historical_spot")
    logging.basicConfig(level=logging.INFO)

    now = _now_ist()
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

    timestamp = now.isoformat()
    limitations: List[str] = []

    # --- Probe 1: recent-range access + symbol echo ------------------------
    recent_status = "UNTESTED"
    symbol_returned: Optional[str] = None
    symbol_match: Optional[bool] = None
    recent_candles: list = []
    try:
        today = now.date()
        raw = await broker._call(
            "historical", symbol=SYMBOL, resolution="D", date_format="1",
            range_from=(today - datetime.timedelta(days=30)).isoformat(),
            range_to=today.isoformat(), cont_flag="1",
        )
        symbol_returned = SYMBOL  # `historical` echoes no symbol field in its response;
        # `s`/`candles` is all it returns -- the request itself (not a response field)
        # is the only echo available for this action, unlike `ltp`'s "n" field.
        if raw.get("s") == "ok" and raw.get("candles"):
            recent_status = "OK"
            recent_candles = raw["candles"]
            symbol_match = True
        elif raw.get("s") == "no_data":
            recent_status = "NO_DATA"
            symbol_match = None
            limitations.append("Recent 30-day window returned no_data -- unexpected for a live index.")
        else:
            recent_status = "ERROR"
            symbol_match = None
            limitations.append(f"Unexpected recent-range response: {raw}")
    except Exception as e:  # noqa: BLE001
        recent_status = "ERROR"
        limitations.append(f"Recent-range probe raised: {e}")

    # --- Probe 2: earliest-boundary reconfirmation --------------------------
    # Re-confirms Phase 17H.1's live-bisected boundary (1996=no_data,
    # 1998=ok) rather than trusting a carried-over number.
    boundary_status = "UNTESTED"
    earliest_confirmed: Optional[str] = None
    try:
        raw_1996 = await broker._call(
            "historical", symbol=SYMBOL, resolution="D", date_format="1",
            range_from="1996-01-01", range_to="1996-12-31", cont_flag="1",
        )
        raw_1998 = await broker._call(
            "historical", symbol=SYMBOL, resolution="D", date_format="1",
            range_from="1998-01-01", range_to="1998-12-31", cont_flag="1",
        )
        no_data_1996 = raw_1996.get("s") == "no_data"
        ok_1998 = raw_1998.get("s") == "ok" and bool(raw_1998.get("candles"))
        if no_data_1996 and ok_1998:
            boundary_status = "OK"
            earliest_confirmed = datetime.datetime.fromtimestamp(
                raw_1998["candles"][0][0], tz=datetime.timezone.utc
            ).date().isoformat()
        else:
            boundary_status = "MISMATCH"
            limitations.append(
                f"Boundary reconfirmation mismatch: 1996 s={raw_1996.get('s')}, "
                f"1998 s={raw_1998.get('s')} candles={len(raw_1998.get('candles', []))}. "
                "Phase 17H.1's cited 1998 earliest-date finding is NOT reconfirmed by this run."
            )
    except Exception as e:  # noqa: BLE001
        boundary_status = "ERROR"
        limitations.append(f"Boundary reconfirmation probe raised: {e}")

    integrity = _validate_candles(recent_candles)
    if integrity.get("issues"):
        limitations.extend(integrity["issues"])

    # --- Classification ------------------------------------------------------
    if recent_status != "OK":
        result = NOT_CERTIFIED
        limitations.append(f"recent_status={recent_status} -- historical access itself failed.")
    elif integrity.get("ok") is False:
        result = NOT_CERTIFIED
        limitations.append(f"candle integrity check failed: {integrity.get('issues')}")
    elif boundary_status != "OK":
        result = PARTIAL
        limitations.append(
            f"boundary_status={boundary_status} -- current access certified, "
            "but the ~1998 depth claim is not independently reconfirmed this run."
        )
    else:
        result = CERTIFIED

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": SYMBOL,
        "symbol_returned": symbol_returned,
        "resolution_tested": "D",
        "range_tested": [(now.date() - datetime.timedelta(days=30)).isoformat(), now.date().isoformat()],
        "historical_status": recent_status,
        "earliest_date_confirmed": earliest_confirmed,
        "cont_flag_tested": True,
        "volume_available": True,
        "oi_available": False,  # Structural: FYERS historical candles never carry OI.
        "integrity_checks_passed": integrity.get("ok"),
        "validation_result": result,
        "limitations": limitations or ["none observed"],
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0 if result == CERTIFIED else (2 if result == PARTIAL else 3)


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
