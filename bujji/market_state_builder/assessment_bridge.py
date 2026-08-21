"""Assessment Bridge -- Shadow Campaign v2 Phase 3B, updated Phase 10
(Market Memory Integrity Upgrade).

Wires real Episode/MarketEvent/OptionObservation state into
msi_market_structure, msi_price_structure, and
msi_participant_positioning -- calling each EXACTLY as its own
signature requires, none of them modified.

price_structure/market_structure are only computed when there is at
least one episode or event to reason about -- calling
assess_price_structure/assess_market_structure with genuinely nothing
(no episodes, no events) would not be a "None" input case those
functions themselves special-case; skipping the call here (leaving the
field None) is this bridge's own honest "insufficient data" signal,
never a fabricated empty-input assessment. participant_positioning is
likewise None (not called) when there is no option chain this cycle.

PHASE 10 FIX -- root cause confirmed by tracing the real code path
(not assumed): `episode.originating_event_ids` accumulates across the
episode's whole life, but `assess_price_structure`/`assess_market_structure`
resolve those ids by building `events_by_id = {e.event_id: e for e in
events}` from whatever `events` tuple THIS call was given. Every prior
caller (`MarketStateBuilder.process()`) passed only the CURRENT
cycle's fresh event delta as `events` -- so nearly every historical
`event_id` a long-lived episode remembers resolves to nothing and is
silently dropped (`if event is None: continue` inside
`_price_events_in_order`, identical in both PSI and MSSI). This is a
lookup-table scope mismatch, not a threshold, not a dedup bug, and not
genuine market flatness.

FIX: a new optional `event_history` parameter. When a caller supplies
the FULL accumulated event history (not just this cycle's delta), it
is unioned with `events` and used for PSI/MSSI's own lookups --
`events` itself is left untouched as the field stored on
`MarketStateAssessment.events` (its established per-cycle-delta
meaning, already consumed by `market_state.synthesizer.build_market_state`'s
`active_events` telemetry, is preserved for backward compatibility).
Omitting `event_history` reproduces the exact pre-Phase-10 behavior --
existing callers/tests that only pass `events` are unaffected.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.live_market_events.models import MarketEvent
from bujji.market_episode.models import Episode
from bujji.msi_market_structure.engine import assess_market_structure
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_participant_positioning.engine import assess_participant_positioning
from bujji.msi_participant_positioning.models import MarketParticipantPositioningAssessment
from bujji.msi_price_structure.engine import assess_price_structure
from bujji.msi_price_structure.models import PriceStructureAssessment
from bujji.options_observation.models import OptionObservation


@dataclass(frozen=True)
class MarketStateAssessment:
    """Contains ONLY market-understanding fields -- no strategy,
    decision, signal, or risk field exists here or ever will."""

    price_structure: Optional[PriceStructureAssessment]
    market_structure: Optional[MarketStructureAssessment]
    participant_positioning: Optional[MarketParticipantPositioningAssessment]
    events: Tuple[MarketEvent, ...]
    episodes: Tuple[Episode, ...]
    # Raw observed basis (futures - spot) for this cycle and the previous one.
    # Carried as NUMBERS, not a classification: PREMIUM/DISCOUNT labelling is
    # the interpretive layer's job (see futures_observation.compute_basis's own
    # docstring), and the direction lens that consumes these does the
    # interpreting. Both None until a futures snapshot is available -- absent,
    # never defaulted to 0.0, which would read as "no change" rather than
    # "no data".
    futures_basis: Optional[float] = None
    previous_futures_basis: Optional[float] = None


def _union_events(events: Tuple[MarketEvent, ...], event_history: Optional[Tuple[MarketEvent, ...]]) -> Tuple[MarketEvent, ...]:
    """Union by event_id, `events` first so a same-id conflict (should
    never happen -- event_ids are content-hashed) prefers the fresher
    copy. Order otherwise irrelevant: PSI/MSSI both build a dict keyed
    by event_id from whatever tuple they're given."""
    if event_history is None:
        return events
    seen = {e.event_id for e in events}
    merged = list(events)
    for e in event_history:
        if e.event_id not in seen:
            merged.append(e)
            seen.add(e.event_id)
    return tuple(merged)


def build_market_state_assessment(
    episodes: Tuple[Episode, ...], events: Tuple[MarketEvent, ...],
    option_observations: Tuple[OptionObservation, ...], timestamp: str,
    event_history: Optional[Tuple[MarketEvent, ...]] = None,
    previous_option_observations: Optional[Tuple[OptionObservation, ...]] = None,
    futures_basis: Optional[float] = None,
    previous_futures_basis: Optional[float] = None,
) -> MarketStateAssessment:
    price_structure = None
    market_structure = None
    participant_positioning = None

    lookup_events = _union_events(events, event_history)

    if episodes or lookup_events:
        price_structure = assess_price_structure(episodes, lookup_events, timestamp=timestamp)
        market_structure = assess_market_structure(episodes, lookup_events, timestamp=timestamp)

    if option_observations:
        # PREVIOUS CHAIN NOW SUPPLIED (2026-08-20). It never was, so two of
        # MPPI's five lenses -- OI migration and OI expansion/contraction --
        # returned UNKNOWN on every cycle Bujji has ever run. Their own
        # docstrings explain why: "no intraday OI history exists in this
        # codebase (Bhavcopy is end-of-day only)". That was true when they
        # were written and is not true now -- the live chain capture writes
        # ~199,000 rows a day. Positioning was being decided by 3 of 5 lenses.
        participant_positioning = assess_participant_positioning(
            option_observations, previous_option_observations, timestamp=timestamp,
        )

    return MarketStateAssessment(
        price_structure=price_structure, market_structure=market_structure,
        participant_positioning=participant_positioning, events=events, episodes=episodes,
    )
