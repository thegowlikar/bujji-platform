"""Market State Builder configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import MARKET_STATE_BUILDER_VERSION

DEFAULT_MARKET_STATE_JOURNAL_PATH = "data/trading_brain/market_state_journal.jsonl"


@dataclass(frozen=True)
class MarketStateConfig:
    version: str = MARKET_STATE_BUILDER_VERSION
    journal_path: str = DEFAULT_MARKET_STATE_JOURNAL_PATH
