"""Order Construction Service configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import ORDER_CONSTRUCTION_VERSION

DEFAULT_ORDER_CONSTRUCTION_JOURNAL_PATH = "data/trading_brain/order_construction_journal.jsonl"


@dataclass(frozen=True)
class OrderConstructionConfig:
    version: str = ORDER_CONSTRUCTION_VERSION
    journal_path: str = DEFAULT_ORDER_CONSTRUCTION_JOURNAL_PATH
