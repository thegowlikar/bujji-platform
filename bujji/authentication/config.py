"""Authentication & Broker Session Manager configuration."""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import AUTHENTICATION_MANAGER_VERSION

DEFAULT_AUTHENTICATION_JOURNAL_PATH = "data/authentication/authentication_journal.jsonl"


@dataclass(frozen=True)
class AuthenticationManagerConfig:
    version: str = AUTHENTICATION_MANAGER_VERSION
    journal_path: str = DEFAULT_AUTHENTICATION_JOURNAL_PATH
