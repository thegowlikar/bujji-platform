"""Phase 20.15 -- searchable memory index. Deterministic, no ML
embeddings -- a plain grouping/sort over `MarketMemoryRecord`s already
read from `store.py`. No IO, no persistence of its own (rebuilt from
`store.read_all_market_memories()` each time it's needed, exactly like
every other index in this codebase is a derived, never a source-of-
truth, structure).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

from .models import MarketMemoryRecord


@dataclass(frozen=True)
class MarketMemoryIndex:
    """`by_regime_key`: exact-match grouping on the three MIC v0
    dimensions -- the same three dimensions `MarketContext` already
    uses to modify confidence (Phase 20.5), never a fourth, invented
    dimension. `chronological`: every record, sorted by `as_of_time`,
    for the no-look-ahead filtering `retrieval.py` needs."""

    by_regime_key: Dict[Tuple[str, str, str], Tuple[str, ...]]  # (regime, vol, risk) -> memory_ids, oldest first.
    chronological: Tuple[MarketMemoryRecord, ...]
    by_memory_id: Dict[str, MarketMemoryRecord]


def _regime_key(record: MarketMemoryRecord) -> Tuple[str, str, str]:
    return (record.market_regime, record.volatility_state, record.risk_state)


def build_index(records: Sequence[MarketMemoryRecord]) -> MarketMemoryIndex:
    ordered = tuple(sorted(records, key=lambda r: r.as_of_time))
    grouped: Dict[Tuple[str, str, str], List[str]] = defaultdict(list)
    by_id: Dict[str, MarketMemoryRecord] = {}
    for record in ordered:
        grouped[_regime_key(record)].append(record.memory_id)
        by_id[record.memory_id] = record
    return MarketMemoryIndex(
        by_regime_key={k: tuple(v) for k, v in grouped.items()},
        chronological=ordered, by_memory_id=by_id,
    )


def exact_regime_matches(index: MarketMemoryIndex, target: MarketMemoryRecord) -> Tuple[MarketMemoryRecord, ...]:
    """Records sharing target's EXACT (regime, volatility, risk) triple
    -- the coarsest, cheapest, fully deterministic candidate narrowing;
    `retrieval.py`'s own weighted similarity scoring runs on top of
    this, never instead of it."""
    ids = index.by_regime_key.get(_regime_key(target), ())
    return tuple(index.by_memory_id[i] for i in ids)
