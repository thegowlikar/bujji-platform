"""Phase 17I.2 — First Live Observation Capture.

The smallest production-like path from a certified FYERS spot quote to
a persisted Layer 0 observation. Nothing here is new architecture: it
is `get_spot()` (live-verified since 2026-07-20, re-confirmed in Gate B
2026-08-13) -> `build_raw_observation()` -> `RawObservationStore.append()`,
exactly the three-call sequence already proven end-to-end by
`tests/test_market_reality_store.py`'s own `_obs()` fixture and already
used in production shape by `run_futures_depth_poller.py`'s `live`
branch. This script adds no new dataclass, no new taxonomy entry, no
new store method -- see docs/PHASE_17I1_FIRST_OBSERVATION_STORAGE_AUDIT.md.

Writes to the SAME shared Layer 0 store every collector writes to
(`layer0_data/`), gated by the SAME certification directory
(`data_certification/`) every other collector reads. Certification for
NIFTY_SPOT via direct_sdk_fyers_broker_py is already CERTIFIED_AVAILABLE
on disk (`fyers_nifty_spot_certification.json`) -- this script does not
certify anything, it only reads that existing verdict.

READ-ONLY against the broker (one `get_spot()` call). WRITE-ONLY against
Layer 0 (one `RawObservationStore.append()` call, idempotent on repeat).
MARKET-HOURS GATED, same reasoning as every other capture script in this
project: an LTP fetched outside market hours is a stale/closed-market
value, not a real observation, and must never be recorded as one.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/capture_first_spot_observation.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

UNDERLYING = "NIFTY"
SOURCE = "fyers"
ACCESS_METHOD = "direct_sdk_fyers_broker_py"
SESSION_ID = "capture-first-spot-observation"

REPO_ROOT = Path(__file__).resolve().parent.parent
CERT_DIR = REPO_ROOT / "data_certification"
LAYER0_DIR = REPO_ROOT / "layer0_data"

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def build_spot_observation(ltp: float, capture_timestamp: str, cert_status: str,
                            cert_ref):
    """Pure construction step: one real `ltp` value -> one `RawObservation`.

    Isolated from `main()` so it is unit-testable without a live broker
    connection, mirroring the depth poller's own testable-function
    convention (`within_market_hours`, `_normalize_depth_payload`).
    """
    from bujji.market_reality import taxonomy
    from bujji.market_reality.capture import build_raw_observation

    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument="NSE:NIFTY50-INDEX",
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source=SOURCE,
        access_method=ACCESS_METHOD,
        capture_timestamp=capture_timestamp,
        event_timestamp=None,  # UNKNOWN whether the real ltp response carries
        # a timestamp beyond `lp` (docs/PHASE_17I1_..._AUDIT.md, Remaining
        # Blockers) -- capture_timestamp is the only value we can honestly
        # attach without fabricating one.
        certification_status=cert_status,
        certification_ref=cert_ref,
        identity_fields={},
    )


async def main() -> int:
    now = now_ist()
    if not within_market_hours(now):
        print(
            f"ABORT: outside NSE market hours (now={now.isoformat()}, "
            f"window={MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri). A spot "
            "quote fetched outside a session is a stale/closed-market "
            "value, not a real observation -- refusing to capture or "
            "persist one. Re-run during an NSE session.",
            file=sys.stderr,
        )
        return 1

    import logging

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig
    from bujji.market_reality import taxonomy
    from bujji.market_reality.certification import CertificationGate
    from bujji.market_reality.store import RawObservationStore

    log = logging.getLogger("capture_first_spot_observation")
    logging.basicConfig(level=logging.INFO)

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

    capture_timestamp = now_ist().isoformat()
    try:
        ltp = await broker.get_spot(UNDERLYING)
    except Exception as exc:  # noqa: BLE001
        print(f"ABORT: get_spot() failed: {exc}", file=sys.stderr)
        return 3

    gate = CertificationGate(str(CERT_DIR))
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, taxonomy.INSTRUMENT_SPOT)

    raw = build_spot_observation(ltp, capture_timestamp, cert_status, cert_ref)

    store = RawObservationStore(str(LAYER0_DIR), gate, session_id=SESSION_ID)
    result = store.append(raw, now=capture_timestamp)

    print(f"outcome:            {result.outcome}")
    print(f"observation_id:     {result.observation_id}")
    print(f"instrument:         NSE:NIFTY50-INDEX")
    print(f"ltp:                {ltp}")
    print(f"certification:      {cert_status} ({cert_ref})")
    print(f"stored at:          {store.accepted_path}")
    if result.outcome == taxonomy.OUTCOME_REJECTED:
        print(f"rejection_reasons:  {result.validation.reasons}", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
