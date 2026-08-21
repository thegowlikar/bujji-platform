#!/usr/bin/env python
"""LSQ-1 — Chief Engineer's Log generator.

Produces the format the user specified: System health / Replay
fidelity / Tick integrity / Position reconciliation (each PASS/FAIL/
PARTIAL, evidence-backed), Engineering concerns, and an Overall
confidence score with its own disclosed arithmetic -- never a number
picked for optics.

Usage: lsq_chief_engineer_log.py DAY_LABEL PATH_TO_session.log PATH_TO_portfolio_valuation.jsonl [PATH_TO_consistency_check_json]
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/bujji/app/tools")
from lsq_daily_report import parse_log, parse_journal  # noqa: E402


def _confidence(deductions: list) -> float:
    """Starts at 10.0. Every real deduction is disclosed by name, never
    hidden inside a single opaque number. Floors at 0.0."""
    score = 10.0
    for _, amount in deductions:
        score -= amount
    return max(0.0, round(score, 1))


def build_log(day_label, log_path, journal_path, consistency_path=None):
    log_data = parse_log(log_path)
    trades, exit_count = parse_journal(journal_path)

    consistency = None
    if consistency_path:
        with open(consistency_path) as f:
            consistency = json.load(f)

    # --- System health ---
    system_health_fail = bool(log_data["errors"]) or bool(log_data["cadences_failed"])
    system_health = "FAIL" if system_health_fail else "PASS"

    # --- Replay fidelity --- (only meaningfully checkable when a
    # consistency-check result is supplied; otherwise honestly NOT_RUN,
    # never assumed PASS).
    if consistency is None:
        replay_fidelity = "NOT_RUN (no consistency-check result supplied this run)"
    elif consistency["STOP_QUALIFICATION"]:
        replay_fidelity = "FAIL"
    else:
        replay_fidelity = "PASS (per-trade checks passed; replay-vs-live re-execution itself is a separate step -- see consistency-check output)"

    # --- Tick integrity ---
    tick_integrity = "PASS" if not log_data["watchdog_critical"] else "FAIL"

    # --- Position reconciliation ---
    if consistency is None:
        position_reconciliation = "NOT_RUN"
    elif consistency["orphan_positions"]:
        position_reconciliation = f"FAIL (orphan positions: {consistency['orphan_positions']})"
    else:
        position_reconciliation = "PASS"

    # --- Confidence, real disclosed arithmetic ---
    deductions = []
    if log_data["errors"]:
        deductions.append((f"{len(log_data['errors'])} real ERROR-level event(s)", min(3.0, 0.5 * len(log_data["errors"]))))
    if log_data["cadences_failed"]:
        deductions.append((f"{len(log_data['cadences_failed'])} unexpected cadence failure(s)", min(3.0, 1.0 * len(log_data["cadences_failed"]))))
    if log_data["watchdog_critical"]:
        deductions.append(("watchdog reached CRITICAL_FAILURE", 3.0))
    if consistency and consistency["STOP_QUALIFICATION"]:
        deductions.append(("critical qualification failure(s) in per-trade consistency checks", 4.0))
    if len(log_data["reconnects"]) > 3:
        deductions.append((f"{len(log_data['reconnects'])} reconnects (>3, elevated but not necessarily a failure)", 0.5))

    confidence = _confidence(deductions)

    lines = [f"# {day_label}\n"]
    lines.append(f"* System health: {system_health}")
    lines.append(f"* Replay fidelity: {replay_fidelity}")
    lines.append(f"* Tick integrity: {tick_integrity}")
    lines.append(f"* Position reconciliation: {position_reconciliation}")
    lines.append("* Engineering concerns:")
    if log_data["reconnects"]:
        lines.append(f"   * {len(log_data['reconnects'])} reconnect(s) during the session.")
    if log_data["errors"]:
        lines.append(f"   * {len(log_data['errors'])} real ERROR-level event(s) -- see Daily Report for detail.")
    if exit_count:
        lines.append(f"   * {exit_count} real trade(s) completed and journaled.")
    if not (log_data["reconnects"] or log_data["errors"]):
        lines.append("   * No notable engineering concerns today.")
    lines.append("   * No code changes recommended today." if not deductions else "   * See confidence deductions below for what needs attention.")
    lines.append("")
    lines.append(f"Overall confidence: {confidence}/10")
    if deductions:
        lines.append("Confidence deductions (disclosed, not hidden in the number):")
        for reason, amount in deductions:
            lines.append(f"  -{amount}: {reason}")
    else:
        lines.append("No deductions -- clean session against everything this log checks.")

    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: lsq_chief_engineer_log.py DAY_LABEL PATH_TO_session.log PATH_TO_portfolio_valuation.jsonl [PATH_TO_consistency_check_json]")
        sys.exit(2)
    consistency_path = sys.argv[4] if len(sys.argv) > 4 else None
    print(build_log(sys.argv[1], sys.argv[2], sys.argv[3], consistency_path))
