"""Risk Brain configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import RISK_BRAIN_VERSION

DEFAULT_RISK_BRAIN_JOURNAL_PATH = "data/trading_brain/risk_brain_journal.jsonl"


@dataclass(frozen=True)
class RiskBrainConfig:
    version: str = RISK_BRAIN_VERSION
    journal_path: str = DEFAULT_RISK_BRAIN_JOURNAL_PATH
