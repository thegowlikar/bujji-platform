"""Broker Adapter configuration.

Deliberately minimal: this sprint's adapter has no broker credential,
endpoint, or environment setting to hold -- it is a pure translation
table. A future series wiring this adapter to a real Runtime Execution
Service is the correct place to add those, not this one.
"""
from __future__ import annotations

from dataclasses import dataclass

from .taxonomy import BROKER_ADAPTER_VERSION, BROKER_FYERS

DEFAULT_BROKER_ADAPTER_JOURNAL_PATH = "data/broker_adapter/broker_adapter_journal.jsonl"


@dataclass(frozen=True)
class BrokerAdapterConfig:
    version: str = BROKER_ADAPTER_VERSION
    default_broker: str = BROKER_FYERS
    journal_path: str = DEFAULT_BROKER_ADAPTER_JOURNAL_PATH
