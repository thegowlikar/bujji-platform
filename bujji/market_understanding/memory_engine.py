"""Market Memory recording + hydration -- Phase 19.5.

Built directly on `bujji.state_persistence.store.EventStore` (Phase
15B) -- the already-existing, fully generic append-only primitive. No
new persistence mechanism invented, per this phase's own audit finding.

Cross-session by design (like `outcome_memory`, Phase 15N): hydration
takes no `session_id` gate -- the entire point of durable market memory
is to span every session, not just the current one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Optional

from bujji.decision_context.models import DecisionContext
from bujji.intelligence.market_intelligence_snapshot.models import MarketIntelligenceSnapshot
from bujji.state_persistence.models import PersistedEvent
from bujji.state_persistence.store import EventStore

from .memory_models import (
    EVENT_MARKET_MEMORY_RECORDED,
    EVENT_MARKET_OUTCOME_OBSERVED,
    STATUS_KNOWN,
    MarketMemoryEntry,
    MarketOutcomeObservation,
    market_memory_id_for,
)


def build_market_memory_entry(
    *, snapshot: MarketIntelligenceSnapshot, decision_context: DecisionContext,
    underlying: str, recorded_at: datetime,
) -> MarketMemoryEntry:
    """Pure composition -- no IO. `decision_context` must have been built
    from the SAME `snapshot` (not checked here structurally, since
    `DecisionContext.intelligence_snapshot_reference` already carries
    that link and callers -- exactly like `runner.py`/`builder.py`
    upstream -- are expected to pass matching objects, the same
    trust boundary `build_market_intelligence_snapshot()` itself
    already relies on for its own brain-reading arguments)."""
    regime, structure, liquidity, volatility, event = (
        snapshot.regime, snapshot.structure, snapshot.liquidity, snapshot.volatility, snapshot.event,
    )
    return MarketMemoryEntry(
        market_memory_id=market_memory_id_for(snapshot.intelligence_snapshot_id, underlying),
        intelligence_snapshot_id=snapshot.intelligence_snapshot_id,
        intelligence_fingerprint=snapshot.fingerprint(),
        as_of_time=snapshot.as_of_time.isoformat(),
        underlying=underlying,
        recorded_at=recorded_at.isoformat(),
        regime_state=regime.regime.value if regime.regime else None,
        volatility_richness=volatility.richness.value if volatility.richness else None,
        structure_proximity=structure.proximity.value if structure.proximity else None,
        liquidity_tightness=liquidity.tightness.value if liquidity.tightness else None,
        event_expiry_proximity=event.expiry_proximity.value if event.expiry_proximity else None,
        event_vix_regime=event.vix_regime.value if event.vix_regime else None,
        posture=snapshot.posture.value,
        confidence=snapshot.evidence_bundle.confidence,
        compatible_strategy_families=decision_context.compatible_strategy_families,
        blocked_strategy_families=decision_context.blocked_strategy_families,
        intelligence_snapshot=snapshot.to_dict(),
        decision_context=decision_context.to_dict(),
    )


def record_market_memory(store: EventStore, entry: MarketMemoryEntry, *, recorded_at: datetime) -> None:
    """Appends ONE immutable fact. Two calls for the same
    (intelligence_snapshot_id, underlying) produce the SAME
    `market_memory_id` -- replaying the same event twice is idempotent
    by construction, never a duplicate memory."""
    event = PersistedEvent(
        event_id=entry.market_memory_id, event_type=EVENT_MARKET_MEMORY_RECORDED,
        session_id="cross-session", cycle_id=entry.as_of_time,
        timestamp=recorded_at.isoformat(), schema_version=entry.schema_version,
        provenance="market_understanding.memory_engine.record_market_memory",
        payload=entry.to_dict(),
    )
    store.append(event)


def record_outcome_observation(
    store: EventStore, market_memory_id: str, observation: MarketOutcomeObservation, *, recorded_at: datetime,
) -> None:
    """Appends a SEPARATE fact -- never edits the original
    MARKET_MEMORY_RECORDED event. `hydrate_market_memory()` applies this
    on replay via `MarketMemoryEntry.with_outcome_observation()`, the
    same "reconstruct via event replay through the same pure
    transition function live code uses" discipline Phase 15B
    established for every other component."""
    event = PersistedEvent(
        event_id=f"{market_memory_id}|OUTCOME|{observation.sessions_later}",
        event_type=EVENT_MARKET_OUTCOME_OBSERVED,
        session_id="cross-session", cycle_id=observation.observed_at,
        timestamp=recorded_at.isoformat(), schema_version="1.0.0",
        provenance="market_understanding.memory_engine.record_outcome_observation",
        payload={"market_memory_id": market_memory_id, **observation.to_dict()},
    )
    store.append(event)


def hydrate_market_memory(store: EventStore) -> Dict[str, MarketMemoryEntry]:
    """Replays every event in file order. A MARKET_OUTCOME_OBSERVED event
    for an id not yet seen (out-of-order write, or a torn/skipped
    MARKET_MEMORY_RECORDED line) is held and applied once its entry
    does appear later in the stream -- never silently dropped, never
    raised."""
    entries: Dict[str, MarketMemoryEntry] = {}
    pending_outcomes: Dict[str, List[MarketOutcomeObservation]] = {}

    for event in store.read_events():
        if event.event_type == EVENT_MARKET_MEMORY_RECORDED:
            entry = MarketMemoryEntry.from_dict(event.payload)
            for observation in pending_outcomes.pop(entry.market_memory_id, []):
                entry = entry.with_outcome_observation(observation)
            entries[entry.market_memory_id] = entry
        elif event.event_type == EVENT_MARKET_OUTCOME_OBSERVED:
            market_memory_id = event.payload["market_memory_id"]
            observation = MarketOutcomeObservation.from_dict(
                {**event.payload, "status": event.payload.get("status", STATUS_KNOWN)},
            )
            if market_memory_id in entries:
                entries[market_memory_id] = entries[market_memory_id].with_outcome_observation(observation)
            else:
                pending_outcomes.setdefault(market_memory_id, []).append(observation)

    return entries
