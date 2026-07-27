"""Decision Pipeline Qualification configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .models import QUALIFICATION_VERSION

DEFAULT_QUALIFICATION_JOURNAL_PATH = "data/trading_brain/decision_pipeline_qualification_journal.jsonl"
DEFAULT_REPLAY_COUNT = 10


@dataclass(frozen=True)
class QualificationConfig:
    version: str = QUALIFICATION_VERSION
    journal_path: str = DEFAULT_QUALIFICATION_JOURNAL_PATH
    replay_count: int = DEFAULT_REPLAY_COUNT
