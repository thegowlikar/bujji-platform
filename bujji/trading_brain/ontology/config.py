"""Trading Ontology configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import ONTOLOGY_PACKAGE_VERSION

DEFAULT_ONTOLOGY_JOURNAL_PATH = "data/trading_brain/trading_ontology_journal.jsonl"


@dataclass(frozen=True)
class OntologyConfig:
    ruleset_version: str = ONTOLOGY_PACKAGE_VERSION
    journal_path: str = DEFAULT_ONTOLOGY_JOURNAL_PATH
