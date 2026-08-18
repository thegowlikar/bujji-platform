"""Phase 17F.0.2 — PREPARED, NOT-YET-EXECUTED websocket access certification.

Certifies the `fyers_websocket` ACCESS METHOD, which Phase 17E's gate
treats as a distinct certification subject from `direct_sdk_fyers_broker_py`.
A certification of the REST/SDK path says nothing about the websocket path
-- conflating two access paths is exactly what the MCP connector incident
proved dangerous, and the gate was built to prevent it.

WHY THIS SCRIPT USES THE SDK SOCKET DIRECTLY, NOT `FyersTickFeed`:
the question being certified is "what does the FYERS websocket actually
deliver, through the production credential path" -- and `FyersTickFeed`
cannot answer it today, because it extracts only `symbol`/`ltp` inside
`on_message` and exposes no per-tick hook (that hook is Phase 17F.0.1a,
which this certification is a prerequisite for). Constructing the socket
here mirrors `FyersTickFeed._connect_locked()` exactly -- same
`data_ws.FyersDataSocket`, same `access_token=f"{app_id}:{token}"` form,
same `write_to_file=False` -- so what is certified is the real transport,
not a synthetic one.

CRITICAL DIFFERENCE FROM THE REST CERTIFICATION: litemode.
`FyersTickFeed` constructs its socket with `litemode=True` (the SDK's own
default is False). In lite mode the feed sends a minimal payload -- so
the long-standing "21 of 23 fields are discarded by on_message" claim is
NOT the real constraint. In lite mode those fields are never sent at all.
This script therefore runs in FULL mode (`litemode=False`) to establish
what the transport can actually deliver, and separately records that the
production wrapper currently runs lite -- a limitation, not a capability
gap.

READ-ONLY. Subscribes and listens. Places no orders, mutates no broker
state, and writes only its own certification artifact. It never obtains,
refreshes, or prompts for a token.

MARKET-HOURS AWARE, BY DESIGN. Zero ticks outside market hours is
uninterpretable -- it cannot distinguish "the transport is broken" from
"the market is closed." Running out of hours therefore ABORTS and writes
NO artifact, rather than recording a false NOT_CERTIFIED. This encodes
the lesson from 2026-08-12, when a pre-market run produced a
PARTIAL_CERTIFICATION that was later proven to be a test artifact rather
than a FYERS limitation.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/certify_websocket_access.py
"""
from __future__ import annotations

import collections
import datetime
import json
import logging
import math
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# The instrument under certification. Spot only, matching the operator's
# session-one scope decision (Phase 17F.0).
SYMBOL = "NSE:NIFTY50-INDEX"
INSTRUMENT = "NIFTY_SPOT"

ACCESS_METHOD = "fyers_websocket"
BROKER = "fyers"

CERT_DIR = Path(__file__).resolve().parent.parent / "data_certification"
ARTIFACT_NAME = "fyers_websocket_certification"

# How long to listen. Long enough that a genuinely active instrument
# cannot plausibly produce zero ticks, short enough to run inside a
# session without tying up the operator.
LISTEN_SECONDS = 120

# NSE regular session, IST.
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

# --- Full-mode price-scaling verification -----------------------------------
# The SDK divides price fields by (10 ** precision) * multiplier. In LITE mode
# that division is applied explicitly to `ltp`. In FULL mode it is applied
# POSITIONALLY -- the index branch scales only fields at positions
# [0, 1, 3, 4, 5] of an internal mapper -- so whether `ltp` is scaled depends
# on where it happens to sit in that list.
#
# This matters far beyond this script: `TickEngine` reads `FyersTickFeed.
# latest()` for LIVE option premiums on open positions. If full mode returns
# an unscaled `ltp`, every premium shifts by a power of ten, silently, in the
# live trading path. That is the single change in Phase 17F.0.1 capable of
# hurting trading, and it must be settled by measurement, not by reading SDK
# source and reasoning about list indices.
#
# The failure being hunted is an ORDER-OF-MAGNITUDE error, not a rounding
# difference -- so the test is a RATIO, not an equality. A ratio test is
# robust to the genuine price movement that occurs between a REST call and a
# websocket tick (a few points on a ~24,000 index, far under 1%), while being
# unmissable about a 100x shift.
SCALING_CONSISTENT = "CONSISTENT"
SCALING_POWER_OF_TEN_MISMATCH = "POWER_OF_TEN_MISMATCH"
SCALING_INCONCLUSIVE = "INCONCLUSIVE"

# Ratio band within which ws/rest counts as the same number. Generous
# relative to real intra-second index movement, and ~2 orders of magnitude
# away from the failure it must catch.
SCALING_TOLERANCE = 0.01

# Fields a capture path needs from a tick to be useful beyond bare price.
# Their ABSENCE is recorded, never inferred as zero.
FIELDS_OF_INTEREST = (
    "ltp", "vol_traded_today", "last_traded_qty", "last_traded_time",
    "exch_feed_time", "bid_price", "ask_price", "bid_size", "ask_size",
    "prev_close_price", "open_price", "high_price", "low_price", "oi",
)


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:  # Saturday/Sunday. Holidays are not modelled --
        return False        # a holiday run simply sees no ticks and aborts.
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _rest_ltp(log) -> Optional[float]:
    """One REST `ltp` sample through the ALREADY-CERTIFIED access path
    (`direct_sdk_fyers_broker_py`). Read-only.

    This is deliberately the same `FyersBroker._call("ltp", ...)` path that
    `verify_fo_access.py` certified on 2026-08-12, so the comparison is
    against a known-good reference rather than a second unverified source.
    Returns None on any failure -- an unavailable reference makes the
    comparison INCONCLUSIVE, never a false pass.
    """
    import asyncio

    async def _go() -> Optional[float]:
        from bujji.broker.fyers import FyersBroker
        from bujji.core.config import AppConfig

        app_cfg = AppConfig.load("config/config.yaml")
        cfg = app_cfg.broker
        if cfg.name != "fyers":
            cfg.name = "fyers"
        broker = FyersBroker(cfg, log)
        await broker.connect()
        data = await broker._call("ltp", symbols=SYMBOL)
        for row in data.get("d", []):
            if row.get("n") == SYMBOL:
                return row.get("v", {}).get("lp")
        return None

    try:
        return asyncio.run(_go())
    except Exception as exc:  # noqa: BLE001
        log.warning("rest_ltp_sample_failed: %s", exc)
        return None


def _compare_scaling(ws_ltp: Optional[float], rest_ltp: Optional[float]) -> dict:
    """Classify ws/rest as CONSISTENT, POWER_OF_TEN_MISMATCH, or
    INCONCLUSIVE. Never asserts agreement it cannot demonstrate."""
    if ws_ltp is None or rest_ltp is None or rest_ltp == 0:
        return {
            "status": SCALING_INCONCLUSIVE,
            "ws_ltp": ws_ltp,
            "rest_ltp": rest_ltp,
            "ratio": None,
            "note": "one or both samples unavailable; no comparison possible.",
        }

    ratio = ws_ltp / rest_ltp

    if abs(ratio - 1.0) <= SCALING_TOLERANCE:
        return {
            "status": SCALING_CONSISTENT, "ws_ltp": ws_ltp, "rest_ltp": rest_ltp,
            "ratio": round(ratio, 6),
            "note": "full-mode ltp agrees with the certified REST reference.",
        }

    # Is the discrepancy a clean power of ten? That is the signature of the
    # precision/multiplier division being applied to one source and not the
    # other -- as opposed to a stale sample or a genuinely moving market.
    if ratio > 0:
        exponent = round(math.log10(ratio))
        if exponent != 0 and abs(ratio / (10 ** exponent) - 1.0) <= SCALING_TOLERANCE:
            return {
                "status": SCALING_POWER_OF_TEN_MISMATCH,
                "ws_ltp": ws_ltp, "rest_ltp": rest_ltp,
                "ratio": round(ratio, 6), "power_of_ten": exponent,
                "note": (
                    f"full-mode ltp differs from the certified REST reference by "
                    f"10^{exponent}. This is the precision/multiplier scaling being "
                    f"applied to one source and not the other. BLOCKS the litemode "
                    f"migration: flipping app.py to litemode=False would shift every "
                    f"live option premium TickEngine reads by this factor."
                ),
            }

    return {
        "status": SCALING_INCONCLUSIVE, "ws_ltp": ws_ltp, "rest_ltp": rest_ltp,
        "ratio": round(ratio, 6),
        "note": (
            "samples differ by neither ~1x nor a clean power of ten -- possibly a "
            "stale sample, a fast-moving market, or something unmodelled. Re-run "
            "before drawing any conclusion; do NOT migrate on this result."
        ),
    }


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


def _classify(*, connected: bool, tick_count: int, symbol_match: Optional[bool],
              missing_required: List[str], timestamp_ok: Optional[bool],
              scaling_status: str) -> tuple:
    """Three-state only. Never ambiguous, mirroring the REST
    certification's own classifier discipline."""
    reasons: List[str] = []

    if not connected:
        return NOT_CERTIFIED, ["websocket did not connect/authenticate."]

    if symbol_match is False:
        return NOT_CERTIFIED, [
            "symbol echoed by the feed does not match the symbol subscribed -- "
            "transport-level symbol corruption; NOT_CERTIFIED regardless of any other field."
        ]

    if tick_count == 0:
        return NOT_CERTIFIED, [
            "connected and subscribed during market hours but received zero ticks."
        ]

    if timestamp_ok is False:
        return NOT_CERTIFIED, ["received ticks carry invalid/future timestamps."]

    partial: List[str] = []
    if missing_required:
        partial.append(f"fields absent from every observed tick: {sorted(missing_required)}")
    if timestamp_ok is None:
        partial.append(
            "no exchange/event timestamp field present in any tick -- event_time "
            "cannot be populated for this access method; knowledge_time only."
        )
    if scaling_status == SCALING_POWER_OF_TEN_MISMATCH:
        # Not NOT_CERTIFIED: the transport genuinely works and symbol integrity
        # may be perfect. But the VALUES cannot be trusted as delivered, so this
        # can never be a clean pass -- and it is a hard block on the migration.
        partial.append(
            "full-mode ltp disagrees with the certified REST reference by a power "
            "of ten -- captured prices would be wrong by that factor. BLOCKS the "
            "litemode=False migration until resolved."
        )
    elif scaling_status == SCALING_INCONCLUSIVE:
        partial.append(
            "price scaling could not be verified against the REST reference. "
            "Migration must not proceed on an unverified scaling result."
        )

    if partial:
        return PARTIAL, partial
    return CERTIFIED, [
        "connected, subscribed, ticks received, symbol echo confirmed, "
        "required fields present, timestamps valid."
    ]


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    log = logging.getLogger("certify_websocket")

    now = _now_ist()
    if not _within_market_hours(now):
        print(json.dumps({
            "aborted": True,
            "reason": "OUTSIDE_MARKET_HOURS",
            "now_ist": now.isoformat(),
            "market_hours": f"{MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri",
        }, indent=2))
        print(
            "\nABORT: outside NSE market hours. Zero ticks outside a session is "
            "uninterpretable -- it cannot distinguish a broken transport from a "
            "closed market. NO certification artifact is written, because "
            "recording an out-of-hours run as NOT_CERTIFIED would be a false "
            "finding of exactly the kind this framework exists to prevent. "
            "Re-run during an NSE session.",
            file=sys.stderr,
        )
        return 1

    from bujji.core.config import AppConfig

    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if not cfg.app_id or not cfg.access_token:
        print("ABORT: no app_id/access_token in the environment.", file=sys.stderr)
        return 1

    from fyers_apiv3.FyersWebsocket import data_ws

    # --- Collection state (guarded; the SDK calls back on its own thread) --
    lock = threading.Lock()
    ticks: List[dict] = []
    key_counts: collections.Counter = collections.Counter()
    null_counts: collections.Counter = collections.Counter()
    symbols_seen: set = set()
    connected = threading.Event()
    errors: List[str] = []

    def on_open():
        connected.set()
        socket.subscribe(symbols=[SYMBOL], data_type="SymbolUpdate")

    def on_message(msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        sym = msg.get("symbol")
        if sym is None:
            return  # Subscription/connection ack, not a price tick.
        with lock:
            ticks.append(dict(msg))
            symbols_seen.add(sym)
            for key, value in msg.items():
                key_counts[key] += 1
                if value is None:
                    null_counts[key] += 1

    def on_error(msg) -> None:
        with lock:
            errors.append(str(msg))

    def on_close(msg) -> None:
        pass

    socket = data_ws.FyersDataSocket(
        access_token=f"{cfg.app_id}:{cfg.access_token}",
        log_path="logs",
        litemode=False,          # FULL mode -- the whole point of this run.
        write_to_file=False,
        reconnect=False,         # A single clean observation window.
        on_connect=on_open,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
    )

    started_at = _now_ist()
    threading.Thread(target=socket.connect, daemon=True,
                     name="certify-websocket").start()

    if not connected.wait(timeout=30):
        cert = {
            "timestamp": started_at.isoformat(), "broker": BROKER,
            "access_method": ACCESS_METHOD, "instrument": INSTRUMENT,
            "symbol_requested": SYMBOL, "symbol_returned": None,
            "connection_status": "CONNECT_TIMEOUT", "tick_count": 0,
            "validation_result": NOT_CERTIFIED,
            "limitations": ["websocket did not connect within 30s.", *errors],
        }
        _write_cert(cert)
        print(json.dumps(cert, indent=2))
        return 1

    print(f"connected; listening {LISTEN_SECONDS}s for {SYMBOL} ...")

    def _latest_ws_ltp() -> Optional[float]:
        with lock:
            return ticks[-1].get("ltp") if ticks else None

    # --- Scaling reference, sample 1 -------------------------------------
    # Allow a few seconds for the first ticks so there is a websocket value
    # to compare against, then take the REST reference as close in time as
    # possible.
    time.sleep(min(10, LISTEN_SECONDS))
    ws_start = _latest_ws_ltp()
    rest_start = _rest_ltp(log)
    scaling_start = _compare_scaling(ws_start, rest_start)
    print(f"scaling sample 1: ws={ws_start} rest={rest_start} -> {scaling_start['status']}")

    time.sleep(max(0, LISTEN_SECONDS - 10))

    # --- Scaling reference, sample 2 -------------------------------------
    # Two samples at opposite ends of the window: a single agreeing pair
    # could be coincidence on a slow-moving instrument, and a single
    # disagreeing pair could be one stale read.
    ws_end = _latest_ws_ltp()
    rest_end = _rest_ltp(log)
    scaling_end = _compare_scaling(ws_end, rest_end)
    print(f"scaling sample 2: ws={ws_end} rest={rest_end} -> {scaling_end['status']}")

    try:
        socket.close_connection()
    except Exception:  # noqa: BLE001
        pass

    # Both samples must agree that scaling is consistent. Any mismatch or
    # inconclusive sample degrades the verdict -- the migration gate demands
    # positive evidence, not absence of evidence against.
    statuses = {scaling_start["status"], scaling_end["status"]}
    if statuses == {SCALING_CONSISTENT}:
        scaling_status = SCALING_CONSISTENT
    elif SCALING_POWER_OF_TEN_MISMATCH in statuses:
        scaling_status = SCALING_POWER_OF_TEN_MISMATCH
    else:
        scaling_status = SCALING_INCONCLUSIVE

    with lock:
        observed = list(ticks)
        counts = dict(key_counts)
        nulls = dict(null_counts)
        seen = set(symbols_seen)
        errs = list(errors)

    ended_at = _now_ist()
    tick_count = len(observed)

    # --- Symbol integrity: the check the MCP incident made mandatory -----
    symbol_match: Optional[bool] = None
    if seen:
        symbol_match = seen == {SYMBOL}

    # --- Field census ---------------------------------------------------
    field_census = {
        key: {
            "present_in_ticks": counts.get(key, 0),
            "null_in_ticks": nulls.get(key, 0),
            "presence_ratio": round(counts.get(key, 0) / tick_count, 4) if tick_count else 0.0,
        }
        for key in sorted(counts)
    }
    missing_required = [f for f in FIELDS_OF_INTEREST if counts.get(f, 0) == 0]

    # --- Timestamp availability + validity ------------------------------
    ts_fields = [f for f in ("exch_feed_time", "last_traded_time") if counts.get(f, 0) > 0]
    timestamp_ok: Optional[bool] = None
    ts_notes: List[str] = []
    if ts_fields:
        field = ts_fields[0]
        now_epoch = time.time()
        values = [t.get(field) for t in observed if t.get(field)]
        numeric = [float(v) for v in values if isinstance(v, (int, float))]
        if numeric:
            future = [v for v in numeric if v > now_epoch + 60]
            timestamp_ok = not future
            if future:
                ts_notes.append(f"{len(future)} tick(s) carry a future {field}.")
            else:
                ts_notes.append(f"event timestamp available via {field!r}.")
        else:
            ts_notes.append(f"{field!r} present but non-numeric; not usable as event time.")

    result, reasons = _classify(
        connected=True, tick_count=tick_count, symbol_match=symbol_match,
        missing_required=missing_required, timestamp_ok=timestamp_ok,
        scaling_status=scaling_status,
    )

    limitations = list(ts_notes) + list(reasons)
    limitations.append(
        "Production `FyersTickFeed` constructs its socket with litemode=True; "
        "this certification ran litemode=False. Fields certified here are only "
        "reachable in production if the wrapper is switched to full mode."
    )
    if errs:
        limitations.append(f"socket errors during window: {errs[:5]}")

    cert = {
        "timestamp": started_at.isoformat(),
        "broker": BROKER,
        "access_method": ACCESS_METHOD,
        "instrument": INSTRUMENT,
        "symbol_requested": SYMBOL,
        "symbol_returned": sorted(seen)[0] if seen else None,
        "connection_status": "OK",
        "observation_window_seconds": LISTEN_SECONDS,
        "observation_started_ist": started_at.isoformat(),
        "observation_ended_ist": ended_at.isoformat(),
        "tick_count": tick_count,
        "ticks_per_second": round(tick_count / LISTEN_SECONDS, 3),
        "litemode": False,
        "symbol_integrity_confirmed": symbol_match,
        "event_timestamp_available": bool(ts_fields),
        "timestamp_valid": timestamp_ok,
        "field_census": field_census,
        "fields_absent_from_every_tick": sorted(missing_required),
        # The migration gate. `litemode=False` in app.py must NOT be applied
        # unless `price_scaling.status == CONSISTENT`.
        "price_scaling": {
            "status": scaling_status,
            "reference_access_method": "direct_sdk_fyers_broker_py",
            "sample_1_window_start": scaling_start,
            "sample_2_window_end": scaling_end,
            "migration_permitted": scaling_status == SCALING_CONSISTENT,
        },
        "validation_result": result,
        "limitations": limitations,
    }
    _write_cert(cert)
    print(json.dumps(cert, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
