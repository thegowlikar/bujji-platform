"""Capital Brain configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import CAPITAL_BRAIN_VERSION

DEFAULT_CAPITAL_BRAIN_JOURNAL_PATH = "data/trading_brain/capital_brain_journal.jsonl"


@dataclass(frozen=True)
class CapitalBrainConfig:
    version: str = CAPITAL_BRAIN_VERSION
    journal_path: str = DEFAULT_CAPITAL_BRAIN_JOURNAL_PATH
