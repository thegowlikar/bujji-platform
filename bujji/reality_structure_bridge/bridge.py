"""Reality Structure Bridge — Phase 17G.A.

Feeds certified 5-minute Historical Reality (17H.9) through the
already-existing, already-tested `live_market_events` /
`market_episode` / `msi_price_structure` / `msi_market_structure`
engines, UNMODIFIED, per the design in
`docs/PHASE_17GA4_FIVE_MINUTE_STRUCTURE_BRIDGE_DESIGN.md`.

This module contributes ONLY: the iteration/pairing loop, one honesty
correction (detection_context), and an on-demand assessment query. It
reimplements none of the four packages' logic.

PURE, REPLAY-SAFE: no wall-clock read anywhere in this module -- every
timestamp comes from the `HistoricalObservation` records themselves.
Mirrors the Volatility Structure Bridge's (Series 88) own verified
discipline of never calling an impure `.analyze()`-style wrapper;
this module calls `engine.py` functions directly in all four packages,
never their `runner.py` journal/publish wrappers.

INV-8 (market_episode.engine): one instrument's stream per call
sequence, never interleaved -- enforced here by every public function
taking a single `instrument_identity`.
"""
from __future__ import annotations

import dataclasses
import datetime
from typing import List, Optional, Tuple

from bujji.historical_reality.store import HistoricalObservationStore
from bujji.live_market_events import config as _live_events_config
from bujji.live_market_events.engine import detect_price_change
from bujji.market_episode import config as _episode_config
from bujji.market_episode.engine import advance_time, process_event
from bujji.market_episode.models import Episode
from bujji.live_market_events.models import MarketEvent
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.msi_market_structure.engine import assess_market_structure
from bujji.msi_market_structure.models import MarketStructureAssessment
from bujji.msi_price_structure.engine import assess_price_structure
from bujji.msi_price_structure.models import PriceStructureAssessment

# First-pass default: one full trading session's worth of 5-min bars
# (matches `bujji.intelligence.volatility_brain.CANDLES_PER_DAY`'s own
# 09:15-15:30-session precedent). Explicitly a first-pass value, not a
# validated conclusion -- revisit after the mandatory validation run
# (design doc §7) confirms what window produces sensible structure
# reads, per this project's calibration-note convention elsewhere.
DEFAULT_LOOKBACK_BARS = 75

# Generous calendar-day buffer to guarantee >= DEFAULT_LOOKBACK_BARS
# real bars are fetched even across a long weekend/holiday cluster --
# trimmed down to the exact bar count after fetching, never assumed
# from the calendar-day count itself.
DEFAULT_LOOKBACK_CALENDAR_DAYS = 10

REPLAY_DETECTION_CONTEXT = _live_events_config.DEFAULT_DETECTION_CONTEXT_REPLAY


def _price_observations(
    historical_store: HistoricalObservationStore, instrument_identity: str,
    from_ts: str, to_ts: str,
):
    """Ordered (ascending timestamp) list of the canonical MOC
    `Observation` embedded in each 5-min `HistoricalObservation` --
    reused unchanged; `HistoricalObservationStore.range()` already
    returns rows in ascending timestamp order (17H.4)."""
    rows = historical_store.range(
        instrument_identity, moc_taxonomy.RESOLUTION_FIVE_MINUTE, from_ts, to_ts,
    )
    return [row.observation for row in rows]


def _replay_stamped(event: MarketEvent) -> MarketEvent:
    """Corrects `detect_price_change()`'s hardcoded LIVE detection
    context to REPLAY -- see design doc §4. Only the provenance
    metadata is touched; `event_id`/`detail`/`timestamp` are untouched,
    so this is honest re-labeling, never a re-derivation."""
    return dataclasses.replace(
        event, provenance=dataclasses.replace(
            event.provenance, detection_context=REPLAY_DETECTION_CONTEXT,
        ),
    )


def build_episodes_and_events(
    observations: List, *,
    quiescent_after_seconds: float = _episode_config.DEFAULT_QUIESCENT_AFTER_SECONDS,
    close_after_seconds: float = _episode_config.DEFAULT_CLOSE_AFTER_SECONDS,
    proximity_window_seconds: float = _episode_config.DEFAULT_PROXIMITY_WINDOW_SECONDS,
) -> Tuple[Tuple[Episode, ...], Tuple[MarketEvent, ...]]:
    """Builds the full episode/event set for one instrument's ordered
    observation sequence. Every window defaults to the EXISTING,
    UNMODIFIED `market_episode` constants -- design doc §5 confirms
    these already fit 5-minute spacing with zero changes; a caller
    wanting a different resolution must pass explicit overrides, never
    silently inherit these 5-min-tuned defaults.
    """
    episodes: Tuple[Episode, ...] = ()
    all_events: List[MarketEvent] = []

    for previous, current in zip(observations, observations[1:]):
        raw_events = detect_price_change(current, previous)
        for raw_event in raw_events:
            event = _replay_stamped(raw_event)
            all_events.append(event)
            episodes = process_event(
                episodes, event, detection_context=REPLAY_DETECTION_CONTEXT,
            )
        # advance_time is independent of whether this bar produced an
        # event (INV-9) -- always run, using the bar's OWN timestamp,
        # never wall-clock, so overnight/weekend gaps close episodes
        # using the unmodified defaults (design doc §5).
        episodes = advance_time(
            episodes, current.identity.timestamp,
            quiescent_after_seconds=quiescent_after_seconds,
            close_after_seconds=close_after_seconds,
            detection_context=REPLAY_DETECTION_CONTEXT,
        )

    return episodes, tuple(all_events)


def structure_as_of(
    instrument_identity: str, as_of_timestamp: str, *,
    historical_store: HistoricalObservationStore,
    lookback_bars: int = DEFAULT_LOOKBACK_BARS,
    lookback_calendar_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
) -> Tuple[Optional[PriceStructureAssessment], Optional[MarketStructureAssessment]]:
    """Rebuilds episodes/events from a trailing window ending at
    `as_of_timestamp` (never a future bar -- `_price_observations`
    only ever fetches `timestamp <= as_of_timestamp` observations, so
    this cannot leak the future into an assessment computed "as of" an
    earlier instant), then returns
    (PriceStructureAssessment, MarketStructureAssessment) computed as
    of that instant. Returns (None, None) if fewer than 2 observations
    exist in the window -- there is nothing to assess, and returning a
    placeholder assessment would fabricate evidence.

    On-demand only -- no new store, mirroring `RealityMemoryCatalog`'s
    (17J.1) own "recompute rather than cache" precedent.
    """
    as_of_dt = datetime.datetime.fromisoformat(as_of_timestamp)
    from_dt = as_of_dt - datetime.timedelta(days=lookback_calendar_days)
    observations = _price_observations(
        historical_store, instrument_identity, from_dt.isoformat(), as_of_timestamp,
    )
    observations = observations[-lookback_bars:] if lookback_bars else observations
    if len(observations) < 2:
        return None, None

    episodes, events = build_episodes_and_events(observations)
    if not episodes:
        return None, None

    price_assessment = assess_price_structure(
        episodes, events, timestamp=as_of_timestamp,
        provenance="reality_structure_bridge.structure_as_of (REPLAY, 5-min Historical Reality)",
    )
    market_assessment = assess_market_structure(
        episodes, events, timestamp=as_of_timestamp,
        provenance="reality_structure_bridge.structure_as_of (REPLAY, 5-min Historical Reality)",
    )
    return price_assessment, market_assessment
