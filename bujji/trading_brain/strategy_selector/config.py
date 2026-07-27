"""Strategy Selector configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import STRATEGY_SELECTOR_VERSION

DEFAULT_STRATEGY_SELECTOR_JOURNAL_PATH = "data/trading_brain/strategy_selector_journal.jsonl"


@dataclass(frozen=True)
class StrategySelectorConfig:
    version: str = STRATEGY_SELECTOR_VERSION
    journal_path: str = DEFAULT_STRATEGY_SELECTOR_JOURNAL_PATH
