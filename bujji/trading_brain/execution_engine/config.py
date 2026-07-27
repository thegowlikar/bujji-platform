"""Execution Engine configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import EXECUTION_ENGINE_VERSION

DEFAULT_EXECUTION_ENGINE_JOURNAL_PATH = "data/trading_brain/execution_engine_journal.jsonl"


@dataclass(frozen=True)
class ExecutionEngineConfig:
    version: str = EXECUTION_ENGINE_VERSION
    journal_path: str = DEFAULT_EXECUTION_ENGINE_JOURNAL_PATH
