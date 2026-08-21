"""Structural guard: bujji.mil_next must not be imported by any live-wired
module. This is an automated check, not a documentation claim -- see
bujji/mil_next/__init__.py's own promotion-or-removal contract."""
from __future__ import annotations

import re
from pathlib import Path

LIVE_PATHS = [
    "bujji/live_shadow_operator",
    "bujji/live_pipeline_bridge.py",
    "run_live_shadow.py",
    "run_daily_observation.py",
    "bujji/trading_brain",
    "bujji/production_runtime",
    "bujji/broker",
    "bujji/execution",
    "bujji/runtime_execution",
    "bujji/journal/position_group_journal.py",
]

IMPORT_PATTERN = re.compile(r"\b(from|import)\s+bujji\.mil_next\b")


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "bujji").is_dir():
            return parent
    raise RuntimeError("could not locate repo root from test file location")


def test_mil_next_not_imported_by_any_live_path():
    root = _repo_root()
    offenders = []
    for rel in LIVE_PATHS:
        target = root / rel
        files = [target] if target.is_file() else list(target.rglob("*.py")) if target.is_dir() else []
        for f in files:
            text = f.read_text(errors="ignore")
            if IMPORT_PATTERN.search(text):
                offenders.append(str(f))
    assert offenders == [], f"bujji.mil_next imported by live-wired path(s): {offenders}"
