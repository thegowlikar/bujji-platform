"""Phase 17H.9 — NIFTY Spot Intraday (5-min) Historical Reality Ingestion.

Mirrors `ingest_nifty_spot_daily_historical.py`'s chunking/validation/
certification discipline exactly, with the differences this phase's
architecture audit requires (docs/PHASE_17H9_INTRADAY_REALITY_ARCHITECTURE_AUDIT.md):

    resolution     = moc_taxonomy.RESOLUTION_FIVE_MINUTE, not RESOLUTION_DAILY
    CHUNK_DAYS     = 100 (real, live-bisected per-request limit for intraday,
                     vs daily's 366)
    timestamp      = each candle's OWN real IST time, via
                     bujji.core.clock.epoch_to_ist(epoch) -- NOT the daily
                     scripts' fixed "T09:15:00" session marker, which is only
                     valid because every daily bar shares one nominal time-of-day.
    access_method  = "direct_sdk_fyers_historical_intraday_rest" -- a
                     third, distinct value from live's
                     "direct_sdk_fyers_broker_py" and daily historical's
                     "direct_sdk_fyers_historical_rest", so
                     CertificationGate never reports a false positive
                     for untested intraday access.

Reuses `HistoricalObservation`/`build_historical_observation()`/
`HistoricalObservationStore` completely unmodified -- confirmed by this
phase's audit to already correctly separate resolution and full
timestamp in both the observation-id content hash and the store's
natural key.

DEFAULT_START = 2017-07-17, the live-bisected real earliest intraday
date for spot (Phase 17H.8, reconfirmed by
certify_fyers_historical_spot_intraday_access.py).

FAILS CLOSED on certification, same as every other Layer 0 writer.

Usage (manual, on the VPS, any time):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/ingest_nifty_spot_intraday_historical.py
    PYTHONPATH=/opt/bujji/app python scripts/ingest_nifty_spot_intraday_historical.py --start 2026-01-01
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SYMBOL = "NSE:NIFTY50-INDEX"
INSTRUMENT_IDENTITY = SYMBOL
INSTRUMENT_TYPE = "SPOT"
SOURCE = "fyers_historical"
ACCESS_METHOD = "direct_sdk_fyers_historical_intraday_rest"
FYERS_RESOLUTION = "5"
DEFAULT_START = datetime.date(2017, 7, 17)  # Live-bisected earliest real intraday date (Phase 17H.8/17H.9).

CHUNK_DAYS = 100  # Real, live-bisected per-request limit for intraday (100d=ok, 101d=error).

RAW_ARTIFACT_DIR = REPO_ROOT / "data" / "historical_reality" / "raw_artifacts" / "fyers" / "NSE_NIFTY50-INDEX" / "intraday_5min"
STORE_PATH = REPO_ROOT / "data" / "historical_reality" / "normalized" / "historical_observations.db"

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _chunks(start: datetime.date, end: datetime.date, days: int):
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + datetime.timedelta(days=days - 1), end)
        yield cursor, chunk_end
        cursor = chunk_end + datetime.timedelta(days=1)


def _validate_row(row: list) -> Optional[str]:
    if len(row) < 6:
        return f"row has fewer than 6 fields: {row}"
    _, o, h, l, c, _v = row[0], row[1], row[2], row[3], row[4], row[5]
    if min(o, h, l, c) <= 0:
        return f"non-positive OHLC: {row}"
    if h < l or h < o or h < c or l > o or l > c:
        return f"impossible OHLC (V1): {row}"
    return None


def _mint_ingestion_run_id(instrument: str, range_from: str, range_to: str, started_at: str) -> str:
    import hashlib
    seed = "|".join((instrument, range_from, range_to, started_at))
    return "RUN-" + hashlib.md5(seed.encode()).hexdigest()[:24]


async def run(*, start: datetime.date, end: datetime.date) -> int:
    import logging

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.core.clock import epoch_to_ist
    from bujji.core.config import AppConfig
    from bujji.historical_reality.capture import build_historical_observation
    from bujji.historical_reality.models import (
        RUN_STATUS_ERROR, RUN_STATUS_NO_DATA, RUN_STATUS_OK, IngestionRun,
    )
    from bujji.historical_reality.store import (
        ConflictingHistoricalObservationError, HistoricalObservationStore,
    )
    from bujji.market_observation import taxonomy as moc_taxonomy
    from bujji.market_reality import taxonomy as reality_taxonomy
    from bujji.market_reality.certification import CertificationGate

    log = logging.getLogger("ingest_nifty_spot_intraday_historical")
    logging.basicConfig(level=logging.INFO)

    gate = CertificationGate(str(REPO_ROOT / "data_certification"))
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, reality_taxonomy.INSTRUMENT_SPOT)
    if cert_status != reality_taxonomy.CERTIFIED_AVAILABLE:
        print(
            f"ABORT: {ACCESS_METHOD}/NIFTY_SPOT is {cert_status}, not CERTIFIED_AVAILABLE. "
            "Run scripts/certify_fyers_historical_spot_intraday_access.py first. Refusing to "
            "ingest anything uncertified.",
            file=sys.stderr,
        )
        return 1

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

    RAW_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    store = HistoricalObservationStore(str(STORE_PATH))

    total_accepted = 0
    total_rejected = 0
    total_runs = 0
    chunks_no_data = 0
    chunks_error = 0

    for chunk_start, chunk_end in _chunks(start, end, CHUNK_DAYS):
        started_at = _now_ist().isoformat()
        range_from = chunk_start.isoformat()
        range_to = chunk_end.isoformat()

        try:
            raw = await broker._call(
                "historical", symbol=SYMBOL, resolution=FYERS_RESOLUTION, date_format="1",
                range_from=range_from, range_to=range_to, cont_flag="1",
            )
        except AuthenticationError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("chunk %s..%s raised: %s", range_from, range_to, exc)
            raw = {"s": "error", "message": str(exc)}

        artifact_path = RAW_ARTIFACT_DIR / f"{range_from}_{range_to}.json"
        artifact_path.write_text(json.dumps(raw, indent=2, default=str))

        run_id = _mint_ingestion_run_id(SYMBOL, range_from, range_to, started_at)
        candles = raw.get("candles", []) if isinstance(raw, dict) else []
        s = raw.get("s") if isinstance(raw, dict) else None

        accepted_this_chunk = 0
        rejected_this_chunk = 0
        retrieved_at = _now_ist().isoformat()

        if s == "ok" and candles:
            status = RUN_STATUS_OK
            for row in candles:
                reason = _validate_row(row)
                if reason is not None:
                    rejected_this_chunk += 1
                    log.warning("rejected row in %s..%s: %s", range_from, range_to, reason)
                    continue
                epoch, o, h, l, c = row[0], row[1], row[2], row[3], row[4]
                raw_volume = row[5] if len(row) > 5 else None
                volume = None if not raw_volume else float(raw_volume)

                # Real per-candle timestamp, not a fixed session marker --
                # each 5-min bar has its own genuine time-of-day (audit §"What's genuinely new").
                timestamp = epoch_to_ist(epoch).isoformat()

                obs = build_historical_observation(
                    instrument_identity=INSTRUMENT_IDENTITY,
                    instrument_type=INSTRUMENT_TYPE,
                    resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
                    timestamp=timestamp,
                    payload={"open": float(o), "high": float(h), "low": float(l),
                             "close": float(c), "volume": volume},
                    source=SOURCE,
                    access_method=ACCESS_METHOD,
                    source_epoch=int(epoch),
                    source_symbol=SYMBOL,
                    raw_artifact_ref=str(artifact_path.relative_to(REPO_ROOT)),
                    ingestion_run_id=run_id,
                    retrieved_at=retrieved_at,
                    certification_status=cert_status,
                    certification_ref=cert_ref,
                )
                try:
                    store.write(obs)
                    accepted_this_chunk += 1
                except ConflictingHistoricalObservationError as exc:
                    rejected_this_chunk += 1
                    log.error("CONFLICT for %s: %s", timestamp, exc)
        elif s == "no_data":
            status = RUN_STATUS_NO_DATA
            chunks_no_data += 1
        else:
            status = RUN_STATUS_ERROR
            chunks_error += 1

        store.record_ingestion_run(IngestionRun(
            ingestion_run_id=run_id, source=SOURCE, instrument=INSTRUMENT_IDENTITY,
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE, range_from=range_from, range_to=range_to,
            started_at=started_at, completed_at=_now_ist().isoformat(), status=status,
            rows_returned=len(candles), rows_accepted=accepted_this_chunk,
            rows_rejected=rejected_this_chunk,
            error_code=(raw.get("code") if isinstance(raw, dict) else None),
            error_message=(raw.get("message") if isinstance(raw, dict) else None),
            raw_artifact_path=str(artifact_path.relative_to(REPO_ROOT)),
        ))

        total_accepted += accepted_this_chunk
        total_rejected += rejected_this_chunk
        total_runs += 1
        log.info(
            "chunk %s..%s: status=%s accepted=%d rejected=%d",
            range_from, range_to, status, accepted_this_chunk, rejected_this_chunk,
        )

    print(f"\nIngestion complete.")
    print(f"chunks processed:   {total_runs}")
    print(f"chunks NO_DATA:     {chunks_no_data}")
    print(f"chunks ERROR:       {chunks_error}")
    print(f"observations accepted: {total_accepted}")
    print(f"observations rejected: {total_rejected}")
    print(f"store total count:     {store.count(INSTRUMENT_IDENTITY)}")
    print(f"store path:             {STORE_PATH}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=str, default=DEFAULT_START.isoformat(),
                         help="Start date YYYY-MM-DD (default: 2017-07-17).")
    parser.add_argument("--end", type=str, default=None,
                         help="End date YYYY-MM-DD (default: today).")
    args = parser.parse_args()
    start = datetime.date.fromisoformat(args.start)
    end = datetime.date.fromisoformat(args.end) if args.end else _now_ist().date()

    import asyncio
    return asyncio.run(run(start=start, end=end))


if __name__ == "__main__":
    raise SystemExit(main())
