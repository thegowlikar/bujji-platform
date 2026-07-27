"""Position Sizing Engine configuration.

All lot counts come from here -- never hard-coded in engine.py.
"""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import POSITION_SIZING_VERSION

DEFAULT_POSITION_SIZING_JOURNAL_PATH = "data/trading_brain/position_sizing_journal.jsonl"


@dataclass(frozen=True)
class PositionSizingConfig:
    minimum_lots: int = 1
    reduced_lots: int = 1
    standard_lots: int = 2
    full_lots: int = 3
    max_lots: int = 10
    version: str = POSITION_SIZING_VERSION
    journal_path: str = DEFAULT_POSITION_SIZING_JOURNAL_PATH
