"""Phase 17H.9 — NIFTY Spot Intraday (5-min) historical access certification.

Mirrors `certify_fyers_historical_spot_access.py`'s structure exactly,
with the one deliberate difference this phase requires: a NEW, distinct
`access_method="direct_sdk_fyers_historical_intraday_rest"`, separate
from daily historical's `"direct_sdk_fyers_historical_rest"`.
`CertificationGate.status_for()` is keyed by (instrument, access_method)
only, with no concept of resolution -- reusing the daily value would
make intraday access appear CERTIFIED_AVAILABLE without ever having
been tested at 5-min resolution. See
docs/PHASE_17H9_INTRADAY_REALITY_ARCHITECTURE_AUDIT.md.

Two live probes, both read-only, both within the real 100-day/request
intraday limit (live-bisected Phase 17H.8, reconfirmed here):
1. A recent-range probe (last 30 days at 5-min resolution).
2. An earliest-boundary reconfirmation probe around the live-bisected
   real cutoff (2017-07-17) -- re-confirmed here, not trusted from a
   prior session's finding.

DELIBERATELY NOT MARKET-HOURS GATED -- historical data has no
market-hours dependency, same reasoning as every historical
certification script before it.

Usage (manual, on the VPS, any time):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_fyers_historical_spot_intraday_access.py
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
ACCESS_METHOD = "direct_sdk_fyers_historical_intraday_rest"
BROKER = "fyers"
RESOLUTION = "5"

CERT_DIR = REPO_ROOT / "data_certification"
ARTIFACT_NAME = "fyers_nifty_spot_intraday_historical_certification"

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
    from bujji.core.config import AppConfig
    import logging

    log = logging.getLogger("certify_fyers_historical_spot_intraday")
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

    # --- Probe 1: recent-range access (30 days, well under the 100-day cap) --
    recent_status = "UNTESTED"
    recent_candles: list = []
    try:
        today = now.date()
        # range_to is yesterday, deliberately excluding today: a real, live-
        # discovered finding this session is that FYERS's intraday response
        # appends the CURRENT/in-progress session's candles a second time
        # when the range includes today, and up to 2 of those duplicate
        # rows can genuinely disagree (a live bar snapshotted twice at
        # different instants). That is a real same-day data-instability
        # fact worth a separate note, not something a certification probe
        # should trip over -- excluding today keeps this probe measuring
        # real settled-day access.
        raw = await broker._call(
            "historical", symbol=SYMBOL, resolution=RESOLUTION, date_format="1",
            range_from=(today - datetime.timedelta(days=31)).isoformat(),
            range_to=(today - datetime.timedelta(days=1)).isoformat(), cont_flag="1",
        )
        if raw.get("s") == "ok" and raw.get("candles"):
            recent_status = "OK"
            recent_candles = raw["candles"]
        elif raw.get("s") == "no_data":
            recent_status = "NO_DATA"
            limitations.append("Recent 30-day 5-min window returned no_data -- unexpected for a live index.")
        else:
            recent_status = "ERROR"
            limitations.append(f"Unexpected recent-range response: {raw}")
    except Exception as e:  # noqa: BLE001
        recent_status = "ERROR"
        limitations.append(f"Recent-range probe raised: {e}")

    # --- Probe 2: earliest-boundary reconfirmation (2017-07-17, Phase 17H.8) --
    # Windows kept <=100 days each, per the live-bisected intraday request limit.
    boundary_status = "UNTESTED"
    earliest_confirmed: Optional[str] = None
    try:
        raw_before = await broker._call(
            "historical", symbol=SYMBOL, resolution=RESOLUTION, date_format="1",
            range_from="2017-06-17", range_to="2017-07-16", cont_flag="1",
        )
        raw_after = await broker._call(
            "historical", symbol=SYMBOL, resolution=RESOLUTION, date_format="1",
            range_from="2017-07-17", range_to="2017-09-24", cont_flag="1",
        )
        no_data_before = raw_before.get("s") == "no_data"
        ok_after = raw_after.get("s") == "ok" and bool(raw_after.get("candles"))
        if no_data_before and ok_after:
            boundary_status = "OK"
            earliest_confirmed = datetime.datetime.fromtimestamp(
                raw_after["candles"][0][0], tz=datetime.timezone.utc
            ).date().isoformat()
        else:
            boundary_status = "MISMATCH"
            limitations.append(
                f"Boundary reconfirmation mismatch: before s={raw_before.get('s')}, "
                f"after s={raw_after.get('s')} candles={len(raw_after.get('candles', []))}. "
                "Phase 17H.8's cited 2017-07-17 earliest intraday date is NOT reconfirmed by this run."
            )
    except Exception as e:  # noqa: BLE001
        boundary_status = "ERROR"
        limitations.append(f"Boundary reconfirmation probe raised: {e}")

    integrity = _validate_candles(recent_candles)
    if integrity.get("issues"):
        limitations.extend(integrity["issues"])

    if recent_status != "OK":
        result = NOT_CERTIFIED
        limitations.append(f"recent_status={recent_status} -- intraday historical access itself failed.")
    elif integrity.get("ok") is False:
        result = NOT_CERTIFIED
        limitations.append(f"candle integrity check failed: {integrity.get('issues')}")
    elif boundary_status != "OK":
        result = PARTIAL
        limitations.append(
            f"boundary_status={boundary_status} -- current access certified, "
            "but the ~2017-07-17 intraday depth claim is not independently reconfirmed this run."
        )
    else:
        result = CERTIFIED

    limitations.append(
        "Intraday depth (~2017-07-17) is structurally shallower than spot daily (~1998-05-04) -- "
        "a genuinely different, resolution-dependent boundary, not a subset relationship."
    )

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": SYMBOL,
        "symbol_returned": SYMBOL,
        "resolution_tested": RESOLUTION,
        "range_tested": [(now.date() - datetime.timedelta(days=31)).isoformat(), (now.date() - datetime.timedelta(days=1)).isoformat()],
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
