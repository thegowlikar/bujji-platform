"""Phase 17F.1.2 Q5 / activated Phase 17I.2 — Futures MARKET_DEPTH poller.
60-second cadence.

Wires the decided cadence (`docs/PHASE_17F1_2_Q1_Q2_Q3_Q5_DECISIONS.md`,
Q5) into a real, runnable poller for `NIFTY` futures `MARKET_DEPTH`
observations, following the exact market-hours-gated, prepared-but-
audited discipline used by every other collector script in this project
(`certify_vix_access.py`, `certify_websocket_access.py`, `verify_fo_access.py`).

FIELD MAPPING NOW VERIFIED (Phase 17I.1/17I.2), against
`data_certification/fyers_depth_discovery_20260813.json`'s real,
two-poll live capture of `FyersBroker.get_depth()`: the raw response
carries `bids`/`ask` (5-level ladders of `{price, volume, ord}`),
`oi`/`pdoi`/`oiflag`/`oipercent`, `totalbuyqty`/`totalsellqty`,
`ltp`/`ltq`/`ltt`, and contract metadata (`tick_Size`, `lower_ckt`,
`upper_ckt`, `expiry`). `_normalize_depth_payload()` below maps these
onto Layer 0's `KIND_MARKET_DEPTH` vocabulary
(`REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH] = ("bids", "asks")` --
note the raw key is singular `ask`, Layer 0's is plural `asks`; this is
a rename, not an invention) field-for-field, with NO computation: no
imbalance, no liquidity score, no pressure, no signal. Every stored
field is either a raw FYERS value or a straight rename of one.

FUTURES IDENTITY (Phase 17I.2, applying the binding rule already
established for Historical Reality futures ingestion, Phase 17H.3 Part
2.4, to this live capture path for the first time): `instrument` is
fixed at `"NIFTY_FUT_CONTINUOUS"`, never the literal, rollover-prone
contract symbol. The real, currently-listed contract symbol (resolved
live via `InstrumentMaster.resolve_nearest_future()` -- never
`_futures_symbol()`'s provisional wall-clock guess) is preserved as
`source_symbol` in `identity_fields`, alongside the required `expiry`
identity field, for full audit traceability without letting a contract
rollover fragment the observation identity.

CERTIFICATION (Phase 17I.1/17I.2): `ACCESS_METHOD` is
`"direct_sdk_fyers_broker_py_depth"` -- a NEW, distinct value from live
QUOTE's `"direct_sdk_fyers_broker_py"`. Reusing the quote value would
let `CertificationGate.status_for()` (keyed by `(instrument_type,
access_method)` only, no concept of observation `kind`) silently
report depth writes as CERTIFIED_AVAILABLE without depth itself ever
being certified -- the same collision class already found and fixed
for 17G.0/17H.3/17H.9. Run
`scripts/certify_fyers_futures_depth_access.py` first; `--live` fails
closed via the normal `CertificationGate.permits_write()` path inside
`RawObservationStore.append()` if it hasn't been.

MARKET-HOURS GATED: outside 09:15-15:40 IST Mon-Fri, this script exits
immediately without opening a connection or writing anything -- a
zero-observation result outside market hours is uninterpretable and
must never be recorded as a finding (same rule as every certification
script in this project).

DO NOT RUN AUTOMATICALLY. Manual execution by the operator, during an
NSE session, after their own token refresh and explicit go-ahead.

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/run_futures_depth_poller.py
    PYTHONPATH=/opt/bujji/app python scripts/run_futures_depth_poller.py --live --cycles 3
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# --- Q5 decision: 60-second polling cadence. See the decisions doc for
# the full rationale (conservative starting value, revisited once live
# rate-limit certification data exists; not a schema/materializer
# dependency -- FuturesStatistics.{oi,depth}_observation_count make the
# actual sampling density self-describing regardless of this number). ---
POLL_INTERVAL_SECONDS = 60.0

UNDERLYING = "NIFTY"
INSTRUMENT_IDENTITY = "NIFTY_FUT_CONTINUOUS"  # Never the literal contract symbol (17H.3 Part 2.4).
INSTRUMENT_TYPE = "FUTURE"
ACCESS_METHOD = "direct_sdk_fyers_broker_py_depth"
SOURCE = "fyers"

# Flipped True now that the real field mapping is confirmed
# (data_certification/fyers_depth_discovery_20260813.json) and
# _normalize_depth_payload()/expiry resolution below are both real,
# not placeholders. Certification is still enforced separately and
# independently by RawObservationStore.append() -- this flag governs
# only whether the SHAPE of the payload is trusted, not whether the
# SOURCE is certified.
FIELD_MAPPING_VERIFIED = True

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 40)  # NSE circular 2026-05-30, effective
# 2026-08-03: F&O market close extended 15:30 -> 15:40 (new Closing
# Auction Session in the cash segment; derivatives follow it). Verified
# via web search 2026-08-13 -- this project trades NIFTY options/futures,
# squarely the F&O segment this change applies to.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

LOG = logging.getLogger("futures_depth_poller")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _normalize_ladder(levels: list) -> list:
    """Rename `{price, volume, ord}` -> `{price, volume, order_count}`
    field-for-field. No filtering, no reordering, no computation --
    every level FYERS returned is preserved exactly."""
    return [
        {"price": level.get("price"), "volume": level.get("volume"),
         "order_count": level.get("ord")}
        for level in levels
    ]


def _normalize_depth_payload(raw_row: dict) -> Optional[dict]:
    """Map a raw `get_depth()` row onto Layer 0's MARKET_DEPTH payload
    shape, using ONLY the real field names confirmed live in
    `fyers_depth_discovery_20260813.json` (Phase 17I.1). Every value is
    either passed through unchanged or renamed -- never computed,
    scored, or interpreted.

    Returns None if the row is missing a field this function depends on
    to construct a structurally valid payload (`bids`/`ask` ladders,
    `oi`) -- a genuinely malformed row must not silently become a
    partially-fabricated observation.
    """
    if not FIELD_MAPPING_VERIFIED:
        return None

    bids = raw_row.get("bids")
    asks_raw = raw_row.get("ask")  # Raw FYERS key is singular; Layer 0's is plural.
    if bids is None or asks_raw is None or raw_row.get("oi") is None:
        return None

    return {
        "bids": _normalize_ladder(bids),
        "asks": _normalize_ladder(asks_raw),
        "open_interest": raw_row.get("oi"),
        "prior_day_open_interest": raw_row.get("pdoi"),
        "oi_change_flag": raw_row.get("oiflag"),
        "oi_change_percent": raw_row.get("oipercent"),
        "total_buy_quantity": raw_row.get("totalbuyqty"),
        "total_sell_quantity": raw_row.get("totalsellqty"),
        "last_price": raw_row.get("ltp"),
        "last_traded_quantity": raw_row.get("ltq"),
        "last_traded_time": raw_row.get("ltt"),
        "tick_size": raw_row.get("tick_Size"),
        "lower_circuit": raw_row.get("lower_ckt"),
        "upper_circuit": raw_row.get("upper_ckt"),
    }


def _log_discovery_sample(symbol: str, raw_row: dict) -> None:
    """DISCOVERY mode's entire purpose: put the real, raw shape in front
    of a human so they can update `_normalize_depth_payload()` from fact,
    not guesswork."""
    LOG.info(
        "DISCOVERY depth sample for %s -- keys=%s payload=%s",
        symbol, sorted(raw_row.keys()), json.dumps(raw_row, default=str),
    )


async def _poll_once(broker, symbol: str, *, expiry_iso: str, live: bool,
                      reality_store=None) -> None:
    raw_row = await broker.get_depth(symbol)
    if raw_row is None:
        LOG.warning("get_depth(%s) returned no row this cycle.", symbol)
        return

    if not live:
        _log_discovery_sample(symbol, raw_row)
        return

    payload = _normalize_depth_payload(raw_row)
    if payload is None:
        LOG.warning(
            "row missing a required field (bids/ask/oi) -- refusing to "
            "write a partial depth observation this cycle."
        )
        return

    from bujji.market_reality.capture import build_raw_observation

    capture_ts = now_ist().isoformat()
    raw = build_raw_observation(
        kind="MARKET_DEPTH", instrument=INSTRUMENT_IDENTITY, instrument_type=INSTRUMENT_TYPE,
        payload=payload, source=SOURCE, access_method=ACCESS_METHOD,
        capture_timestamp=capture_ts,
        identity_fields={"expiry": expiry_iso, "source_symbol": symbol},
    )
    result = reality_store.append(raw, now=capture_ts)
    LOG.info("append outcome=%s observation_id=%s", result.outcome, result.observation_id)


async def run(*, cycles: Optional[int], live: bool) -> int:
    started = now_ist()
    # First-sample capture (2026-08-19): a 09:14 pre-open fire WAITS for the
    # open instant instead of refusing, so the first observation lands within
    # seconds of the first tick. Offset +5s staggers this process against
    # its siblings on the shared FYERS 10/s ceiling. A far-from-open start
    # still refuses below, exactly as before.
    import time as _time

    from bujji.market_reality.open_wait import wait_until_open

    if wait_until_open(now_fn=now_ist, market_open=MARKET_OPEN,
                       open_offset_seconds=5, sleep_fn=_time.sleep, logger=LOG):
        started = now_ist()
    if not within_market_hours(started):
        LOG.warning(
            "Outside market hours (%s IST) -- aborting without opening a "
            "connection or writing anything. This mirrors every "
            "certification script in this project: a zero-observation "
            "result outside market hours is uninterpretable.",
            started.isoformat(),
        )
        return 1

    if live and not FIELD_MAPPING_VERIFIED:
        LOG.error(
            "--live requested but FIELD_MAPPING_VERIFIED is False. Refusing "
            "to start -- see this script's module docstring for why."
        )
        return 2

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.broker.instrument_master import InstrumentMaster
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

    instruments = InstrumentMaster(REPO_ROOT / "data" / "instrument_master", LOG)
    try:
        symbol, expiry_iso, _lot_size = await instruments.resolve_nearest_future(UNDERLYING)
    except LookupError as exc:
        LOG.error("futures identity resolution failed: %s", exc)
        return 4

    reality_store = None
    if live:
        from bujji.market_reality.certification import CertificationGate
        from bujji.market_reality.store import RawObservationStore
        gate = CertificationGate(str(REPO_ROOT / "data_certification"))
        reality_store = RawObservationStore(
            str(REPO_ROOT / "layer0_data"), gate, session_id="futures-depth-poller",
        )

    LOG.info(
        "Starting futures depth poller: symbol=%s identity=%s interval=%ss mode=%s",
        symbol, INSTRUMENT_IDENTITY, POLL_INTERVAL_SECONDS, "LIVE" if live else "DISCOVERY",
    )

    completed = 0
    while cycles is None or completed < cycles:
        cycle_start = now_ist()
        if not within_market_hours(cycle_start):
            LOG.info("Market hours ended mid-run -- stopping cleanly.")
            break
        try:
            await _poll_once(broker, symbol, expiry_iso=expiry_iso, live=live,
                              reality_store=reality_store)
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AuthenticationError):
                LOG.error("AuthenticationError -- stopping (a dead token is a real signal): %s", exc)
                return 3
            LOG.warning("poll cycle failed, continuing: %s", exc)
        completed += 1
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    LOG.info("Poller finished after %s cycle(s).", completed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=None,
                         help="Stop after N poll cycles (default: run until market close).")
    parser.add_argument("--live", action="store_true",
                         help="Write to Layer 0 instead of DISCOVERY-only logging. "
                              "Refuses to start until FIELD_MAPPING_VERIFIED is True.")
    args = parser.parse_args()
    return asyncio.run(run(cycles=args.cycles, live=args.live))


if __name__ == "__main__":
    raise SystemExit(main())
