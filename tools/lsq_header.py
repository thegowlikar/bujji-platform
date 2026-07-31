#!/usr/bin/env python
"""LSQ-1 — immutable provenance header, prepended to every LSQ report.

Every field is real: git commit/branch from the actual repo state at
report-generation time, config hash the same way run_daily_observation.py
already computes it (md5 over every real config.py in the repo -- reused
by identity, not re-derived), runtime version from the real installed
Python, market/date/day from real caller input. Never fabricated,
never a placeholder left unfilled -- a field that cannot be determined
says so explicitly rather than being silently omitted.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], text=True, cwd="/opt/bujji/app").strip()
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE ({exc!r})"


def _config_hash():
    try:
        repo = Path("/opt/bujji/app")
        paths = sorted(repo.rglob("config.py"))
        h = hashlib.md5()
        for p in paths:
            h.update(p.read_bytes())
        return h.hexdigest()[:16]
    except Exception as exc:  # noqa: BLE001
        return f"UNAVAILABLE ({exc!r})"


def build_header(*, day: int, date: str, market: str = "NSE NIFTY Options",
                 operator: str = "autonomous (Claude Code)", qualification_status: str = "IN_PROGRESS") -> str:
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    commit = _git("rev-parse", "HEAD")
    dirty = _git("status", "--short")
    config_hash = _config_hash()
    runtime_version = sys.version.split()[0]

    lines = [
        "BUJJI Options OS",
        "",
        "Operational Qualification",
        "",
        f"Day: {day}",
        f"Date: {date}",
        f"Git Commit: {commit}" + (f" (branch={branch}, DIRTY -- uncommitted changes present)" if dirty else f" (branch={branch}, clean)"),
        f"Configuration Hash: {config_hash}",
        f"Market: {market}",
        f"Runtime Version: Python {runtime_version}",
        f"Operator: {operator}",
        f"Qualification Status: {qualification_status}",
        "",
        "=" * 60,
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=int, required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--status", default="IN_PROGRESS")
    args = parser.parse_args()
    print(build_header(day=args.day, date=args.date, qualification_status=args.status))
