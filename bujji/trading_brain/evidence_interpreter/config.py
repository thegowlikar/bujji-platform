"""Evidence Interpreter configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import INTERPRETER_VERSION

DEFAULT_EVIDENCE_INTERPRETER_JOURNAL_PATH = "data/trading_brain/evidence_interpreter_journal.jsonl"


@dataclass(frozen=True)
class EvidenceInterpreterConfig:
    interpreter_version: str = INTERPRETER_VERSION
    journal_path: str = DEFAULT_EVIDENCE_INTERPRETER_JOURNAL_PATH
