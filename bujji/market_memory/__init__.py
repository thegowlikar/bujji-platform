"""bujji.market_memory -- Phase 20.15 (Bujji OS v1.0 Roadmap, Cycle-1
lineage).

The missing Interface Map stage: Shadow Result -> Market Memory ->
Future Intelligence. Stores and retrieves EVIDENCE only -- never a
strategy optimizer. This package does NOT tune strategies, discover
new strategies, change scores, modify MIC, or create any holdings- or
execution-related record. Enforced structurally by
`tests/test_market_memory.py`.

STORAGE/RETRIEVAL ONLY -- interpretation lives elsewhere. Phase
20.15.1's own confidence-adjustment logic deliberately does NOT live
in this package (per that phase's own explicit instruction: "keep
storage separate from interpretation") -- see `bujji.memory_intelligence`
instead, which consumes this package's own `MemoryContext`/
`OutcomeMemoryRecord` outputs, never the reverse.

NAMING / COLLISION AUDIT (this phase's own mandatory Step 1): no
`bujji/market_memory/` package existed prior to this phase.

Broad audit of existing memory systems found:

  (B, reusable PATTERN only) `bujji.market_understanding.{memory_models,
  memory_similarity,memory_query}` (Phase 19.5) already solved exactly
  this shape -- deterministic weighted-dimension similarity, explainable
  scoring, no-look-ahead `as_of_time` querying, honest `NOT_YET_OBSERVED`
  outcomes never guessed. That module's own `MarketMemoryEntry` is keyed
  to `MarketIntelligenceSnapshot.intelligence_snapshot_id` (Phase 19.3's
  own lineage identity) over a six-dimension feature space this
  package's own three MIC v0 dimensions do not share -- the ALGORITHM
  shape is mirrored here (`retrieval.py`'s `score_similarity`/
  `find_similar_conditions_as_of`), the code itself is not imported.
  `bujji.market_state_graph.memory` (Phase 19.8) extends that same
  lineage further and is equally not reusable code-wise.

  (A, reusable directly) `bujji.state_persistence.store.EventStore`
  (Phase 15B) -- genuinely domain-agnostic append-only persistence,
  already used by multiple independent systems. Reused here directly
  in `store.py`, never a third bespoke JSONL convention.

  (C, wrong domain) `bujji.market_regime_memory` (Phase 11, session-
  scoped regime duration tracking, not cross-session), `bujji.
  outcome_memory` (Phase 15N, real POSITION-level P&L outcome tracking
  -- Cycle 1 has no positions), `bujji.reality_memory` (a data-capture
  availability catalog, not decision memory), `bujji.trading_brain.
  risk_governor.adaptive_risk_memory` (real capital risk memory, MSI/
  Trading Brain lineage). None imported.

ARCHITECTURE POSITION: sits between Shadow Result (Phase 20.11's
`DecisionObservation`) and `bujji.memory_intelligence` (Phase 20.15.1).
`builder.py` composes `MarketMemoryRecord`/`DecisionMemoryRecord`
directly from a real `DecisionObservation`, never recomputing any
upstream layer's own values.
"""
from __future__ import annotations

from .builder import (
    build_decision_memory_record, build_known_outcome_memory_record,
    build_market_memory_record, build_pending_outcome_memory_record,
)
from .explain import explain_memory_context
from .index import MarketMemoryIndex, build_index, exact_regime_matches
from .models import (
    DecisionMemoryRecord, MarketMemoryRecord, OutcomeMemoryRecord,
    STATUS_KNOWN, STATUS_NOT_YET_OBSERVED, memory_id_for,
)
from .retrieval import (
    MemoryContext, SimilarityExplanation, build_memory_context,
    find_similar_conditions_as_of, score_similarity,
)
from .store import (
    read_all_decision_memories, read_all_market_memories, read_all_outcome_memories,
    record_decision_memory, record_market_memory, record_outcome_memory,
)

__all__ = [
    "MarketMemoryRecord", "DecisionMemoryRecord", "OutcomeMemoryRecord",
    "STATUS_KNOWN", "STATUS_NOT_YET_OBSERVED", "memory_id_for",
    "build_market_memory_record", "build_decision_memory_record",
    "build_pending_outcome_memory_record", "build_known_outcome_memory_record",
    "record_market_memory", "record_decision_memory", "record_outcome_memory",
    "read_all_market_memories", "read_all_decision_memories", "read_all_outcome_memories",
    "MarketMemoryIndex", "build_index", "exact_regime_matches",
    "SimilarityExplanation", "score_similarity", "find_similar_conditions_as_of",
    "MemoryContext", "build_memory_context",
    "explain_memory_context",
]
