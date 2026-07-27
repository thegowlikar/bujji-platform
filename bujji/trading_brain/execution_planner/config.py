"""Execution Planner configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import EXECUTION_PLANNER_VERSION

DEFAULT_EXECUTION_PLANNER_JOURNAL_PATH = "data/trading_brain/execution_planner_journal.jsonl"


@dataclass(frozen=True)
class ExecutionPlannerConfig:
    version: str = EXECUTION_PLANNER_VERSION
    journal_path: str = DEFAULT_EXECUTION_PLANNER_JOURNAL_PATH
