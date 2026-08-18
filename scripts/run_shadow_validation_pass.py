"""Phase 12 offline validation pass -- reads an already-completed (or
in-progress) shadow session's real `intelligence_cycle.jsonl` and
produces `shadow_validation.jsonl` + `session_intelligence_summary.json`
in the same session directory.

READ-ONLY: opens existing files, writes only the two new output files
listed above. No broker import, no network call, no execution/order/
risk/capital import anywhere in this script.

Usage:
    python scripts/run_shadow_validation_pass.py shadow_sessions/SHADOW-OBSERVATORY-2026-08-06
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/bujji/app")

from bujji.intelligence_completeness.engine import evaluate_cycle_extended
from bujji.intelligence_consistency_checker.engine import check_cycle
from bujji.shadow_validation.engine import build_validation_record
from bujji.shadow_validation.health_metrics import honesty_metrics, understanding_quality
from bujji.shadow_validation.memory_validation import validate_market_memory
from bujji.shadow_validation.session_summary import build_session_intelligence_summary


def load_records(path: str) -> list:
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def main(session_dir: str) -> None:
    cycle_path = f"{session_dir}/intelligence_cycle.jsonl"
    records = load_records(cycle_path)

    completeness_reports = [
        evaluate_cycle_extended(
            r, r.get("memory_health"), r.get("regime_memory"), r.get("narrative"),
        ).to_dict()
        for r in records
    ]
    consistency_warnings = [list(check_cycle(r)) for r in records]

    validation_rows = [
        build_validation_record(r, completeness_reports[i], consistency_warnings[i])
        for i, r in enumerate(records)
    ]
    with open(f"{session_dir}/shadow_validation.jsonl", "w") as f:
        for row in validation_rows:
            f.write(json.dumps(row) + "\n")

    scores = [c["completeness_score"] for c in completeness_reports]
    summary = build_session_intelligence_summary(records, scores)
    summary["understanding_quality"] = understanding_quality(completeness_reports)
    summary["honesty_metrics"] = honesty_metrics(completeness_reports, validation_rows)
    summary["memory_validation"] = validate_market_memory(records)

    with open(f"{session_dir}/session_intelligence_summary.json", "w") as f:
        json.dump(summary, f, indent=2)

    print(f"cycles processed: {len(records)}")
    print(f"shadow_validation.jsonl -> {session_dir}/shadow_validation.jsonl")
    print(f"session_intelligence_summary.json -> {session_dir}/session_intelligence_summary.json")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: run_shadow_validation_pass.py <session_dir>")
        sys.exit(1)
    main(sys.argv[1])
