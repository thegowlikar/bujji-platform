"""CLI: replay historical candles through the live decision engine.

Usage:
    python -m bujji.replay --config config/config.yaml --candles data/history.csv

Path isolation (production-safety fix): by default this NEVER reads or writes
the live process's session snapshot / journal CSV / SQLite database, even
though it loads the SAME --config file the live bot uses for its strategy
settings. Loading the live config for strategy parameters (timing, risk,
strike interval) is intentional and desired; silently reusing the live
config's *persistence paths* is not — doing so would (a) let a stale live
session snapshot leak into a replay run, breaking the "same candles -> same
output" determinism guarantee, and (b) write synthetic replay trades into the
SAME journal a real trading day's history lives in, permanently mixing
backtest output into the production trade record.

Every replay run is isolated under --workdir (default: data/replay/) unless
--use-live-paths is passed explicitly, which is a deliberately loud opt-out
for the rare case an operator genuinely wants to inspect the live paths
(e.g. reproducing today's exact session against saved candles for a support
investigation) and has consciously decided that's what they want.
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from ..core.banner import render_startup_banner
from ..core.config import AppConfig
from ..core.logging_setup import setup_logging
from .engine import ReplayEngine, load_candles_csv


def _isolate_paths(config: AppConfig, workdir: str) -> None:
    """Redirect session/journal/database paths under an isolated workdir so
    a replay run can never read or write the live process's persistence
    files, regardless of what --config points at."""
    base = Path(workdir)
    config.paths.journal_csv = base / "trade_journal.csv"
    config.paths.database = base / "bujji.db"
    config.paths.state_file = base / "session_state.json"
    config.paths.lock_file = base / "bujji.lock"
    # Sprint 3 fix: the Decision Journal (Sprint 2) was never added to this
    # redirect -- a replay run was silently writing DecisionSnapshots into
    # the LIVE production data directory instead of the isolated workdir,
    # discovered during Sprint 3's replay validation. Same isolation
    # discipline as every other path above.
    config.paths.decision_journal = base / "decision_journal.jsonl"


def main() -> None:
    parser = argparse.ArgumentParser(description="Bujji historical replay")
    parser.add_argument("--config", default="config/config.yaml",
                        help="Strategy/timing/risk config — read-only, "
                             "never used for persistence paths unless "
                             "--use-live-paths is also passed.")
    parser.add_argument("--candles", required=True, help="CSV of historical candles")
    parser.add_argument("--workdir", default="data/replay",
                        help="Isolated directory for this replay run's "
                             "session snapshot / journal / database "
                             "(default: data/replay/ — never the live "
                             "process's paths).")
    parser.add_argument("--use-live-paths", action="store_true",
                        help="DANGEROUS: use --config's own journal/database/"
                             "state_file paths verbatim (the SAME paths the "
                             "live process reads/writes) instead of an "
                             "isolated --workdir. This can pick up a stale "
                             "live session snapshot and WILL write this "
                             "replay's synthetic trades into the real "
                             "production journal. Only pass this if you "
                             "specifically intend that.")
    args = parser.parse_args()

    config = AppConfig.load(args.config)
    if not args.use_live_paths:
        _isolate_paths(config, args.workdir)
    config.ensure_dirs()
    logger = setup_logging(config.paths.log_dir, config.log_level)

    if args.use_live_paths:
        logger.warning(
            "replay_using_live_paths: journal=%s database=%s state_file=%s "
            "— this run reads/writes the SAME persistence files as the live "
            "process. Pass --workdir (the default) instead unless this is "
            "deliberate.", config.paths.journal_csv, config.paths.database,
            config.paths.state_file,
        )
    else:
        print(f"Replay isolated under: {args.workdir} "
              f"(journal/database/state_file never touch the live process's "
              f"files — pass --use-live-paths to opt out)")

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
