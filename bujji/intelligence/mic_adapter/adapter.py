"""Intelligence Adapter — BUJJI Options OS, Integration Series 1, Sprint 1.

Strictly read-only. The only supported integration point between BUJJI
Options OS and MIC v2. Reads MIC v2's Consumer API only (via
`query.py`, which reads only the Consumer Journal -- Sprint 29's own
published, durable representation). Never imports a MIC v2 reasoning
engine. Never reads a MIC v2 journal other than the Consumer Journal.
Never invokes replay, publication, certification, or runtime.

The adapter exists for OBSERVATION ONLY. It cannot influence entry
logic, exit logic, qualification, sizing, filtering, risk, execution,
or broker communication -- there is no such method anywhere on this
module's surface, structurally, not merely by convention.
"""
from __future__ import annotations

from typing import Optional

from .config import IntelligenceAdapterConfig
from .mapper import map_consumer_record
from .models import IntelligenceSnapshot
from .query import latest_record, read_consumer_records, record_by_publication_id, record_by_replay_id


class IntelligenceAdapter:
    """Every method re-reads the Consumer Journal fresh -- no mutable
    state is cached between calls, so the adapter can never serve a
    stale or inconsistent view across two calls within the same
    process.
    """

    def __init__(self, config: Optional[IntelligenceAdapterConfig] = None) -> None:
        self._config = config or IntelligenceAdapterConfig()

    def _load_records(self) -> list:
        if not self._config.consumer_journal_path.exists():
            return []
        return read_consumer_records(self._config.consumer_journal_path, self._config.mic_v2_root)

    def load_latest_snapshot(self) -> Optional[IntelligenceSnapshot]:
        record = latest_record(self._load_records())
        return map_consumer_record(record) if record is not None else None

    def load_snapshot(self, publication_id: str) -> Optional[IntelligenceSnapshot]:
        record = record_by_publication_id(self._load_records(), publication_id)
        return map_consumer_record(record) if record is not None else None

    def load_snapshot_by_replay(self, replay_id: str) -> Optional[IntelligenceSnapshot]:
        record = record_by_replay_id(self._load_records(), replay_id)
        return map_consumer_record(record) if record is not None else None
