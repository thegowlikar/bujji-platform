"""NIFTY Contract Builder configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import NIFTY_CONTRACT_BUILDER_VERSION, STRIKE_INTERVAL

DEFAULT_NIFTY_CONTRACT_BUILDER_JOURNAL_PATH = "data/trading_brain/nifty_contract_builder_journal.jsonl"


@dataclass(frozen=True)
class NiftyContractBuilderConfig:
    version: str = NIFTY_CONTRACT_BUILDER_VERSION
    strike_interval: int = STRIKE_INTERVAL
    journal_path: str = DEFAULT_NIFTY_CONTRACT_BUILDER_JOURNAL_PATH
