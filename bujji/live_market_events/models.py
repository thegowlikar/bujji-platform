"""Live Market Event Engine models — frozen, immutable records.

Implements Deliverable 4 (`MarketEvent`) plus the minimal running-state
carrier (`SessionExtremesState`) needed for NEW_SESSION_HIGH/LOW
detection (see `taxonomy.py` module docstring and `engine.py` for the
full design-tension writeup).

Every dataclass here is `frozen=True` and carries no logic --
construction lives in `engine.py`, never here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


# ---------------------------------------------------------------------------
# MarketEventProvenance — mirrors ObservationProvenance's shape (per the
# spec's explicit instruction), applied at the event-detection layer
# rather than the observation-capture layer.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketEventProvenance:
    originating_source: str        # e.g. "live_market_events.engine" or the caller-supplied detector name.
    detection_context: str         # e.g. "LIVE", "REPLAY", "BATCH" — how the comparison was run.
    schema_version: str


# ---------------------------------------------------------------------------
# MarketEvent — the canonical unit (Deliverable 4). References the
# originating Observation(s) by their existing observation_id — never
# copies an Observation's payload. `detail` carries only the minimal
# fact-of-change payload for this event_type (e.g. old/new value,
# delta) — never a full copy of either observation's other fields.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class MarketEvent:
    event_id: str
    event_type: str                                # One of taxonomy.ALL_MARKET_EVENT_TYPES.
    timestamp: str                                  # The (current) observation's own timestamp — never detection time.
    originating_observation_ids: Tuple[str, ...]    # (current_id,) or (current_id, previous_id).
    detail: Mapping[str, Any]                       # Minimal fact-of-change payload — never a full Observation copy.
    provenance: MarketEventProvenance
    schema_version: str


# ---------------------------------------------------------------------------
# SessionExtremesState — the minimal running fact NEW_SESSION_HIGH/LOW
# detection needs: a running max/min carried forward, per
# (observation_type, instrument). Not a history replay — a single
# carried-forward fact, mirroring Series 74's AggregationWindow
# precedent (see taxonomy.py module docstring for the full reasoning).
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SessionExtremesState:
    observation_type: str
    instrument: str
    session_high: Optional[float]
    session_high_observation_id: Optional[str]
    session_low: Optional[float]
    session_low_observation_id: Optional[str]


def initial_session_extremes(observation_type: str, instrument: str) -> SessionExtremesState:
    """Construct an empty SessionExtremesState — no observations seen yet."""
    return SessionExtremesState(
        observation_type=observation_type,
        instrument=instrument,
        session_high=None,
        session_high_observation_id=None,
        session_low=None,
        session_low_observation_id=None,
    )


# ---------------------------------------------------------------------------
# RunningState — bundles the (currently only) session-extremes state
# with room to grow, per (observation_type, instrument), without
# forcing every caller to track a bare SessionExtremesState directly.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RunningState:
    session_extremes: SessionExtremesState
