"""Phase 17I.6 — Minimum Market Reality Capture Loop.

The first bounded, continuous forward-capture session: one consolidated
process observing NIFTY Spot, NIFTY Futures, and India VIX every
`POLL_INTERVAL_SECONDS`, writing each successful fetch into the same
Layer 0 store every other collector in this project writes to.

Nothing here is new architecture -- see
docs/PHASE_17I5_FUTURES_IDENTITY_AUDIT.md and
docs/PHASE_17I6_MINIMUM_CAPTURE_IMPLEMENTATION_PLAN.md for the audited
design this implements exactly:

* Loop shape mirrors `run_futures_depth_poller.py`'s `cycles`/
  `POLL_INTERVAL_SECONDS`/per-cycle `within_market_hours()` skeleton.
  This script is self-contained (no cross-script imports), matching
  every other operational script in this project -- the three
  `build_*_observation()` functions below reproduce
  `capture_first_spot_observation.py`'s and
  `capture_first_vix_observation.py`'s already-tested construction
  bodies verbatim rather than importing them, plus a new
  `build_futures_observation()` in the same shape.
* ONE `CaptureLifecycleTracker` instance for the whole session, not one
  per instrument -- Spot/Futures/VIX all share
  `source="fyers"`/`access_method="direct_sdk_fyers_broker_py"`, and a
  REST session failure is one fact about the connection, not three
  (confirmed across the 17I.3/17I.4 review chain).
* Failure handling is deliberately two-tier. A genuine connection-level
  condition (`AuthenticationError`) routes through the tracker's
  `record_condition()`/`record_recovery()` and stops the session -- a
  dead token is a real signal, matching `run_futures_depth_poller.py`'s
  own precedent. A bare per-instrument miss (broker returns `None`, or
  raises anything else) is logged and the cycle continues to the next
  instrument -- NO fake `CaptureEvent`, NO fake `RejectedObservation`.
  Layer 0 records facts, not absence; Phase 17I.7's gap measurement will
  infer misses from cadence vs. the persisted JSONL, not from a
  synthetic "missed" record this script does not create.
* Futures identity resolved once at session startup via
  `InstrumentMaster.resolve_nearest_future()` (Phase 17I.5's audited,
  additive extension) -- never `_futures_symbol()`'s provisional,
  wall-clock-driven guess.
* Timestamp handling: on the FIRST successful cycle only, the complete
  raw response of each broker call is logged (not just the extracted
  price) so a human can finally answer, from real data, whether FYERS's
  quote responses carry any timestamp beyond the price. `event_timestamp`
  stays `None` in every written record regardless -- this script does
  not invent one.

MARKET-HOURS GATED, MANUAL LAUNCH ONLY, single bounded session, no
daemon, no cron, no unattended execution -- same discipline as every
other collector in this project.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/capture_market_reality_session.py
    PYTHONPATH=/opt/bujji/app python scripts/capture_market_reality_session.py --cycles 3
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

POLL_INTERVAL_SECONDS = 60.0

UNDERLYING = "NIFTY"
SOURCE = "fyers"
ACCESS_METHOD = "direct_sdk_fyers_broker_py"
SESSION_ID = "capture-market-reality-session"
SPOT_SYMBOL = "NSE:NIFTY50-INDEX"
VIX_SYMBOL = "NSE:INDIAVIX-INDEX"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to. NOTE: Phase 17I.7
# Session 1 was already running with the OLD 15:30 value when this fix
# landed -- that session's own process already loaded the stale
# constant into memory and will exit at 15:30 today regardless of this
# file edit. Session 2 onward correctly picks up 15:40.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

LOG = logging.getLogger("capture_market_reality_session")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def build_spot_observation(ltp: float, capture_timestamp: str, cert_status: str,
                            cert_ref):
    """Same construction as `capture_first_spot_observation.py`'s
    already-tested `build_spot_observation()` -- reproduced here rather
    than imported, per this script's self-contained convention."""
    from bujji.market_reality import taxonomy
    from bujji.market_reality.capture import build_raw_observation

    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument=SPOT_SYMBOL,
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source=SOURCE,
        access_method=ACCESS_METHOD,
        capture_timestamp=capture_timestamp,
        event_timestamp=None,
        certification_status=cert_status,
        certification_ref=cert_ref,
        identity_fields={},
    )


def build_vix_observation(vix: dict, capture_timestamp: str, cert_status: str,
                           cert_ref):
    """Same construction as `capture_first_vix_observation.py`'s
    already-tested `build_vix_observation()` -- reproduced here rather
    than imported."""
    from bujji.market_reality import taxonomy
    from bujji.market_reality.capture import build_raw_observation

    payload = {"ltp": vix["level"]}
    if "prev_close" in vix:
        payload["prev_close"] = vix["prev_close"]

    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument=VIX_SYMBOL,
        instrument_type=taxonomy.INSTRUMENT_INDEX,
        payload=payload,
        source=SOURCE,
        access_method=ACCESS_METHOD,
        capture_timestamp=capture_timestamp,
        event_timestamp=None,
        certification_status=cert_status,
        certification_ref=cert_ref,
        identity_fields={},
    )


def build_futures_observation(quote: dict, expiry_iso: str, capture_timestamp: str,
                               cert_status: str, cert_ref):
    """New: same `KIND_QUOTE`/`build_raw_observation()` shape as spot/VIX,
    with the one addition `INSTRUMENT_FUTURE` requires --
    `identity_fields={"expiry": ...}`, resolved by
    `InstrumentMaster.resolve_nearest_future()` at session startup, never
    guessed. `volume`/`oi` are carried through unchanged when present --
    neither required nor forbidden by `taxonomy.REQUIRED_PAYLOAD_FIELDS`/
    `FORBIDDEN_PAYLOAD_FIELDS`.
    """
    from bujji.market_reality import taxonomy
    from bujji.market_reality.capture import build_raw_observation

    payload = {"ltp": quote["ltp"]}
    if quote.get("volume") is not None:
        payload["volume"] = quote["volume"]
    if quote.get("oi") is not None:
        payload["oi"] = quote["oi"]

    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument=quote["symbol"],
        instrument_type=taxonomy.INSTRUMENT_FUTURE,
        payload=payload,
        source=SOURCE,
        access_method=ACCESS_METHOD,
        capture_timestamp=capture_timestamp,
        event_timestamp=None,
        certification_status=cert_status,
        certification_ref=cert_ref,
        identity_fields={"expiry": expiry_iso},
    )


async def _capture_spot(broker, gate, store, capture_timestamp: str, log_raw: bool) -> None:
    from bujji.market_reality import taxonomy

    ltp = await broker.get_spot(UNDERLYING)
    if log_raw:
        LOG.info("RAW spot ltp=%r (first-cycle timestamp inspection)", ltp)
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT)
    raw = build_spot_observation(ltp, capture_timestamp, cert_status, cert_ref)
    result = store.append(raw, now=capture_timestamp)
    LOG.info("spot append outcome=%s observation_id=%s", result.outcome, result.observation_id)


async def _capture_vix(broker, gate, store, capture_timestamp: str, log_raw: bool) -> None:
    from bujji.market_reality import taxonomy

    vix = await broker.get_vix()
    if log_raw:
        LOG.info("RAW vix response=%r (first-cycle timestamp inspection)", vix)
    if vix is None or vix.get("level") is None:
        LOG.warning("get_vix() returned no usable level this cycle: %r -- skipping.", vix)
        return
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, taxonomy.INSTRUMENT_INDEX)
    raw = build_vix_observation(vix, capture_timestamp, cert_status, cert_ref)
    result = store.append(raw, now=capture_timestamp)
    LOG.info("vix append outcome=%s observation_id=%s", result.outcome, result.observation_id)


async def _capture_futures(broker, gate, store, expiry_iso: str, capture_timestamp: str,
                            log_raw: bool) -> None:
    from bujji.market_reality import taxonomy

    quote = await broker.get_futures_quote(UNDERLYING)
    if log_raw:
        LOG.info("RAW futures quote=%r (first-cycle timestamp inspection)", quote)
    if quote is None or quote.get("ltp") is None:
        LOG.warning("get_futures_quote() returned no usable ltp this cycle: %r -- skipping.", quote)
        return
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, taxonomy.INSTRUMENT_FUTURE)
    raw = build_futures_observation(quote, expiry_iso, capture_timestamp, cert_status, cert_ref)
    result = store.append(raw, now=capture_timestamp)
    LOG.info("futures append outcome=%s observation_id=%s", result.outcome, result.observation_id)


async def _run_cycle(broker, gate, store, tracker, expiry_iso: str, log_raw: bool) -> bool:
    """Runs all three instrument captures for one cycle. Each is isolated:
    a bare miss (None/non-auth exception) is logged and the cycle moves
    on to the next instrument. An AuthenticationError is a connection-
    level fact, recorded once via the shared tracker, and propagated to
    stop the session (mirrors run_futures_depth_poller.py's own "a dead
    token is a real signal" handling).

    Returns False if the session should stop (auth failure), True to
    continue.
    """
    from bujji.broker.errors import AuthenticationError
    from bujji.market_reality import taxonomy

    capture_timestamp = now_ist().isoformat()
    # Each entry is a zero-arg callable returning a fresh coroutine, not a
    # coroutine object itself -- if an earlier instrument raises and this
    # loop returns early, a later entry's coroutine must never have been
    # created (an eagerly-created-but-never-awaited coroutine triggers a
    # real "coroutine was never awaited" warning).
    for label, make_coro in (
        ("spot", lambda: _capture_spot(broker, gate, store, capture_timestamp, log_raw)),
        ("futures", lambda: _capture_futures(broker, gate, store, expiry_iso, capture_timestamp, log_raw)),
        ("vix", lambda: _capture_vix(broker, gate, store, capture_timestamp, log_raw)),
    ):
        try:
            await make_coro()
            if tracker.has_open_condition:
                tracker.record_recovery(
                    event_time=capture_timestamp, knowledge_time=capture_timestamp,
                    detail=f"{label} fetch succeeded after a prior open condition.",
                )
        except AuthenticationError as exc:
            tracker.record_condition(
                reason=taxonomy.REASON_AUTH_FAILURE, event_time=capture_timestamp,
                knowledge_time=capture_timestamp, detail=str(exc),
                affected_instruments=(label,),
            )
            LOG.error("AuthenticationError during %s fetch -- stopping session: %s", label, exc)
            return False
        except Exception as exc:  # noqa: BLE001
            LOG.warning("%s fetch failed this cycle, continuing: %s", label, exc)
    return True


async def run(*, cycles: Optional[int]) -> int:
    started = now_ist()
    # First-sample capture (2026-08-19): a 09:14 pre-open fire WAITS for the
    # open instant instead of refusing, so the first observation lands within
    # seconds of the first tick. Offset +0s staggers this process against
    # its siblings on the shared FYERS 10/s ceiling. A far-from-open start
    # still refuses below, exactly as before.
    import time as _time

    from bujji.market_reality.open_wait import wait_until_open

    if wait_until_open(now_fn=now_ist, market_open=MARKET_OPEN,
                       open_offset_seconds=0, sleep_fn=_time.sleep, logger=LOG):
        started = now_ist()
    if not within_market_hours(started):
        LOG.warning(
            "Outside market hours (%s IST) -- aborting without opening a "
            "connection or writing anything.", started.isoformat(),
        )
        return 1

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.instrument_master import InstrumentMaster
    from bujji.core.config import AppConfig
    from bujji.market_reality.capture_lifecycle import CaptureLifecycleTracker
    from bujji.market_reality.certification import CertificationGate
    from bujji.market_reality.store import RawObservationStore

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
        return 2

    instruments = InstrumentMaster(REPO_ROOT / "data" / "instrument_master", LOG)
    try:
        _, expiry_iso, _ = await instruments.resolve_nearest_future(UNDERLYING)
    except LookupError as exc:
        LOG.error("futures identity resolution failed, aborting: %s", exc)
        return 4

    gate = CertificationGate(str(REPO_ROOT / "data_certification"))
    store = RawObservationStore(str(REPO_ROOT / "layer0_data"), gate, session_id=SESSION_ID)
    tracker = CaptureLifecycleTracker(store=store, source=SOURCE, access_method=ACCESS_METHOD)

    LOG.info(
        "Starting market reality capture session: interval=%ss futures_expiry=%s",
        POLL_INTERVAL_SECONDS, expiry_iso,
    )

    completed = 0
    while cycles is None or completed < cycles:
        cycle_start = now_ist()
        if not within_market_hours(cycle_start):
            LOG.info("Market hours ended mid-run -- stopping cleanly.")
            break
        keep_going = await _run_cycle(
            broker, gate, store, tracker, expiry_iso, log_raw=(completed == 0),
        )
        completed += 1
        if not keep_going:
            return 3
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    LOG.info("Session finished after %s cycle(s).", completed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=None,
                         help="Stop after N poll cycles (default: run until market close).")
    args = parser.parse_args()
    return asyncio.run(run(cycles=args.cycles))


if __name__ == "__main__":
    raise SystemExit(main())
