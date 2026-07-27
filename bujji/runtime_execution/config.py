"""Runtime Execution Orchestrator configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import RUNTIME_EXECUTION_VERSION

DEFAULT_RUNTIME_EXECUTION_JOURNAL_PATH = "data/runtime_execution/runtime_execution_journal.jsonl"


@dataclass(frozen=True)
class RuntimeExecutionConfig:
    version: str = RUNTIME_EXECUTION_VERSION
    journal_path: str = DEFAULT_RUNTIME_EXECUTION_JOURNAL_PATH
