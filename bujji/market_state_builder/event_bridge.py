"""Event Bridge -- Shadow Campaign v2 Phase 3B.

Diffs the current cycle's PRICE Observation against the previous one,
via the EXISTING, unmodified
live_market_events.engine.compare_observations() -- no custom event
detection logic here.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.live_market_events.engine import compare_observations
from bujji.live_market_events.models import MarketEvent, RunningState
from bujji.market_observation.models import Observation


def detect_events(
    current: Optional[Observation], previous: Optional[Observation],
    running_state: Optional[RunningState] = None,
) -> Tuple[Tuple[MarketEvent, ...], Optional[RunningState]]:
    """Empty tuple + unchanged running_state (never fabricated) if
    current is None -- there is nothing to compare this cycle.
    First-cycle (previous is None) is handled natively by
    compare_observations() itself, per its own documented null-safety.

    running_state MUST be threaded across cycles by the caller (see
    ObservationMemory) -- session-high/low detection
    (NEW_SESSION_HIGH/NEW_SESSION_LOW) is otherwise silently reset to
    None every call, producing a spurious "new low" even on a rising
    price. Caught by this phase's own smoke test before any test file
    was written."""
    if current is None:
        return (), running_state
    events, updated_state = compare_observations(current, previous, running_state)
    return events, updated_state
