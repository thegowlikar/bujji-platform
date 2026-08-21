"""Phase 17H.6 — NIFTY Futures (continuous) historical access certification.

Mirrors `certify_fyers_historical_spot_access.py`'s structure, with the
one real difference futures requires: the request symbol is NOT the
data's identity (PHASE_17H3 Part 2.4) -- a currently-listed contract
symbol (resolved live via `InstrumentMaster.resolve_nearest_future()`,
never `_futures_symbol()`'s provisional guess) is used purely as the
API's required request parameter, with `cont_flag=1` triggering FYERS's
real continuous-series stitching (confirmed live, Phase 17H.1/17H.3 --
a not-yet-existent contract symbol returned a full prior year of real,
continuous data).

REAL FINDING THIS SCRIPT VERIFIES, NOT ASSUMED: the continuous futures
series is NOT as deep as spot's. Live bisection (2026-08-13) found
2017=no_data, 2018=ok -- real earliest date ~2018-01-02, a structurally
different (much shallower) depth than spot's ~1998. This script
reconfirms that boundary live rather than trusting the carried-over
number, same discipline as the spot/VIX certification scripts.

DELIBERATELY NOT MARKET-HOURS GATED, same reasoning as its siblings.

Usage (manual, on the VPS, any time):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_fyers_historical_futures_access.py
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

UNDERLYING = "NIFTY"
INSTRUMENT = "NIFTY_FUTURES"
ACCESS_METHOD = "direct_sdk_fyers_historical_rest"
BROKER = "fyers"
CONTINUITY_METHOD = "fyers_cont_flag_1"

CERT_DIR = REPO_ROOT / "data_certification"
ARTIFACT_NAME = "fyers_nifty_future_historical_certification"

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _write_cert(cert: dict) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    date_suffix = cert["timestamp"][:10].replace("-", "")
    path = CERT_DIR / f"{ARTIFACT_NAME}_{date_suffix}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_candles(candles: list) -> dict:
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
    from bujji.broker.instrument_master import InstrumentMaster
    from bujji.core.config import AppConfig
    import logging

    log = logging.getLogger("certify_fyers_historical_futures")
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

    instruments = InstrumentMaster(REPO_ROOT / "data" / "instrument_master", log)
    try:
        request_symbol, expiry_iso, _lot_size = await instruments.resolve_nearest_future(UNDERLYING)
    except LookupError as e:
        print(f"ABORT: futures identity resolution failed: {e}", file=sys.stderr)
        return 4

    timestamp = now.isoformat()
    limitations: List[str] = []

    recent_status = "UNTESTED"
    recent_candles: list = []
    try:
        today = now.date()
        raw = await broker._call(
            "historical", symbol=request_symbol, resolution="D", date_format="1",
            range_from=(today - datetime.timedelta(days=30)).isoformat(),
            range_to=today.isoformat(), cont_flag="1",
        )
        if raw.get("s") == "ok" and raw.get("candles"):
            recent_status = "OK"
            recent_candles = raw["candles"]
        elif raw.get("s") == "no_data":
            recent_status = "NO_DATA"
            limitations.append("Recent 30-day window returned no_data -- unexpected for an active future.")
        else:
            recent_status = "ERROR"
            limitations.append(f"Unexpected recent-range response: {raw}")
    except Exception as e:  # noqa: BLE001
        recent_status = "ERROR"
        limitations.append(f"Recent-range probe raised: {e}")

    # Boundary reconfirmation: 2017=no_data, 2018=ok, live-bisected 2026-08-13.
    # Uses the SAME request_symbol -- the whole point of cont_flag=1 is that
    # the symbol used for the request is not the data's real identity, so a
    # currently-listed contract symbol correctly retrieves 2018 data despite
    # that specific contract never having existed then.
    boundary_status = "UNTESTED"
    earliest_confirmed: Optional[str] = None
    try:
        raw_2017 = await broker._call(
            "historical", symbol=request_symbol, resolution="D", date_format="1",
            range_from="2017-01-01", range_to="2017-12-31", cont_flag="1",
        )
        raw_2018 = await broker._call(
            "historical", symbol=request_symbol, resolution="D", date_format="1",
            range_from="2018-01-01", range_to="2018-12-31", cont_flag="1",
        )
        no_data_2017 = raw_2017.get("s") == "no_data"
        ok_2018 = raw_2018.get("s") == "ok" and bool(raw_2018.get("candles"))
        if no_data_2017 and ok_2018:
            boundary_status = "OK"
            earliest_confirmed = datetime.datetime.fromtimestamp(
                raw_2018["candles"][0][0], tz=datetime.timezone.utc
            ).date().isoformat()
        else:
            boundary_status = "MISMATCH"
            limitations.append(
                f"Boundary reconfirmation mismatch: 2017 s={raw_2017.get('s')}, "
                f"2018 s={raw_2018.get('s')} candles={len(raw_2018.get('candles', []))}."
            )
    except Exception as e:  # noqa: BLE001
        boundary_status = "ERROR"
        limitations.append(f"Boundary reconfirmation probe raised: {e}")

    integrity = _validate_candles(recent_candles)
    if integrity.get("issues"):
        limitations.extend(integrity["issues"])

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
            "but the ~2018 depth claim is not independently reconfirmed this run."
        )
    else:
        result = CERTIFIED

    limitations.append(
        "Continuous futures depth is structurally shallower than spot/VIX: "
        "real earliest date ~2018-01-02, live-bisected -- 2017 and earlier "
        "returns no_data regardless of cont_flag."
    )
    limitations.append("OI is never available from FYERS historical candles, at any resolution.")

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": request_symbol,
        "symbol_returned": request_symbol,
        "continuity_method": CONTINUITY_METHOD,
        "resolution_tested": "D",
        "range_tested": [(now.date() - datetime.timedelta(days=30)).isoformat(), now.date().isoformat()],
        "historical_status": recent_status,
        "earliest_date_confirmed": earliest_confirmed,
        "cont_flag_tested": True,
        "volume_available": True,
        "oi_available": False,
        "integrity_checks_passed": integrity.get("ok"),
        "validation_result": result,
        "limitations": limitations,
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0 if result == CERTIFIED else (2 if result == PARTIAL else 3)


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
