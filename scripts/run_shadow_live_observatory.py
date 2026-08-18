"""Shadow Live launcher -- Continuous Intelligence Observatory.

Constructs and runs ShadowSessionRunner with the Observatory flags
enabled (market_perception_enabled=True, intelligence_cycle_enabled=True).
This is the ONLY file that changes -- ShadowSessionRunner's own
defaults remain exactly as shipped (both flags default OFF); this
launcher just opts in explicitly, per Phase 8's scope.

Reliability: ShadowSessionRunner.start() already never raises -- every
failure (recorder, market perception, quote fetch) is caught internally
and recorded into the final artifact's `errors`, never stopping the
quote loop. This launcher adds nothing beyond that existing guarantee.

Reusable across days: resolves today's ATM strike from a single live
get_spot() call plus the cached instrument master CSV (same offline
approach used throughout this project), builds today's session_id from
the real date, and computes max_cycles from the real time remaining
until market close (15:20 IST) -- so this script can be launched at
any future market open without editing hardcoded values.

Explicitly OUT of scope, never imported here: msi_strategy_selector,
msi_trade_intent, msi_trade_construction, execution_engine,
risk_governor, trading_brain.

DEPRECATION NOTICE (Phase 19.14.1): this is a manual/legacy launcher.
The authoritative daily runtime is now `run_daily_intelligence_session.py`
(Phase 19.11-19.14), normally driven by a systemd timer. This script
refuses to start (`bujji.shadow_runtime.manual_entrypoint_guard`) if
that authoritative runtime currently owns
`data/daily_intelligence.lock`, to avoid a duplicate capture/broker
session -- see Phase 19.14.0's own audit finding and Phase 19.14.1's
implementation doc.
"""
import asyncio
import csv
import datetime
import json
import logging
import os
import sys
import time

sys.path.insert(0, "/opt/bujji/app")
os.chdir("/opt/bujji/app")

from bujji.broker.fyers import FyersBroker
from bujji.core.config import BrokerConfig
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract
from bujji.market_perception.models import OptionChainConfig
from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner

CACHE_FILE = "/opt/bujji/app/data/instrument_master/fyers_fo_NSE.csv"
COL_LOT_SIZE, COL_EXPIRY_EPOCH, COL_SYMBOL = 3, 8, 9
COL_UNDERLYING, COL_STRIKE, COL_OPTION_TYPE = 13, 15, 16


def load_env_file(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ[key.strip()] = value.strip()


def ist_now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))


def resolve_atm_offline(spot: float):
    """Same offline resolution used throughout this project -- reads
    only the cached instrument master CSV, no live network call beyond
    the single get_spot() already performed by the caller."""
    now_epoch = time.time()
    rows = []
    with open(CACHE_FILE) as f:
        for row in csv.reader(f):
            if len(row) <= COL_OPTION_TYPE:
                continue
            if row[COL_UNDERLYING] != "NIFTY" or row[COL_OPTION_TYPE] not in ("CE", "PE"):
                continue
            try:
                expiry_epoch = int(row[COL_EXPIRY_EPOCH])
                strike = float(row[COL_STRIKE])
                lot_size = int(row[COL_LOT_SIZE])
            except ValueError:
                continue
            if expiry_epoch < now_epoch:
                continue
            rows.append((expiry_epoch, strike, row[COL_OPTION_TYPE], row[COL_SYMBOL], lot_size))
    if not rows:
        raise RuntimeError("no unexpired NIFTY CE/PE rows found in cached instrument master")
    nearest_expiry = min(r[0] for r in rows)
    same_expiry = [r for r in rows if r[0] == nearest_expiry]
    ce_rows = [r for r in same_expiry if r[2] == "CE"]
    pe_rows = [r for r in same_expiry if r[2] == "PE"]
    atm_ce = min(ce_rows, key=lambda r: abs(r[1] - spot))
    atm_pe = min(pe_rows, key=lambda r: abs(r[1] - spot))
    expiry_date = datetime.datetime.fromtimestamp(nearest_expiry, datetime.timezone.utc).date().isoformat()
    return atm_ce, atm_pe, expiry_date


async def main():
    from bujji.shadow_runtime.manual_entrypoint_guard import (
        AuthoritativeRuntimeActiveError,
        refuse_if_authoritative_runtime_active,
    )
    try:
        refuse_if_authoritative_runtime_active()
    except AuthoritativeRuntimeActiveError as exc:
        print(f"REFUSING TO START: {exc}", file=sys.stderr)
        sys.exit(1)

    load_env_file("/opt/bujji/.env")
    config = BrokerConfig(
        name="fyers",
        app_id=os.environ["FYERS_APP_ID"],
        access_token=os.environ["FYERS_ACCESS_TOKEN"],
        app_secret=os.environ.get("FYERS_APP_SECRET"),
        refresh_token=os.environ.get("FYERS_REFRESH_TOKEN"),
    )
    logger = logging.getLogger("shadow-live-observatory")
    broker = FyersBroker(config, logger)

    await broker.connect()
    spot = await broker.get_spot("NIFTY")
    atm_ce, atm_pe, expiry_date = resolve_atm_offline(spot)

    ce = OptionContract(atm_ce[3], "NIFTY", int(atm_ce[1]), OptionType.CE, expiry_date, atm_ce[4])
    pe = OptionContract(atm_pe[3], "NIFTY", int(atm_pe[1]), OptionType.PE, expiry_date, atm_pe[4])
    watchlist = [(ce, Side.SELL), (pe, Side.SELL)]

    today = ist_now().date().isoformat()
    session_id = f"SHADOW-OBSERVATORY-{today}"
    session_dir = f"shadow_sessions/{session_id}"
    os.makedirs(session_dir, exist_ok=True)
    logging.basicConfig(
        filename=f"{session_dir}/session.log", level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    market_end = ist_now().replace(hour=15, minute=20, second=0, microsecond=0)
    remaining = (market_end - ist_now()).total_seconds()
    poll_interval = 30.0
    max_cycles = max(1, int(remaining // poll_interval))

    runner = ShadowSessionRunner(
        broker=broker,
        watchlist=watchlist,
        storage_path=f"{session_dir}/quotes.jsonl",
        session_id=session_id,
        clock=lambda: datetime.datetime.now(datetime.timezone.utc),
        max_cycles=max_cycles,
        max_consecutive_failures=5,
        sleep_seconds=poll_interval,
        # --- Continuous Intelligence Observatory, Phase 8 ---
        # Only these three lines differ from a plain quote-only launch.
        # ShadowSessionRunner's own defaults are untouched; this is an
        # explicit opt-in at the call site only.
        market_perception_enabled=True,
        market_snapshot_path=f"{session_dir}/market_snapshots.jsonl",
        intelligence_snapshot_path=f"{session_dir}/intelligence_snapshots.jsonl",
        intelligence_cycle_enabled=True,
        intelligence_cycle_path=f"{session_dir}/intelligence_cycle.jsonl",
        # Reduced from the default (strike_range=2000, ~80 leg quotes/cycle)
        # after live rate-limiting was observed 2026-08-06 -- still a
        # real, meaningful ATM+/-500 chain (11 strikes x 2 = 22 legs/cycle).
        market_perception_chain_config=OptionChainConfig(strike_range=500, strike_step=100),
    )

    artifact = await runner.start()

    out = {
        "session_id": artifact.session_id,
        "start_time": artifact.start_time,
        "end_time": artifact.end_time,
        "observations_count": artifact.observations_count,
        "data_quality_summary": artifact.data_quality_summary,
        "liquidity_summary": artifact.liquidity_summary,
        "errors": list(artifact.errors),
        "runtime_health": artifact.runtime_health,
    }
    with open(f"{session_dir}/artifact.json", "w") as f:
        json.dump(out, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
