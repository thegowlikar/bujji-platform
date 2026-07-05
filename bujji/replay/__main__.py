"""CLI: replay historical candles through the live decision engine.

Usage:
    python -m bujji.replay --config config/config.yaml --candles data/history.csv
"""
from __future__ import annotations

import argparse
import asyncio

from ..core.banner import render_startup_banner
from ..core.config import AppConfig
from ..core.logging_setup import setup_logging
from .engine import ReplayEngine, load_candles_csv


def main() -> None:
    parser = argparse.ArgumentParser(description="Bujji historical replay")
    parser.add_argument("--config", default="config/config.yaml")
    parser.add_argument("--candles", required=True, help="CSV of historical candles")
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    config.ensure_dirs()
    logger = setup_logging(config.paths.log_dir, config.log_level)

    print(render_startup_banner(config, mode_override="REPLAY"))

    candles = load_candles_csv(args.candles)
    engine = ReplayEngine(config, logger)
    result = asyncio.run(engine.run(candles))

    print(f"\nReplay complete: {result.candles_processed} candles -> "
          f"state={result.final_state}, trades={len(result.trades)}")
    for trade in result.trades:
        print(f"  {trade.get('direction')} pnl={trade.get('daily_result')} "
              f"reason={trade.get('exit_reason')} | {trade.get('thesis')}")


if __name__ == "__main__":
    main()
