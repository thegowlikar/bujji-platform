"""Intelligence Adapter — Configuration — BUJJI Options OS, Integration
Series 1, Sprint 1.

A finite, statically-typed configuration object -- no dynamic loading,
no environment-variable magic beyond explicit paths. Carries no
credential, no broker endpoint, no order-routing detail of any kind.
The adapter is entirely inert unless `enabled` is True; the default is
False everywhere this config is constructed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ADAPTER_CONFIG_VERSION = "1.0.0"

# Default location of MIC v2's own repo root -- needed only to make its
# `mic_v2.consumer`/`mic_v2.models` modules importable (the published
# Consumer API contract). No other path under this root is ever read.
DEFAULT_MIC_V2_ROOT = Path("/opt/bujji-mic-v2")

# Default location of the Consumer Journal (mic_v2.journal.consumer_journal,
# Sprint 29) -- the durable, append-only, already-published record this
# adapter reads. This IS the Consumer API's own persisted surface, not an
# internal MIC v2 implementation detail.
DEFAULT_CONSUMER_JOURNAL_PATH = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")

# Default location of the Opinion Journal (mic_v2.journal.opinion_journal,
# Engineering Series 19, Sprint 1 / Addendum 8) -- read by
# opinion_reader.read_opinion_classification() only (Integration Series
# 4, Sprint 1). Not a field on IntelligenceAdapterConfig itself, since
# the Adapter class never reads it -- only the Orchestrator's own
# opinion-source wiring does, using this constant as its default.
DEFAULT_OPINION_JOURNAL_PATH = Path("/opt/bujji-mic-v2/qualification_campaign_1/opinion_journal.jsonl")


@dataclass(frozen=True)
class IntelligenceAdapterConfig:
    enabled: bool = False
    mic_v2_root: Path = DEFAULT_MIC_V2_ROOT
    consumer_journal_path: Path = DEFAULT_CONSUMER_JOURNAL_PATH


DEFAULT_ADAPTER_CONFIG = IntelligenceAdapterConfig()
