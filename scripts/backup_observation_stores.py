#!/usr/bin/env python3
"""Nightly backup of every store the system cannot recreate.

WHAT AND WHY: intraday option-chain observations cannot be re-fetched from
any source (bujji-token-preflight.service's own header documents this) --
until 2026-08-19 the ONLY copy of every live capture day lived in one SQLite
file on one droplet. This script snapshots:

  historical_observations.db   via sqlite3's online backup API (safe against
                               concurrent writers -- never a raw file copy)
  market_reality_snapshots.db  same
  layer0_data/                 tar.gz (append-only JSONL: quotes/depth)
  decision + trading journals  tar.gz

into /opt/bujji/backups/YYYY-MM-DD/, keeping the newest 7 days.

DISCLOSED LIMITATION: this is an ON-BOX backup. It survives corruption,
accidental deletion, and bad deploys; it does NOT survive droplet loss.
Shipping off-box needs a destination only the operator can provide (object
storage credentials or a second host) -- recorded as an open operator
decision in the Master Plan risk register. An on-box copy tonight beats a
perfect copy never.
"""
from __future__ import annotations

import datetime
import gzip
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

APP = Path("/opt/bujji/app")
DEST_ROOT = Path("/opt/bujji/backups")
KEEP_DAYS = 7

SQLITE_STORES = [
    APP / "data/historical_reality/normalized/historical_observations.db",
    APP / "data/historical_reality/normalized/market_reality_snapshots.db",
]
TAR_SETS = {
    "layer0_data.tar.gz": [APP / "layer0_data"],
    "journals.tar.gz": [
        APP / "data/options_os_trading_journal.db",
        APP / "data/options_os_shadow_position_group_journal.db",
        APP / "data/live_shadow_campaign",
        APP / "data/daily_intelligence_artifacts.jsonl",
    ],
}


def main() -> int:
    today = datetime.date.today().isoformat()
    dest = DEST_ROOT / today
    dest.mkdir(parents=True, exist_ok=True)
    errors = []

    for src in SQLITE_STORES:
        if not src.exists():
            errors.append(f"missing sqlite source: {src}")
            continue
        out = dest / src.name
        try:
            with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as s, sqlite3.connect(out) as d:
                s.backup(d)
            print(f"sqlite backup OK: {src.name} -> {out} ({out.stat().st_size:,} bytes)")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"sqlite backup FAILED {src.name}: {exc}")

    for tar_name, sources in TAR_SETS.items():
        out = dest / tar_name
        try:
            with tarfile.open(out, "w:gz") as tf:
                for src in sources:
                    if src.exists():
                        tf.add(src, arcname=src.name)
                    else:
                        print(f"  (absent, skipped: {src})")
            print(f"tar OK: {tar_name} ({out.stat().st_size:,} bytes)")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"tar FAILED {tar_name}: {exc}")

    # Retention: newest KEEP_DAYS date-dirs survive.
    dirs = sorted(d for d in DEST_ROOT.iterdir() if d.is_dir())
    for old in dirs[:-KEEP_DAYS]:
        shutil.rmtree(old)
        print(f"retention: removed {old.name}")

    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print(f"backup complete: {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
