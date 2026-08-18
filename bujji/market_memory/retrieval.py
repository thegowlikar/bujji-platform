"""Phase 20.15 -- retrieval. Returns historical EVIDENCE only -- never
decides, never scores a strategy, never computes a confidence
adjustment (that step, if it is ever built, is explicitly out of this
phase's own scope; see this package's own `__init__.py`). Mirrors the
PATTERN `bujji.market_understanding.memory_similarity`/`memory_query`
(Phase 19.5) already established -- weighted deterministic dimension
scoring, explainable, no ML -- applied to Cycle 1's own three MIC v0
dimensions instead of that lineage's own six-dimension feature space
(disclosed, not imported: that module's own `MarketMemoryEntry` is
keyed to a different lineage's snapshot identity).
"""
from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .index import MarketMemoryIndex
from .models import DecisionMemoryRecord, MarketMemoryRecord, OutcomeMemoryRecord, STATUS_KNOWN

# Disclosed, not tuned -- mirrors the same "documented weight, not
# optimized" discipline every prior phase's own constants use.
WEIGHT_REGIME = 40
WEIGHT_VOLATILITY = 30
WEIGHT_RISK = 30
MAX_POSSIBLE_SCORE = WEIGHT_REGIME + WEIGHT_VOLATILITY + WEIGHT_RISK


@dataclass(frozen=True)
class SimilarityExplanation:
    memory_id: str
    score: int
    max_score: int
    regime_match: bool
    volatility_match: bool
    risk_match: bool

    def render(self) -> str:
        return (f"{self.memory_id}: {self.score}/{self.max_score} "
                f"(regime={'match' if self.regime_match else 'differ'}, "
                f"volatility={'match' if self.volatility_match else 'differ'}, "
                f"risk={'match' if self.risk_match else 'differ'})")


def score_similarity(target: MarketMemoryRecord, candidate: MarketMemoryRecord) -> SimilarityExplanation:
    regime_match = target.market_regime == candidate.market_regime
    vol_match = target.volatility_state == candidate.volatility_state
    risk_match = target.risk_state == candidate.risk_state
    score = (WEIGHT_REGIME if regime_match else 0) + (WEIGHT_VOLATILITY if vol_match else 0) \
        + (WEIGHT_RISK if risk_match else 0)
    return SimilarityExplanation(
        memory_id=candidate.memory_id, score=score, max_score=MAX_POSSIBLE_SCORE,
        regime_match=regime_match, volatility_match=vol_match, risk_match=risk_match,
    )


def find_similar_conditions_as_of(
    target: MarketMemoryRecord, index: MarketMemoryIndex, *, as_of_time: str, top_n: int = 5,
) -> List[Tuple[MarketMemoryRecord, SimilarityExplanation]]:
    """Only compares `target` against candidates whose own `as_of_time`
    is STRICTLY BEFORE `as_of_time` -- never the target's own record,
    never a future one, regardless of storage order. Empty universe or
    zero qualifying candidates returns an empty list honestly -- never
    a fabricated match."""
    cutoff = datetime.fromisoformat(as_of_time)
    candidates = [r for r in index.chronological
                  if r.memory_id != target.memory_id and datetime.fromisoformat(r.as_of_time) < cutoff]
    scored = [(c, score_similarity(target, c)) for c in candidates]
    scored.sort(key=lambda pair: (-pair[1].score, pair[0].as_of_time))
    return scored[:top_n]


@dataclass(frozen=True)
class MemoryContext:
    """Pure evidence, never a decision input transformation. `outcome_
    distribution`: real counts only, from records whose own `status`
    is `STATUS_KNOWN` -- `NOT_YET_OBSERVED` records are counted
    separately and never treated as zero/negative evidence."""

    target_memory_id: str
    similar_count: int
    known_outcome_count: int
    not_yet_observed_count: int
    outcome_distribution: Dict[str, int]   # decision_state_after (or "regime_unchanged"/"regime_changed") -> count.
    similarities: Tuple[SimilarityExplanation, ...]


def build_memory_context(
    target: MarketMemoryRecord, index: MarketMemoryIndex, outcome_memories: Sequence[OutcomeMemoryRecord],
    *, as_of_time: str, top_n: int = 5,
) -> MemoryContext:
    """Composes ONLY already-real, already-stored records -- never
    recomputes a `MarketMemoryRecord`'s own regime/volatility/risk,
    never touches `bujji.mic_v0` or `bujji.strategy_intelligence` at
    all (structurally verified by this package's own safety test)."""
    similar = find_similar_conditions_as_of(target, index, as_of_time=as_of_time, top_n=top_n)
    similar_ids = {rec.memory_id for rec, _ in similar}

    outcomes_by_memory_id: Dict[str, List[OutcomeMemoryRecord]] = {}
    for o in outcome_memories:
        outcomes_by_memory_id.setdefault(o.memory_id, []).append(o)

    known_count = 0
    not_yet_count = 0
    distribution: Dict[str, int] = {}
    for memory_id in similar_ids:
        for outcome in outcomes_by_memory_id.get(memory_id, ()):
            if outcome.status == STATUS_KNOWN:
                known_count += 1
                bucket = outcome.decision_state_after or "UNKNOWN"
                distribution[bucket] = distribution.get(bucket, 0) + 1
            else:
                not_yet_count += 1

    return MemoryContext(
        target_memory_id=target.memory_id, similar_count=len(similar),
        known_outcome_count=known_count, not_yet_observed_count=not_yet_count,
        outcome_distribution=distribution, similarities=tuple(exp for _, exp in similar),
    )
