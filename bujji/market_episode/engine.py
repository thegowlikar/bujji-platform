"""Market Episode Engine — pure functions, no state, no IO.

Implements Deliverable 4/5: given a new `MarketEvent` (Series 75) and
the current set of open (non-CLOSED) `Episode`s, determine whether the
event joins an existing episode (extend -> new snapshot, same
episode_id), creates a new episode, or is a no-op duplicate. Also
implements the independent, time-based lifecycle-transition trigger
(`advance_time`), which can fire even with NO new event.

Nothing here judges market meaning. Every function answers only
"do these changes belong to the same structural sequence" and "has
this sequence gone quiet / ended" -- never "what does this sequence
mean."

---------------------------------------------------------------------
STEP 0 INVARIANTS (derived before any grouping code was written; each
is enforced by this module, not just documented, and each has a
dedicated test in tests/test_market_episode_engine.py):
---------------------------------------------------------------------
INV-1  An Event belongs to at most one OPEN (non-CLOSED) Episode at a
       time. `process_event` only ever attaches a given event_id to
       ONE episode per call: once a compatible episode is chosen (see
       INV-5 for tie-break), the event is not additionally offered to
       any other episode in the same call. A CLOSED episode is never a
       candidate (see INV-4), so an event cannot "belong" to a closed
       episode either.
       Test: test_event_belongs_to_at_most_one_active_episode.

INV-2  Episodes never merge retroactively. Two independently-created
       Episodes never later become one (no "combine two episode_ids
       into one" operation exists anywhere in this module). If a new
       event would, in isolation, be compatible with more than one
       open episode, it joins exactly one of them (INV-5's tie-break)
       -- it never causes the two candidate episodes themselves to
       merge.
       Test: test_episodes_never_merge_retroactively.

INV-3  Closing an Episode never changes its prior contents. Growth and
       closure both produce a NEW, immutable `Episode` snapshot; nothing
       in this module mutates a previously-returned `Episode` object,
       and every snapshot's `originating_event_ids`/
       `originating_observation_ids` are a superset of the prior
       snapshot's (append-only growth, mirroring Series 75/74's
       append-only journal precedent).
       Test: test_closing_never_changes_prior_contents.

INV-4  A CLOSED episode never reopens. `taxonomy.VALID_TRANSITIONS[
       STATE_CLOSED] == ()` -- there is no CLOSED -> anything edge, and
       `_open_candidates` below filters out CLOSED episodes before any
       compatibility check runs, so a closed episode is structurally
       unreachable as a join target. A subsequent compatible event
       always creates a brand NEW Episode (new episode_id) instead.
       Test: test_closed_episode_never_reopens.

INV-5  Deterministic multi-match tie-break. If an incoming event is
       compatible with more than one open episode, it joins the one
       with the most recent `latest_update` (i.e. the most recently
       active episode) -- never an arbitrary/first-found one. Ties in
       `latest_update` itself are broken by lexicographically-smallest
       `episode_id`, so the choice is fully deterministic and
       reproducible under replay.
       Test: test_deterministic_join_tiebreak.

INV-6  episode_id is fixed at creation and never recomputed on growth.
       See models.py's module docstring for the full design rationale.
       Test: test_deterministic_episode_creation,
       test_deterministic_extension_same_episode_id.

INV-7  Duplicate-event idempotency. Processing the SAME MarketEvent
       (same event_id) against the same episode set twice never
       creates a second episode and never double-counts membership --
       the second call is a no-op (returns the episodes unchanged).
       Test: test_duplicate_event_is_idempotent.

INV-8  Episodes are scoped to a single instrument stream by
       construction, never by a stored/compared field. `MarketEvent`
       (Series 75) does not uniformly carry `instrument` in `detail`
       (only OBSERVATION_CREATED does), so "same instrument" -- a
       compatibility criterion this sprint's spec calls for -- cannot
       be recovered generically from an arbitrary MarketEvent. This is
       resolved the same way Series 75's own runner scopes work: one
       `ObservationSeries` is single-instrument by definition (Series
       73A), and `generate_events_for_series`/`LiveMarketEventStream`
       both process one series (one instrument) end-to-end. This
       engine adopts the identical scope: a caller processes one
       instrument's event stream through one `process_event`/
       `advance_time` sequence at a time, so "same instrument" is true
       by construction, not by a runtime field comparison. Cross-
       instrument grouping is structurally impossible: the engine
       never receives events from more than one instrument in a single
       call sequence to begin with.
       Documented here rather than test-proven directly (there is no
       field to assert on); implicitly proven by every test in this
       suite operating on one instrument's stream and never
       cross-contaminating episodes across the parity/demo tests.

INV-9  Time-based transitions are independent of event-driven growth.
       `advance_time(episodes, current_time)` never reads or requires
       a new MarketEvent, and `process_event` never performs a
       silence-duration check -- the two triggers are separate
       functions with no shared mutable state, so a caller can run
       either independently (e.g. a scheduler ticking `advance_time`
       once a minute regardless of event traffic).
       Test: test_advance_time_independent_of_process_event.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import List, Optional, Tuple

from bujji.live_market_events.models import MarketEvent

from . import config as _config
from . import taxonomy
from .models import Episode, EpisodeProvenance


# ---------------------------------------------------------------------------
# episode_id / Episode construction
# ---------------------------------------------------------------------------
def _episode_id(episode_type: str, founding_event_id: str) -> str:
    """Deterministic content hash over (episode_type, founding
    event_id) ONLY -- minted once, at creation, never recomputed as the
    episode grows. See models.py's module docstring and INV-6."""
    seed = "|".join([episode_type, founding_event_id])
    return "EPS-" + hashlib.md5(seed.encode()).hexdigest()[:24]


def _parse_ts(timestamp: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(timestamp)
    except (ValueError, TypeError):
        return None


def _episode_type_for_event(event: MarketEvent) -> Optional[str]:
    return taxonomy._EVENT_TYPE_TO_EPISODE_TYPE.get(event.event_type)


def _make_provenance(
    *,
    originating_source: str = _config.DEFAULT_ORIGINATING_SOURCE,
    detection_context: str = _config.DEFAULT_DETECTION_CONTEXT_LIVE,
    schema_version: str = _config.SCHEMA_VERSION,
) -> EpisodeProvenance:
    return EpisodeProvenance(
        originating_source=originating_source,
        detection_context=detection_context,
        schema_version=schema_version,
    )


def _create_episode(event: MarketEvent, episode_type: str, *, detection_context: str) -> Episode:
    episode_id = _episode_id(episode_type, event.event_id)
    return Episode(
        episode_id=episode_id,
        episode_type=episode_type,
        start_time=event.timestamp,
        latest_update=event.timestamp,
        end_time=None,
        originating_event_ids=(event.event_id,),
        originating_observation_ids=tuple(event.originating_observation_ids),
        current_state=taxonomy.STATE_ACTIVE,
        provenance=_make_provenance(detection_context=detection_context),
        schema_version=_config.SCHEMA_VERSION,
    )


def _grow_episode(episode: Episode, event: MarketEvent, *, detection_context: str) -> Episode:
    """Produce a NEW Episode snapshot with the SAME episode_id, a
    superset event/observation id tuple, updated latest_update, and
    (per resolved ambiguity in the module docstring) episode_type
    upgraded to MULTI_FACTOR_EPISODE if the joining event belongs to a
    different structural family than the episode's current type.
    Reactivates QUIESCENT -> ACTIVE (see taxonomy.py)."""
    joining_type = _episode_type_for_event(event)
    new_type = episode.episode_type
    if joining_type is not None and joining_type != episode.episode_type:
        new_type = taxonomy.MULTI_FACTOR_EPISODE

    new_event_ids = episode.originating_event_ids
    if event.event_id not in new_event_ids:
        new_event_ids = new_event_ids + (event.event_id,)

    new_observation_ids = list(episode.originating_observation_ids)
    for oid in event.originating_observation_ids:
        if oid not in new_observation_ids:
            new_observation_ids.append(oid)

    new_state = episode.current_state
    if episode.current_state == taxonomy.STATE_QUIESCENT:
        assert taxonomy.is_valid_transition(taxonomy.STATE_QUIESCENT, taxonomy.STATE_ACTIVE)
        new_state = taxonomy.STATE_ACTIVE
    elif episode.current_state == taxonomy.STATE_ACTIVE:
        assert taxonomy.is_valid_transition(taxonomy.STATE_ACTIVE, taxonomy.STATE_ACTIVE)
        new_state = taxonomy.STATE_ACTIVE

    return Episode(
        episode_id=episode.episode_id,
        episode_type=new_type,
        start_time=episode.start_time,
        latest_update=event.timestamp,
        end_time=episode.end_time,
        originating_event_ids=new_event_ids,
        originating_observation_ids=tuple(new_observation_ids),
        current_state=new_state,
        provenance=_make_provenance(detection_context=detection_context),
        schema_version=episode.schema_version,
    )


# ---------------------------------------------------------------------------
# Compatibility (Deliverable 5) — a clearly separate, named function.
# Checks ONLY: episode openness, event-type/episode-forming eligibility,
# timestamp proximity. Instrument identity is structural (INV-8), never
# a field comparison. Never anything resembling market meaning.
# ---------------------------------------------------------------------------
def is_compatible(
    event: MarketEvent,
    episode: Episode,
    *,
    proximity_window_seconds: float = _config.DEFAULT_PROXIMITY_WINDOW_SECONDS,
) -> bool:
    if episode.current_state not in taxonomy.OPEN_STATES:
        return False  # INV-4: CLOSED is never a candidate.
    if event.event_type not in taxonomy.EPISODE_FORMING_EVENT_TYPES:
        return False
    if event.event_id in episode.originating_event_ids:
        return False  # INV-7: already a member, not a fresh join.

    event_ts = _parse_ts(event.timestamp)
    episode_ts = _parse_ts(episode.latest_update)
    if event_ts is None or episode_ts is None:
        return False
    delta = abs((event_ts - episode_ts).total_seconds())
    return delta <= proximity_window_seconds


def _open_candidates(episodes: Tuple[Episode, ...]) -> Tuple[Episode, ...]:
    return tuple(e for e in episodes if e.current_state in taxonomy.OPEN_STATES)


def _select_join_target(event: MarketEvent, episodes: Tuple[Episode, ...]) -> Optional[Episode]:
    """INV-5: deterministic tie-break among all compatible open
    episodes -- most recently active (max latest_update); ties broken
    by lexicographically-smallest episode_id."""
    candidates = [e for e in _open_candidates(episodes) if is_compatible(event, e)]
    if not candidates:
        return None
    candidates.sort(key=lambda e: (e.latest_update, e.episode_id))
    return candidates[-1]


# ---------------------------------------------------------------------------
# Deliverable 4 — process_event: event-driven growth/creation. Never
# performs a time-based (silence-duration) transition check — see
# INV-9 and advance_time below.
# ---------------------------------------------------------------------------
def process_event(
    episodes: Tuple[Episode, ...],
    event: MarketEvent,
    *,
    detection_context: str = _config.DEFAULT_DETECTION_CONTEXT_LIVE,
) -> Tuple[Episode, ...]:
    """Returns the updated tuple of episodes: either the same tuple
    (duplicate/no-op or non-episode-forming event), the tuple with one
    episode replaced by its grown snapshot, or the tuple with one new
    episode appended. Never mutates an existing Episode object."""
    if event.event_type not in taxonomy.EPISODE_FORMING_EVENT_TYPES:
        return episodes  # Not an episode-forming factual family (e.g. lifecycle/gap/duplicate events).

    # INV-7: idempotency — if this exact event already belongs to ANY
    # episode (open or closed), this call is a no-op.
    for existing in episodes:
        if event.event_id in existing.originating_event_ids:
            return episodes

    target = _select_join_target(event, episodes)
    if target is not None:
        grown = _grow_episode(target, event, detection_context=detection_context)
        return tuple(grown if e is target else e for e in episodes)

    episode_type = _episode_type_for_event(event)
    assert episode_type is not None  # guaranteed by the EPISODE_FORMING_EVENT_TYPES check above.
    new_episode = _create_episode(event, episode_type, detection_context=detection_context)
    return episodes + (new_episode,)


# ---------------------------------------------------------------------------
# Deliverable 4 — advance_time: time-based lifecycle transitions,
# independent of any new event (INV-9).
# ---------------------------------------------------------------------------
def advance_time(
    episodes: Tuple[Episode, ...],
    current_time: str,
    *,
    quiescent_after_seconds: float = _config.DEFAULT_QUIESCENT_AFTER_SECONDS,
    close_after_seconds: float = _config.DEFAULT_CLOSE_AFTER_SECONDS,
    detection_context: str = _config.DEFAULT_DETECTION_CONTEXT_LIVE,
) -> Tuple[Episode, ...]:
    """Recompute ACTIVE->QUIESCENT and QUIESCENT->CLOSED transitions
    for every open episode, purely from elapsed silence since
    `latest_update` vs. `current_time`. Idempotent: calling this
    repeatedly with the same `current_time` (or an earlier one) never
    produces a different result than calling it once at the latest
    `current_time` reached so far, and never touches a CLOSED
    episode's prior contents (INV-3/INV-4)."""
    now = _parse_ts(current_time)
    if now is None:
        return episodes

    updated: List[Episode] = []
    for episode in episodes:
        if episode.current_state not in taxonomy.OPEN_STATES:
            updated.append(episode)
            continue

        last = _parse_ts(episode.latest_update)
        if last is None:
            updated.append(episode)
            continue
        silence = (now - last).total_seconds()
        if silence < 0:
            updated.append(episode)  # current_time is before latest_update — nothing to advance.
            continue

        new_state = episode.current_state
        new_end_time = episode.end_time
        close_threshold = quiescent_after_seconds + close_after_seconds

        if episode.current_state == taxonomy.STATE_ACTIVE:
            if silence >= close_threshold:
                # A single large time jump can skip visibly sitting in
                # QUIESCENT — ACTIVE -> CLOSED is itself a valid table
                # entry (see taxonomy.VALID_TRANSITIONS), so this is
                # not a forbidden shortcut, just a coarser tick.
                new_state = taxonomy.STATE_CLOSED
                new_end_time = current_time
            elif silence >= quiescent_after_seconds:
                new_state = taxonomy.STATE_QUIESCENT
        elif episode.current_state == taxonomy.STATE_QUIESCENT and silence >= close_threshold:
            new_state = taxonomy.STATE_CLOSED
            new_end_time = current_time
        # `silence` is always measured from latest_update (which never
        # changes on a pure state transition, only on growth), so
        # repeated advance_time calls at increasing current_time values
        # are idempotent and monotonic regardless of tick granularity.

        if new_state == episode.current_state:
            updated.append(episode)
            continue

        assert taxonomy.is_valid_transition(episode.current_state, new_state), (
            f"illegal transition {episode.current_state} -> {new_state}"
        )
        updated.append(
            Episode(
                episode_id=episode.episode_id,
                episode_type=episode.episode_type,
                start_time=episode.start_time,
                latest_update=episode.latest_update,
                end_time=new_end_time,
                originating_event_ids=episode.originating_event_ids,
                originating_observation_ids=episode.originating_observation_ids,
                current_state=new_state,
                provenance=_make_provenance(detection_context=detection_context),
                schema_version=episode.schema_version,
            )
        )

    return tuple(updated)
