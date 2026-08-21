"""Phase 17G — PREPARED, NOT-YET-EXECUTED India VIX certification.

Closes the gap found during the 17G Market Understanding audit: Domain E
(Volatility Intelligence) cannot exist because India VIX has never been
certified. `bujji/market_reality/certification.py`'s
`INSTRUMENT_TYPE_TO_CERT_KEY` has no entry for `INSTRUMENT_INDEX` at all
-- VIX cannot even be written to Layer 0 today, regardless of whether the
REST call itself works.

`FyersBroker.get_vix()` (bujji/broker/fyers.py:422) already carries a
LIVE-VERIFIED docstring claim from 2026-07-20 (lp=13.02,
prev_close_price=13.15) -- but that was an informal verification for a
different purpose (the Event Brain), predates the Phase 17A.5
certification framework entirely, and was never turned into a dated,
re-checkable artifact. This script produces that artifact, following the
exact three-state discipline (CERTIFIED_AVAILABLE / PARTIAL_CERTIFICATION
/ NOT_CERTIFIED) and symbol-echo verification already established for
spot/futures/options.

INDIA_VIX is an INDEX, same category as NIFTY spot -- so per the
Instrument Capability Registry (17F.0.4 Part 1), quote/depth/volume/OI
are all structurally NOT_APPLICABLE. This script certifies exactly what
an index CAN provide: LTP and historical daily candles. It does not
attempt bid/ask/volume and does not classify their absence as a gap --
classifying a structural non-applicability as a deficiency would
contradict the very contract this phase exists to freeze.

READ-ONLY. LTP + historical only. No order capability, no state mutation.
MARKET-HOURS GATED, by design, for the same reason as every certification
script in this project: a zero-tick/stale-quote result outside market
hours is uninterpretable and must never be recorded as a data-availability
finding.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an NSE
session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_vix_access.py
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

SYMBOL = "NSE:INDIAVIX-INDEX"
INSTRUMENT = "INDIA_VIX"
ACCESS_METHOD = "direct_sdk_fyers_broker_py"
BROKER = "fyers"

CERT_DIR = Path(__file__).resolve().parent.parent / "data_certification"
ARTIFACT_NAME = "fyers_india_vix_certification"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

CERTIFIED = "CERTIFIED_AVAILABLE"
PARTIAL = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _write_cert(cert: dict) -> None:
    """Date-stamped filename (YYYYMMDD, from this cert's own real
    `timestamp` field -- never the wall clock read separately), matching
    the discovery scripts' convention (17F.5/17F.7.1): a re-run on a
    later day produces a new, distinct artifact instead of silently
    overwriting a prior day's real certification. `CertificationGate`
    resolves multiple same-instrument artifacts by sorted filename order
    (last wins), so a later date naturally supersedes an earlier one --
    no gate-logic change required for this."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    date_suffix = cert["timestamp"][:10].replace("-", "")
    path = CERT_DIR / f"{ARTIFACT_NAME}_{date_suffix}.json"
    path.write_text(json.dumps(cert, indent=2, default=str))
    print(f"wrote {path}")


def _validate_candles(candles: list, now: datetime.datetime) -> dict:
    """Same integrity discipline as every other certification script in
    this project (no future timestamps, no duplicates, ascending order,
    no impossible OHLC) -- deliberately not reimplemented differently."""
    issues: List[str] = []
    if not candles:
        return {"ok": None, "issues": ["no candles to validate"]}
    timestamps = [c.timestamp for c in candles]
    future = [t for t in timestamps if t > now]
    if future:
        issues.append(f"{len(future)} candle(s) with a future timestamp (e.g. {future[0]}).")
    dupes = len(timestamps) - len(set(timestamps))
    if dupes:
        issues.append(f"{dupes} duplicate timestamp(s) found.")
    if timestamps != sorted(timestamps):
        issues.append("candles are not in ascending timestamp order.")
    bad_ohlc = 0
    for c in candles:
        if c.high < c.low or c.high < c.open or c.high < c.close \
           or c.low > c.open or c.low > c.close \
           or min(c.open, c.high, c.low, c.close) <= 0:
            bad_ohlc += 1
    if bad_ohlc:
        issues.append(f"{bad_ohlc} candle(s) with impossible OHLC.")
    return {"ok": not issues, "issues": issues}


def _classify(*, symbol_match: Optional[bool], quote_status: str,
              historical_status: str, integrity: dict) -> tuple:
    reasons: List[str] = []

    if symbol_match is False:
        return NOT_CERTIFIED, [
            "symbol_requested != symbol_returned -- hard fail regardless "
            "of any other signal."
        ]
    if quote_status != "OK" and historical_status != "OK":
        return NOT_CERTIFIED, [
            f"neither quote ({quote_status}) nor historical "
            f"({historical_status}) succeeded."
        ]
    if integrity.get("ok") is False:
        return NOT_CERTIFIED, [f"candle integrity check failed: {integrity.get('issues')}"]

    partial: List[str] = []
    if quote_status != "OK":
        partial.append(f"quote_status={quote_status} (historical succeeded, live quote did not).")
    if historical_status != "OK":
        partial.append(f"historical_status={historical_status} (quote succeeded, historical did not).")
    if integrity.get("ok") is None:
        partial.append("candle integrity could not be checked (no candles returned).")

    if partial:
        return PARTIAL, partial
    return CERTIFIED, [
        "quote OK, historical OK, symbol match confirmed (where observable), "
        "integrity checks passed."
    ]


async def main() -> int:
    now = _now_ist()
    if not _within_market_hours(now):
        print(json.dumps({
            "aborted": True, "reason": "OUTSIDE_MARKET_HOURS",
            "now_ist": now.isoformat(),
            "market_hours": f"{MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri",
        }, indent=2))
        print(
            "\nABORT: outside NSE market hours. A zero/stale VIX quote outside "
            "a session is uninterpretable -- it cannot distinguish a broken "
            "path from a closed market. NO certification artifact is written. "
            "Re-run during an NSE session.",
            file=sys.stderr,
        )
        return 1

    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig
    from bujji.broker.errors import AuthenticationError
    import logging

    log = logging.getLogger("certify_vix")
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

    timestamp = now.isoformat()

    # --- Quote leg: raw _call, not get_vix(), so the echoed symbol ("n") is
    # visible for the same symbol-integrity check every other certification
    # in this project performs -- get_vix() itself discards that field. ---
    quote_status = "UNTESTED"
    symbol_returned = None
    symbol_match = None
    limitations: List[str] = []
    try:
        raw = await broker._call("ltp", symbols=SYMBOL)
        row = next((r for r in raw.get("d", []) if r.get("n")), None)
        if row is None:
            quote_status = "NO_DATA"
            limitations.append(f"no usable row in ltp response: {raw}")
        else:
            symbol_returned = row.get("n")
            symbol_match = symbol_returned == SYMBOL
            v = row.get("v", {})
            level = v.get("lp")
            if not symbol_match:
                quote_status = "SYMBOL_MISMATCH"
                limitations.append(
                    f"requested {SYMBOL}, broker echoed {symbol_returned} -- "
                    "symbol-translation corruption; NOT_CERTIFIED regardless "
                    "of any other field."
                )
            elif level is None or level <= 0:
                quote_status = "NO_DATA"
                limitations.append(f"symbol matched but no valid lp in v-dict: {v}")
            else:
                quote_status = "OK"
                prev_close = v.get("prev_close_price")
                if prev_close is None or prev_close <= 0:
                    limitations.append("prev_close_price absent/non-positive -- optional, not required.")
    except Exception as e:  # noqa: BLE001
        quote_status = "ERROR"
        limitations.append(f"vix ltp call raised: {e}")

    # --- Historical leg: raw _call with resolution="D", mirroring every
    # other certification script's exact param shape rather than guessing. -
    historical_status = "UNTESTED"
    candles: list = []
    try:
        today = datetime.date.today()
        raw = await broker._call(
            "historical", symbol=SYMBOL, resolution="D", date_format="1",
            range_from=(today - datetime.timedelta(days=30)).isoformat(),
            range_to=today.isoformat(), cont_flag="1",
        )
        if raw.get("s") == "ok" and raw.get("candles"):
            historical_status = "OK"
            from bujji.core.models import Candle
            from bujji.core.clock import epoch_to_ist
            candles = [
                Candle(timestamp=epoch_to_ist(r[0]), open=r[1], high=r[2],
                       low=r[3], close=r[4], volume=r[5] if len(r) > 5 else 0.0)
                for r in raw["candles"]
            ]
            if len(candles) < 2:
                limitations.append(
                    f"only {len(candles)} historical candle(s) over a 30-day "
                    "window -- cannot confirm multi-date coverage."
                )
        elif raw.get("s") == "no_data":
            historical_status = "NO_DATA"
        else:
            historical_status = "ERROR"
            limitations.append(f"unexpected historical response: {raw}")
    except Exception as e:  # noqa: BLE001
        historical_status = "ERROR"
        limitations.append(f"historical vix probe raised: {e}")

    integrity = _validate_candles(candles, now)
    if integrity.get("issues"):
        limitations.extend(integrity["issues"])

    # Volume/OI are structurally NOT_APPLICABLE for an index -- per the
    # 17F.0.4 Instrument Capability Registry, this is never recorded as a
    # gap. Recording it as PARTIAL for a missing volume/OI field would
    # directly contradict the contract this script exists to uphold.
    limitations.append(
        "volume/OI are NOT_APPLICABLE for an index (17F.0.4 Instrument "
        "Capability Registry) -- not evaluated, not counted as missing."
    )

    result, reasons = _classify(
        symbol_match=symbol_match, quote_status=quote_status,
        historical_status=historical_status, integrity=integrity,
    )
    limitations.extend(reasons)

    cert = {
        "timestamp": timestamp,
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": SYMBOL,
        "symbol_returned": symbol_returned,
        "quote_status": quote_status,
        "historical_status": historical_status,
        "volume_available": None,  # NOT_APPLICABLE, not False -- see limitations.
        "oi_available": None,      # NOT_APPLICABLE, not False.
        "timestamp_valid": integrity.get("ok"),
        "validation_result": result,
        "limitations": limitations or ["none observed"],
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
