"""Intelligence Adapter Query — BUJJI Options OS, Integration Series 1,
Sprint 1.

Reads MIC v2's published Consumer API contract ONLY -- the Consumer
Journal (`mic_v2.journal.consumer_journal.ConsumerJournal`, Sprint 29's
own append-only, durable representation of everything the Consumer API
has ever exposed). Never reads a MIC v2 reasoning journal (evidence,
qualifications, traces, ...), never imports a reasoning engine, never
invokes replay, publication, certification, or runtime. The only MIC v2
imports anywhere in this module are `mic_v2.journal.consumer_journal`
and the plain data models it returns -- the published Consumer API
surface itself, not an internal implementation detail.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional


def _ensure_mic_v2_importable(mic_v2_root: Path) -> None:
    root_str = str(mic_v2_root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def read_consumer_records(consumer_journal_path: Path, mic_v2_root: Path) -> list:
    """Reads every ConsumerRecord the Consumer API has ever published,
    in append order. Read-only: `ConsumerJournal.read_all()` (Sprint 29)
    only ever opens its file in mode 'r'.
    """
    _ensure_mic_v2_importable(mic_v2_root)
    from mic_v2.journal.consumer_journal import ConsumerJournal  # Consumer API surface only.

    journal = ConsumerJournal(consumer_journal_path)
    return journal.read_all()


def latest_record(records: list):
    return records[-1] if records else None


def record_by_publication_id(records: list, publication_id: str):
    for record in reversed(records):
        if record.publication_id == publication_id:
            return record
    return None


def record_by_replay_id(records: list, replay_id: str):
    for record in reversed(records):
        if record.replay_id == replay_id:
            return record
    return None
