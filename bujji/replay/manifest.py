"""Replay Corpus Manifest — BUJJI Options OS v3, Engineering Series 59
(extended by Series 64).

`CorpusManifest` is the immutable metadata record attached to every
generated replay corpus. It carries no market data itself -- only
identity, provenance, and a checksum over the sessions it describes --
so a corpus can be referenced, compared across releases, and verified
without re-reading the underlying session data.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Tuple

from .schema_version import CURRENT_SCHEMA_VERSION as CURRENT_SESSION_SCHEMA_VERSION

SCHEMA_VERSION = "1.0.0"

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class CorpusManifest:
    """Immutable after construction. `checksum` is computed by the
    caller (see `corpus_builder.compute_checksum`) over the exact set
    of sessions included in the corpus -- this dataclass never
    computes it itself, so the manifest can never silently drift from
    what was actually checksummed.

    `schema_version` is this manifest's own format version (unchanged
    since Series 59). `session_schema_version` (Series 64) records
    which `HistoricalSessionRecord` schema built this corpus -- purely
    informational, never consumed by validation or checksum
    computation, so its addition changes no existing corpus's
    checksum or fingerprint.
    """

    corpus_id: str
    source_description: str
    trading_dates: Tuple[str, ...]
    session_count: int
    checksum: str
    generation_timestamp: str
    schema_version: str = SCHEMA_VERSION
    session_schema_version: str = CURRENT_SESSION_SCHEMA_VERSION


def build_corpus_id(source_description: str, trading_dates: Tuple[str, ...], timestamp: str) -> str:
    """Deterministic identifier -- `hashlib.md5` over a fixed seed,
    never `uuid4()`, matching the identifier discipline used
    throughout this project since Series 31.
    """
    seed = f"CORPUS|{source_description}|{'|'.join(trading_dates)}|{timestamp}"
    return "CORPUS-" + hashlib.md5(seed.encode()).hexdigest()[:16]


def build_manifest(
    source_description: str,
    trading_dates: Tuple[str, ...],
    session_count: int,
    checksum: str,
    clock: Clock = _real_clock,
    session_schema_version: str = CURRENT_SESSION_SCHEMA_VERSION,
) -> CorpusManifest:
    timestamp = clock().isoformat()
    corpus_id = build_corpus_id(source_description, trading_dates, timestamp)
    return CorpusManifest(
        corpus_id=corpus_id,
        source_description=source_description,
        trading_dates=trading_dates,
        session_count=session_count,
        checksum=checksum,
        generation_timestamp=timestamp,
        schema_version=SCHEMA_VERSION,
        session_schema_version=session_schema_version,
    )
