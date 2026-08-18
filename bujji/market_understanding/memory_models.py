"""Market Understanding Memory -- Phase 19.5. Pure models, no IO, no
broker, no execution, no strategy-selection import, no prediction.

Architecture: OBSERVATION, never INFERENCE. `MarketMemoryEntry` records
"what the market's own intelligence looked like at this moment" and,
separately and only once real time has actually passed,
"what the market's own intelligence looked like N sessions later" --
never "what will happen" or "what to do."

Identity reuse (per this phase's own explicit instruction -- do NOT
introduce another snapshot_id):
- `intelligence_snapshot_id` is `MarketIntelligenceSnapshot.intelligence_snapshot_id`
  verbatim (Phase 19.3).
- `intelligence_fingerprint` is the SAME value (Phase 19.3's own
  `.fingerprint()` always equals `.intelligence_snapshot_id` by
  construction) -- kept as an explicit second field only because the
  Phase 19.5 spec names it separately, not because it is a second,
  independently-computed hash. Documented here rather than silently
  duplicating a value under a different name without explanation.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

SCHEMA_VERSION = "1.0.0"

EVENT_MARKET_MEMORY_RECORDED = "MARKET_MEMORY_RECORDED"
EVENT_MARKET_OUTCOME_OBSERVED = "MARKET_OUTCOME_OBSERVED"

STATUS_KNOWN = "KNOWN"           # a real outcome observation exists.
STATUS_NOT_YET_OBSERVED = "NOT_YET_OBSERVED"  # honest absence -- not enough time has passed yet.


def market_memory_id_for(intelligence_snapshot_id: str, underlying: str) -> str:
    """Deterministic, collision-resistant, replay-safe -- same MD5-based
    convention as `outcome_memory.models.memory_id_for()` (Phase 15N),
    reused here rather than reinvented. Two records for the same
    snapshot + underlying are the SAME memory record, by design."""
    return "MKTMEM-" + hashlib.md5(f"{intelligence_snapshot_id}|{underlying}".encode()).hexdigest()[:24]


@dataclass(frozen=True)
class MarketOutcomeObservation:
    """A FACTUAL record of what the market's own intelligence looked like
    some real number of sessions after a `MarketMemoryEntry` was
    recorded -- never a prediction, never converted into a
    recommendation. `status` is `NOT_YET_OBSERVED` until a real later
    snapshot is actually supplied; never guessed or interpolated."""

    sessions_later: int
    observed_at: str                        # the LATER snapshot's own as_of_time, isoformat.
    regime_after: Optional[str]
    volatility_richness_after: Optional[str]
    posture_after: Optional[str]
    status: str = STATUS_NOT_YET_OBSERVED

    def to_dict(self) -> dict:
        return {
            "sessions_later": self.sessions_later, "observed_at": self.observed_at,
            "regime_after": self.regime_after, "volatility_richness_after": self.volatility_richness_after,
            "posture_after": self.posture_after, "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "MarketOutcomeObservation":
        return MarketOutcomeObservation(
            sessions_later=d["sessions_later"], observed_at=d["observed_at"],
            regime_after=d.get("regime_after"), volatility_richness_after=d.get("volatility_richness_after"),
            posture_after=d.get("posture_after"), status=d.get("status", STATUS_KNOWN),
        )


@dataclass(frozen=True)
class MarketMemoryEntry:
    """One immutable historical fact: what Bujji's Intelligence Core
    concluded at one real moment, plus (once real time has passed) what
    it concluded later. `intelligence_snapshot` / `decision_context` are
    embedded VERBATIM `to_dict()` output -- the single source of truth
    for every underlying field, same "never re-derive, embed verbatim"
    discipline `outcome_memory.OutcomeMemoryRecord` already established.
    The top-level fields below exist purely as QUERY CONVENIENCE (fast
    filtering/comparison without re-parsing nested dicts), never as a
    second, competing copy of the truth.
    """

    market_memory_id: str
    intelligence_snapshot_id: str
    intelligence_fingerprint: str  # == intelligence_snapshot_id, see module docstring.
    as_of_time: str                # isoformat -- the snapshot's own as_of_time, never wall-clock.
    underlying: str
    recorded_at: str               # isoformat -- when this memory entry was recorded, audit metadata only.

    # --- Query-convenience extracts (verbatim copies of Reading fields, never re-derived) ---
    regime_state: Optional[str]
    volatility_richness: Optional[str]
    structure_proximity: Optional[str]
    liquidity_tightness: Optional[str]
    event_expiry_proximity: Optional[str]
    event_vix_regime: Optional[str]
    posture: str
    confidence: float

    compatible_strategy_families: Tuple[str, ...]
    blocked_strategy_families: Tuple[str, ...]

    # --- Full verbatim snapshots -- the actual source of truth. -----------
    intelligence_snapshot: Dict[str, Any]
    decision_context: Dict[str, Any]

    outcome_observation: Optional[MarketOutcomeObservation] = None

    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "market_memory_id": self.market_memory_id,
            "intelligence_snapshot_id": self.intelligence_snapshot_id,
            "intelligence_fingerprint": self.intelligence_fingerprint,
            "as_of_time": self.as_of_time, "underlying": self.underlying, "recorded_at": self.recorded_at,
            "regime_state": self.regime_state, "volatility_richness": self.volatility_richness,
            "structure_proximity": self.structure_proximity, "liquidity_tightness": self.liquidity_tightness,
            "event_expiry_proximity": self.event_expiry_proximity, "event_vix_regime": self.event_vix_regime,
            "posture": self.posture, "confidence": self.confidence,
            "compatible_strategy_families": list(self.compatible_strategy_families),
            "blocked_strategy_families": list(self.blocked_strategy_families),
            "intelligence_snapshot": self.intelligence_snapshot, "decision_context": self.decision_context,
            "outcome_observation": self.outcome_observation.to_dict() if self.outcome_observation else None,
            "schema_version": self.schema_version,
        }

    @staticmethod
    def from_dict(d: dict) -> "MarketMemoryEntry":
        outcome = d.get("outcome_observation")
        return MarketMemoryEntry(
            market_memory_id=d["market_memory_id"], intelligence_snapshot_id=d["intelligence_snapshot_id"],
            intelligence_fingerprint=d["intelligence_fingerprint"], as_of_time=d["as_of_time"],
            underlying=d["underlying"], recorded_at=d["recorded_at"],
            regime_state=d.get("regime_state"), volatility_richness=d.get("volatility_richness"),
            structure_proximity=d.get("structure_proximity"), liquidity_tightness=d.get("liquidity_tightness"),
            event_expiry_proximity=d.get("event_expiry_proximity"), event_vix_regime=d.get("event_vix_regime"),
            posture=d["posture"], confidence=d["confidence"],
            compatible_strategy_families=tuple(d.get("compatible_strategy_families", ())),
            blocked_strategy_families=tuple(d.get("blocked_strategy_families", ())),
            intelligence_snapshot=d.get("intelligence_snapshot", {}), decision_context=d.get("decision_context", {}),
            outcome_observation=MarketOutcomeObservation.from_dict(outcome) if outcome else None,
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )

    def with_outcome_observation(self, observation: MarketOutcomeObservation) -> "MarketMemoryEntry":
        """Never mutates in place -- an outcome observation is a NEW fact
        recorded once real time has passed, applied here by returning a
        new, otherwise-identical entry. The store itself persists this
        as a SEPARATE append-only event (see `memory_store.py`), never an
        edit to the original record -- matching this project's "no
        mutable historical facts" discipline exactly."""
        from dataclasses import replace
        return replace(self, outcome_observation=observation)
