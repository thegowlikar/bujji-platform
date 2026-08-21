"""Point-in-time Market Memory query -- Phase 19.5.

Same `as_of_time`-threaded, no-look-ahead discipline
`reality_memory/catalog.py` already established for Reality-tier
queries (Phase 17J.1), applied here to memory retrieval: every query
takes an explicit `as_of_time` and NEVER calls `datetime.now()`/
`now_ist()` internally. A candidate whose own `as_of_time` is strictly
after the query's `as_of_time` is future information relative to that
query and is excluded -- structurally, not by convention.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Tuple

from .memory_models import MarketMemoryEntry
from .memory_similarity import SimilarityExplanation, explain_similarity


def _parse(as_of_time: str) -> datetime:
    return datetime.fromisoformat(as_of_time)


def find_similar_memories_as_of(
    target: MarketMemoryEntry, universe: List[MarketMemoryEntry], *, as_of_time: datetime, top_n: int = 5,
) -> List[Tuple[MarketMemoryEntry, SimilarityExplanation]]:
    """Only compares `target` against candidates whose own `as_of_time`
    is at or before `as_of_time` -- a candidate from the future relative
    to this query can never leak in, regardless of what order it
    happens to sit in `universe` or when it was actually recorded into
    the store. `target` itself is always excluded from its own
    candidate pool (by `market_memory_id`)."""
    eligible = [
        entry for entry in universe
        if entry.market_memory_id != target.market_memory_id and _parse(entry.as_of_time) <= as_of_time
    ]
    scored = [(entry, explain_similarity(target, entry)) for entry in eligible]
    scored.sort(key=lambda pair: pair[1].similarity_pct, reverse=True)
    return scored[:top_n]
