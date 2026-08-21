"""Phase 17I.4 — India VIX Reality Capture.

The second live observation collector, mirroring
`capture_first_spot_observation.py` (Phase 17I.2) exactly: it is
`get_vix()` (live-verified 2026-07-20, re-confirmed in Gate B
2026-08-13) -> `build_raw_observation()` -> `RawObservationStore.append()`,
the same three-call sequence, same shared Layer 0 store, same shared
certification directory. No new dataclass, no new taxonomy entry, no
new store method -- see docs/PHASE_17I3_MINIMUM_MARKET_REALITY_CAPTURE_AUDIT.md.

Per that audit, VIX is structurally as simple as spot: `INSTRUMENT_INDEX`
requires zero identity fields (same as `INSTRUMENT_SPOT`), and
certification is already CERTIFIED_AVAILABLE on disk
(`fyers_india_vix_certification_YYYYMMDD.json`). The ONLY translation
needed is the payload key: `get_vix()` returns `{"level": ..., ("prev_close": ...)?}`,
while `taxonomy.REQUIRED_PAYLOAD_FIELDS[KIND_QUOTE]` wants `ltp`. This
mirrors the exact "normalize at the collector boundary, never change
the taxonomy" pattern already locked for `ask`->`asks` (17H.1 Decision 1).
`prev_close`, when present, is carried through unchanged -- it is
neither required nor forbidden.

Writes to the SAME shared Layer 0 store every collector writes to
(`layer0_data/`), gated by the SAME certification directory
(`data_certification/`) every other collector reads. This script does
not certify anything, it only reads the existing verdict.

READ-ONLY against the broker (one `get_vix()` call). WRITE-ONLY against
Layer 0 (one `RawObservationStore.append()` call, idempotent on repeat).
MARKET-HOURS GATED, same reasoning as every other capture script in this
project: an LTP fetched outside market hours is a stale/closed-market
value, not a real observation, and must never be recorded as one.

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/capture_first_vix_observation.py
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

SOURCE = "fyers"
ACCESS_METHOD = "direct_sdk_fyers_broker_py"
SESSION_ID = "capture-first-vix-observation"
SYMBOL = "NSE:INDIAVIX-INDEX"

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


def build_vix_observation(vix: dict, capture_timestamp: str, cert_status: str,
                           cert_ref):
    """Pure construction step: one real `get_vix()` result -> one
    `RawObservation`.

    Isolated from `main()` so it is unit-testable without a live broker
    connection, mirroring `build_spot_observation()`'s convention.
    `vix["level"]` (the only field `taxonomy.REQUIRED_PAYLOAD_FIELDS`
    requires) is renamed to `ltp` here -- the one known translation this
    phase exists to resolve. `prev_close`, when present, passes through
    unchanged.
    """
    from bujji.market_reality import taxonomy
    from bujji.market_reality.capture import build_raw_observation

    payload = {"ltp": vix["level"]}
    if "prev_close" in vix:
        payload["prev_close"] = vix["prev_close"]

    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument=SYMBOL,
        instrument_type=taxonomy.INSTRUMENT_INDEX,
        payload=payload,
        source=SOURCE,
        access_method=ACCESS_METHOD,
        capture_timestamp=capture_timestamp,
        event_timestamp=None,  # UNKNOWN whether the real ltp response carries
        # a timestamp beyond `lp` (docs/PHASE_17I1_..._AUDIT.md, Remaining
        # Blockers) -- capture_timestamp is the only value we can honestly
        # attach without fabricating one. Same open item as spot.
        certification_status=cert_status,
        certification_ref=cert_ref,
        identity_fields={},
    )


async def main() -> int:
    now = now_ist()
    if not within_market_hours(now):
        print(
            f"ABORT: outside NSE market hours (now={now.isoformat()}, "
            f"window={MARKET_OPEN}-{MARKET_CLOSE} IST, Mon-Fri). A VIX "
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

    log = logging.getLogger("capture_first_vix_observation")
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
        vix = await broker.get_vix()
    except Exception as exc:  # noqa: BLE001
        print(f"ABORT: get_vix() failed: {exc}", file=sys.stderr)
        return 3

    if vix is None or vix.get("level") is None:
        print(
            f"ABORT: get_vix() returned no usable level: {vix!r}. "
            "Refusing to capture or persist a non-observation.",
            file=sys.stderr,
        )
        return 3

    gate = CertificationGate(str(CERT_DIR))
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, taxonomy.INSTRUMENT_INDEX)

    raw = build_vix_observation(vix, capture_timestamp, cert_status, cert_ref)

    store = RawObservationStore(str(LAYER0_DIR), gate, session_id=SESSION_ID)
    result = store.append(raw, now=capture_timestamp)

    print(f"outcome:            {result.outcome}")
    print(f"observation_id:     {result.observation_id}")
    print(f"instrument:         {SYMBOL}")
    print(f"ltp:                {vix['level']}")
    print(f"certification:      {cert_status} ({cert_ref})")
    print(f"stored at:          {store.accepted_path}")
    if result.outcome == taxonomy.OUTCOME_REJECTED:
        print(f"rejection_reasons:  {result.validation.reasons}", file=sys.stderr)
        return 4

    return 0


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
