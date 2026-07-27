"""Runtime Safety Gate configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import RUNTIME_SAFETY_VERSION

DEFAULT_RUNTIME_SAFETY_JOURNAL_PATH = "data/runtime_safety/runtime_safety_journal.jsonl"


@dataclass(frozen=True)
class RuntimeSafetyConfig:
    version: str = RUNTIME_SAFETY_VERSION
    journal_path: str = DEFAULT_RUNTIME_SAFETY_JOURNAL_PATH
