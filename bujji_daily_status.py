#!/usr/bin/env python
"""Bujji Daily Intelligence Session -- operational status command,
Phase 19.12.

    python bujji_daily_status.py [--heartbeat-path PATH] [--lock-path PATH] [--json]

Read-only. Never starts, stops, or modifies the daily runtime -- reads
the same heartbeat file `run_daily_intelligence_session.py` already
writes (Phase 19.11/19.12), and reuses `bujji.core.process_lock.ProcessLock`
(unmodified) to check whether a live instance currently holds the lock.
"""
from __future__ import annotations

import argparse
import json
import sys

from bujji.shadow_runtime.status import get_operational_status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--heartbeat-path", default="data/daily_session_heartbeat.json")
    parser.add_argument("--lock-path", default="data/daily_intelligence.lock")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    status = get_operational_status(args.heartbeat_path, lock_path=args.lock_path)
    if args.json:
        print(json.dumps(status.to_dict()))
    else:
        print(status.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
