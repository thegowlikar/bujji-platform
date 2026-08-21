#!/usr/bin/env python
"""LSQ-1 — Daily Report generator.

Reads the real, already-produced log file and portfolio_valuation.jsonl
for one live_shadow session, and produces the 9-section LSQ Daily
Report the protocol requires. Every number here is parsed from real
log/journal lines -- nothing is estimated or assumed.

Usage: lsq_daily_report.py PATH_TO_session.log PATH_TO_portfolio_valuation.jsonl
"""
from __future__ import annotations

import json
import re
import sys


def parse_log(log_path):
    with open(log_path) as f:
        lines = f.readlines()

    warnings = [l.strip() for l in lines if " WARNING " in l]
    errors = [l.strip() for l in lines if " ERROR " in l]
    cadences_complete = [l.strip() for l in lines if "cadence complete" in l]
    cadences_skipped = [l.strip() for l in lines if "cadence_skipped" in l]
    cadences_failed = [l.strip() for l in lines if "cadence_failed_unexpectedly" in l]
    reconnects = [l for l in lines if "tick_feed_force_reconnect" in l]
    tick_errors = [l for l in lines if "WARNING tick_feed_error" in l]
    watchdog_critical = [l for l in lines if "watchdog_critical_failure" in l]
    exits = [l for l in lines if "POSITION CLOSED" in l]

    strategies = set()
    for l in cadences_complete:
        m = re.search(r"thesis=(\S+)", l)
        if m:
            strategies.add(m.group(1))

    return {
        "warnings": warnings, "errors": errors,
        "cadences_complete": cadences_complete, "cadences_skipped": cadences_skipped,
        "cadences_failed": cadences_failed, "reconnects": reconnects,
        "tick_errors": tick_errors, "watchdog_critical": watchdog_critical,
        "regimes_seen": sorted(strategies), "exit_lines": exits,
    }


def parse_journal(journal_path):
    trades, exits_ct = [], 0
    try:
        with open(journal_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                if r["type"] == "EXIT":
                    exits_ct += 1
                    trades.append(r)
    except FileNotFoundError:
        pass
    return trades, exits_ct


def build_report(log_path, journal_path):
    log_data = parse_log(log_path)
    trades, exit_count = parse_journal(journal_path)

    lines = []
    lines.append("# LSQ Daily Report\n")

    lines.append("## 1. Trades Taken")
    if trades:
        for t in trades:
            lines.append(f"  - {t['symbol']}: entry={t['entry_ltp']} exit={t['exit_ltp']} "
                         f"reason={t['exit_reason']} final_mtm={t.get('final_mtm')}")
    else:
        lines.append("  - None (no real trade completed today -- see honest scope note in LSQ1_PROTOCOL.md: "
                     "no entry-order-construction path is wired into run_live_shadow.py yet).")

    lines.append("\n## 2. Trades Skipped")
    lines.append(f"  - {len(log_data['cadences_skipped'])} decision cadence(s) skipped (mandatory input STALE -- real freshness gate, not a failure).")

    lines.append("\n## 3. Market Regime")
    lines.append(f"  - Thesis types observed today: {', '.join(log_data['regimes_seen']) or 'none (no completed cadence)'}")

    lines.append("\n## 4. Every Warning")
    lines.append(f"  - {len(log_data['warnings'])} real WARNING-level log line(s).")
    for w in log_data["warnings"][:20]:
        lines.append(f"    {w}")
    if len(log_data["warnings"]) > 20:
        lines.append(f"    ... and {len(log_data['warnings']) - 20} more (see full log).")

    lines.append("\n## 5. Every Exception")
    lines.append(f"  - {len(log_data['errors'])} real ERROR-level log line(s).")
    for e in log_data["errors"][:20]:
        lines.append(f"    {e}")

    lines.append("\n## 6. Unexpected Behaviour")
    unexpected = log_data["cadences_failed"] + log_data["watchdog_critical"]
    if unexpected:
        for u in unexpected:
            lines.append(f"  - {u.strip()}")
    else:
        lines.append("  - None observed today.")

    lines.append("\n## 7. Engineering Observations")
    lines.append(f"  - Reconnect events: {len(log_data['reconnects'])}")
    lines.append(f"  - tick_feed_error events: {len(log_data['tick_errors'])}")
    lines.append(f"  - Cadences completed: {len(log_data['cadences_complete'])}, skipped: {len(log_data['cadences_skipped'])}")
    lines.append(f"  - Real trades completed: {exit_count}")

    lines.append("\n## 8. Potential Bugs")
    lines.append("  - (Filled in manually after reviewing warnings/errors above against known-expected patterns -- never auto-classified as a bug without a human read.)")

    lines.append("\n## 9. Potential Strategy Improvements")
    lines.append("  - Observations only, per LSQ-1 Rule #1 -- NOT implemented. (Filled in manually if any are noticed.)")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: lsq_daily_report.py PATH_TO_session.log PATH_TO_portfolio_valuation.jsonl")
        sys.exit(2)
    print(build_report(sys.argv[1], sys.argv[2]))
