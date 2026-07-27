"""Runtime Session Manager configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import RUNTIME_SESSION_VERSION

DEFAULT_RUNTIME_SESSION_JOURNAL_PATH = "data/runtime_session/runtime_session_journal.jsonl"


@dataclass(frozen=True)
class RuntimeSessionConfig:
    version: str = RUNTIME_SESSION_VERSION
    journal_path: str = DEFAULT_RUNTIME_SESSION_JOURNAL_PATH
