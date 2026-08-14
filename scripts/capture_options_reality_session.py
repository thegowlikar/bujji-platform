"""Phase 17I.10 — NIFTY Option Chain Reality live capture. 5-minute
cadence, market hours only.

Reuses, unmodified: `historical_reality.capture.build_historical_observation()`,
`historical_reality.store.HistoricalObservationStore`,
`market_reality.certification.CertificationGate` -- per this project's
own 17I.9 readiness audit conclusion (reuse `HistoricalObservationStore`,
no new store; the store's `payload`/`record` columns are already fully
generic). No new architecture is introduced anywhere in this script.

IDENTITY: `instrument_identity = "{underlying}|{expiry_iso}|{strike}|{option_type}"`
(e.g. `"NIFTY|2026-08-25|24500|CE"`) -- per this phase's explicit
instruction. `fyToken` and the literal broker symbol are NEVER identity;
both are preserved as lineage (`source_symbol` = the broker symbol;
`fyToken` carried inside the payload as a non-identity fact, mirroring
how futures depth already treats non-identity broker fields, 17I.2).

ACCESS METHOD: `direct_sdk_fyers_optionchain_reality` -- new, distinct
from every existing value (`direct_sdk_fyers_broker_py`,
`direct_sdk_fyers_historical_rest`, `direct_sdk_fyers_historical_intraday_rest`,
`direct_sdk_fyers_broker_py_depth`), per 17I.9 S5's own recommendation.
Fails closed via the same `CertificationGate` every other Layer 0/
Historical Reality writer already uses -- refuses to write anything
until `certify_fyers_optionchain_reality_access.py` has run successfully.

COMPLETE CHAIN, ALL EXPIRIES: per this phase's explicit instruction
("do not only capture ATM... the whole observable universe"), this
script captures every expiry FYERS's own `expiryData` list returns, at
`STRIKE_COUNT=50` each (live-verified this phase to return 202 real
option rows) -- not just the nearest expiry. This is a real, disclosed
cost: with N expiries, ONE snapshot cycle issues N+1 real API calls
(one to discover expiries + N to fetch each chain), never stress-tested
at this call volume in any phase to date (17I.7 S6's own disclosed
limitation) -- see PHASE_17I10 doc for the real, observed expiry count
and any rate-limit behavior encountered during live validation.

NEVER passes `greeks=1` to the FYERS SDK -- IV/Greeks are excluded from
Reality by this project's deliberate architecture rule, not because the
source lacks them (see the certification script's own corrected
finding: FYERS CAN provide Greeks/IV on request; Bujji chooses never to
ask).

DELIBERATELY MARKET-HOURS GATED (09:15-15:30 IST per this phase's
explicit instruction -- narrower than futures depth's 15:40, not
silently reconciled; see PHASE_17I10 doc's own note on this
discrepancy).

Usage (manual, on the VPS, during market hours):
    cd /opt/bujji/app
    set -a && source /tmp/local_fyers.env && set +a
    PYTHONPATH=/opt/bujji/app python scripts/capture_options_reality_session.py
    PYTHONPATH=/opt/bujji/app python scripts/capture_options_reality_session.py --cycles 3
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import logging
import sys
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

UNDERLYING = "NIFTY"
INDEX_SYMBOL = "NSE:NIFTY50-INDEX"
INSTRUMENT_TYPE = "OPTION"
SOURCE = "fyers"
ACCESS_METHOD = "direct_sdk_fyers_optionchain_reality"
STRIKE_COUNT = 50

POLL_INTERVAL_SECONDS = 300.0  # 5-minute cadence, per this phase's explicit instruction.

MARKET_OPEN = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 30)  # Per this phase's explicit instruction.
IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

LOG = logging.getLogger("capture_options_reality_session")


def now_ist() -> datetime.datetime:
    return datetime.datetime.now(IST)


def within_market_hours(now: datetime.datetime) -> bool:
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def _format_strike(strike) -> str:
    """Canonical strike formatting -- integer-valued strikes render
    without a decimal point (matching how FYERS itself already sends
    them, e.g. 24300 not 24300.0), never ambiguous between two
    representations of the same real strike."""
    f = float(strike)
    if f == int(f):
        return str(int(f))
    return str(f)


def _instrument_identity(underlying: str, expiry_iso: str, strike, option_type: str) -> str:
    return f"{underlying}|{expiry_iso}|{_format_strike(strike)}|{option_type}"


def _expiry_iso_from_epoch(expiry_epoch) -> str:
    from bujji.core.clock import epoch_to_ist
    return epoch_to_ist(int(expiry_epoch)).date().isoformat()


def _validate_row(row: dict) -> Optional[str]:
    """Same discipline as every other Reality-tier row validator in
    this project: reject on missing/invalid REQUIRED facts, never on a
    genuinely zero/illiquid value."""
    if row.get("option_type") not in ("CE", "PE"):
        return f"missing/invalid option_type: {row.get('option_type')!r}"
    strike = row.get("strike_price")
    if strike is None or strike == -1:
        return f"missing/invalid strike_price: {strike!r}"
    if not row.get("symbol"):
        return "missing symbol"
    ltp = row.get("ltp")
    if ltp is None:
        return "missing ltp (None, not zero)"
    for field in ("ltp", "bid", "ask"):
        value = row.get(field)
        if value is not None and value < 0:
            return f"negative {field}: {value}"
    return None


async def _capture_one_expiry(broker, *, expiry_epoch: Optional[int], expiry_iso: str,
                               capture_ts: str, store, cert_status: str, cert_ref) -> dict:
    """Fetches and writes one expiry's chain. Returns per-expiry counts."""
    accepted = 0
    rejected = 0
    try:
        raw = await broker._call(
            "optionchain", symbol=INDEX_SYMBOL, strikecount=STRIKE_COUNT,
            timestamp=(expiry_epoch if expiry_epoch is not None else ""),
        )
    except Exception as exc:  # noqa: BLE001
        LOG.warning("expiry %s: optionchain call raised: %s", expiry_iso, exc)
        return {"expiry": expiry_iso, "status": "ERROR", "accepted": 0, "rejected": 0}

    data = raw.get("data") if isinstance(raw, dict) else None
    if not data:
        LOG.warning("expiry %s: no data in response: %s", expiry_iso, raw)
        return {"expiry": expiry_iso, "status": "NO_DATA", "accepted": 0, "rejected": 0}

    rows = data.get("optionsChain", [])
    option_rows = [r for r in rows if r.get("option_type") in ("CE", "PE")]

    from bujji.historical_reality.capture import build_historical_observation
    from bujji.historical_reality.store import ConflictingHistoricalObservationError
    from bujji.market_observation import taxonomy as moc_taxonomy

    for row in option_rows:
        reason = _validate_row(row)
        if reason is not None:
            rejected += 1
            LOG.warning("expiry %s: rejected row: %s (%s)", expiry_iso, reason, row.get("symbol"))
            continue

        identity = _instrument_identity(UNDERLYING, expiry_iso, row["strike_price"], row["option_type"])
        payload = {
            "ltp": row.get("ltp"),
            "bid": row.get("bid"),
            "ask": row.get("ask"),
            "volume": row.get("volume"),
            "open_interest": row.get("oi"),
            "prior_day_open_interest": row.get("prev_oi"),
            "open_interest_change": row.get("oich"),
            "open_interest_change_percent": row.get("oichp"),
            "ltp_change": row.get("ltpch"),
            "ltp_change_percent": row.get("ltpchp"),
            "fy_token": row.get("fyToken"),
        }
        obs = build_historical_observation(
            instrument_identity=identity,
            instrument_type=INSTRUMENT_TYPE,
            resolution=moc_taxonomy.RESOLUTION_FIVE_MINUTE,
            timestamp=capture_ts,
            payload=payload,
            source=SOURCE,
            access_method=ACCESS_METHOD,
            # PHASE_17I11: option chain state is ltp/bid/ask/oi, never a
            # true OHLC candle -- MAPPING is the same value_kind
            # market_reality.capture and options_observation.engine
            # already use for option/quote/depth payloads.
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            source_epoch=int(datetime.datetime.fromisoformat(capture_ts).timestamp()),
            source_symbol=row["symbol"],
            raw_artifact_ref="",
            ingestion_run_id=f"OPTCHAIN-{capture_ts}",
            retrieved_at=capture_ts,
            certification_status=cert_status,
            certification_ref=cert_ref,
        )
        try:
            store.write(obs)
            accepted += 1
        except ConflictingHistoricalObservationError as exc:
            rejected += 1
            LOG.error("CONFLICT for %s: %s", identity, exc)

    return {"expiry": expiry_iso, "status": "OK", "accepted": accepted, "rejected": rejected,
            "rows_seen": len(option_rows)}


async def _capture_one_cycle(broker, store, *, cert_status: str, cert_ref) -> List[dict]:
    """Discovers the real, currently-listed expiry set via one
    default-expiry call, then fetches every expiry's own chain
    (including the nearest one again, via its real epoch) -- one extra
    API call for simplicity/correctness (a single code path for every
    expiry) over a marginal efficiency gain; disclosed, not hidden."""
    capture_ts = now_ist().isoformat()

    raw = await broker._call("optionchain", symbol=INDEX_SYMBOL, strikecount=STRIKE_COUNT, timestamp="")
    data = raw.get("data") if isinstance(raw, dict) else None
    if not data:
        LOG.warning("cycle at %s: no data on default-expiry probe: %s", capture_ts, raw)
        return []

    expiry_list = data.get("expiryData", [])
    if not expiry_list:
        LOG.warning("cycle at %s: no expiryData returned.", capture_ts)
        return []

    results = []
    for entry in expiry_list:
        expiry_iso = _expiry_iso_from_epoch(entry["expiry"])
        result = await _capture_one_expiry(
            broker, expiry_epoch=int(entry["expiry"]), expiry_iso=expiry_iso, capture_ts=capture_ts,
            store=store, cert_status=cert_status, cert_ref=cert_ref,
        )
        results.append(result)

    return results


async def run(*, cycles: Optional[int]) -> int:
    started = now_ist()
    if not within_market_hours(started):
        LOG.warning(
            "Outside market hours (%s IST) -- aborting without opening a "
            "connection or writing anything.", started.isoformat(),
        )
        return 1

    from bujji.broker.errors import AuthenticationError
    from bujji.broker.fyers import FyersBroker
    from bujji.core.config import AppConfig
    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.market_reality import taxonomy as reality_taxonomy
    from bujji.market_reality.certification import CertificationGate

    logging.basicConfig(level=logging.INFO)
    app_cfg = AppConfig.load("config/config.yaml")
    cfg = app_cfg.broker
    if cfg.name != "fyers":
        cfg.name = "fyers"
    broker = FyersBroker(cfg, LOG)

    gate = CertificationGate(str(REPO_ROOT / "data_certification"))
    cert_status, cert_ref = gate.status_for(ACCESS_METHOD, reality_taxonomy.INSTRUMENT_OPTION)
    if cert_status != reality_taxonomy.CERTIFIED_AVAILABLE:
        LOG.error(
            "%s/OPTION is %s, not CERTIFIED_AVAILABLE. Run "
            "scripts/certify_fyers_optionchain_reality_access.py first. Refusing to capture.",
            ACCESS_METHOD, cert_status,
        )
        return 2

    try:
        await broker.connect()
    except AuthenticationError as exc:
        LOG.error("token invalid, aborting: %s", exc)
        return 3

    store = HistoricalObservationStore(
        str(REPO_ROOT / "data" / "historical_reality" / "normalized" / "historical_observations.db")
    )

    LOG.info("Starting options reality capture: underlying=%s interval=%ss strike_count=%s",
              UNDERLYING, POLL_INTERVAL_SECONDS, STRIKE_COUNT)

    completed = 0
    while cycles is None or completed < cycles:
        cycle_start = now_ist()
        if not within_market_hours(cycle_start):
            LOG.info("Market hours ended mid-run -- stopping cleanly.")
            break
        try:
            # Re-check certification LIVE each cycle -- never trust a
            # status resolved once at startup for the life of a long-
            # running process (same discipline as CertificationGate's
            # own "cache=False" default).
            cert_status, cert_ref = gate.status_for(ACCESS_METHOD, reality_taxonomy.INSTRUMENT_OPTION)
            if cert_status != reality_taxonomy.CERTIFIED_AVAILABLE:
                LOG.error("certification status changed mid-run to %s -- stopping.", cert_status)
                return 2
            results = await _capture_one_cycle(broker, store, cert_status=cert_status, cert_ref=cert_ref)
            total_accepted = sum(r.get("accepted", 0) for r in results)
            total_rejected = sum(r.get("rejected", 0) for r in results)
            LOG.info(
                "cycle complete: expiries=%d accepted=%d rejected=%d",
                len(results), total_accepted, total_rejected,
            )
        except Exception as exc:  # noqa: BLE001
            if isinstance(exc, AuthenticationError):
                LOG.error("AuthenticationError -- stopping (a dead token is a real signal): %s", exc)
                return 3
            LOG.warning("capture cycle failed, continuing: %s", exc)
        completed += 1
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

    LOG.info("Capture finished after %s cycle(s).", completed)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=None,
                         help="Stop after N poll cycles (default: run until market close).")
    args = parser.parse_args()
    return asyncio.run(run(cycles=args.cycles))


if __name__ == "__main__":
    raise SystemExit(main())
