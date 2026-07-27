"""Tests for BUJJI Engineering Series 76 — Market Episode Engine v1.

Covers: deterministic creation, deterministic extension, deterministic
closure, serialization round-trip, replay/live parity, append-only
journal recording (structural), duplicate-event idempotency, each Step
0 invariant (one test per invariant, named after it), AST isolation,
and the Deliverable 10 cross-series demonstration (real Observations
-> real 73A -> real 75 engine.compare_observations -> real MarketEvents
-> this engine), run twice for byte-identical results.
"""
from __future__ import annotations

import ast
import glob
import os
import tempfile

import pytest

from bujji.live_market_events import engine as lme_engine
from bujji.live_market_events.models import MarketEvent, MarketEventProvenance
from bujji.market_episode import config as mee_config
from bujji.market_episode import engine as mee_engine
from bujji.market_episode import journal as mee_journal
from bujji.market_episode import query as mee_query
from bujji.market_episode import runner as mee_runner
from bujji.market_episode import serialization as mee_serialization
from bujji.market_episode import taxonomy as mee_taxonomy
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _mk_event(event_type, timestamp, obs_ids=("OBS-a",), detail=None, event_id_suffix=""):
    detail = detail or {"old_price": 100.0, "new_price": 101.0, "delta": 1.0}
    provenance = MarketEventProvenance(
        originating_source="test", detection_context="LIVE", schema_version="1.0.0"
    )
    # Use the real engine's id function indirectly by constructing
    # through _make_event-equivalent hashing so ids are realistic and
    # unique per call-site; simplest is to vary detail/timestamp per
    # caller, which we do.
    import hashlib
    import json

    canonical_detail = json.dumps(detail, sort_keys=True, default=repr)
    seed = "|".join([event_type, *obs_ids, canonical_detail, timestamp, event_id_suffix])
    event_id = "MEVT-" + hashlib.md5(seed.encode()).hexdigest()[:24]
    return MarketEvent(
        event_id=event_id,
        event_type=event_type,
        timestamp=timestamp,
        originating_observation_ids=tuple(obs_ids),
        detail=dict(detail),
        provenance=provenance,
        schema_version="1.0.0",
    )


def _mk_observation(obs_type, instrument, timestamp, price):
    return moc_engine.build_observation(
        observation_type=obs_type,
        instrument=instrument,
        exchange="NSE",
        segment="EQ",
        timestamp=timestamp,
        resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE,
        source="TEST_SOURCE",
        schema_version="1.0.0",
        value_kind=moc_taxonomy.VALUE_KIND_SCALAR,
        payload=price,
        completeness=1.0,
        freshness=0.0,
        confidence=1.0,
        missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID,
        source_quality="HIGH",
        originating_source="TEST_SOURCE",
        acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp,
        origin=moc_taxonomy.ORIGIN_LIVE,
        provenance_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Deterministic creation / extension / closure
# ---------------------------------------------------------------------------
def test_deterministic_episode_creation():
    ev = _mk_event(mee_taxonomy._EVENT_TYPE_TO_EPISODE_TYPE and "PRICE_CHANGED", "2026-07-24T09:15:00")
    ep1 = mee_engine.process_event((), ev)
    ep2 = mee_engine.process_event((), ev)
    assert len(ep1) == 1 and len(ep2) == 1
    assert ep1[0].episode_id == ep2[0].episode_id
    assert ep1[0].episode_type == mee_taxonomy.PRICE_MOVEMENT_EPISODE
    assert ep1[0].current_state == mee_taxonomy.STATE_ACTIVE


def test_deterministic_extension_same_episode_id():
    ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"delta": 1})
    ev2 = _mk_event("VOLUME_CHANGED", "2026-07-24T09:16:00", detail={"delta": 2})
    episodes = mee_engine.process_event((), ev1)
    original_id = episodes[0].episode_id
    episodes2 = mee_engine.process_event(episodes, ev2)
    assert len(episodes2) == 1
    grown = episodes2[0]
    assert grown.episode_id == original_id
    assert set(episodes[0].originating_event_ids).issubset(set(grown.originating_event_ids))
    assert ev2.event_id in grown.originating_event_ids
    assert grown.episode_type == mee_taxonomy.MULTI_FACTOR_EPISODE


def test_deterministic_closure():
    ev = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
    episodes = mee_engine.process_event((), ev)
    later = "2026-07-24T10:30:00"  # well past quiescent+close default window
    closed = mee_engine.advance_time(episodes, later)
    assert closed[0].current_state == mee_taxonomy.STATE_CLOSED
    assert closed[0].end_time == later
    assert closed[0].episode_id == episodes[0].episode_id


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
def test_serialization_round_trip():
    ev = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
    episodes = mee_engine.process_event((), ev)
    episode = episodes[0]
    text = mee_serialization.episode_to_json(episode)
    back = mee_serialization.episode_from_json(text)
    assert back == episode


# ---------------------------------------------------------------------------
# Replay/live parity
# ---------------------------------------------------------------------------
def test_replay_live_parity():
    events = [
        _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"i": 1}),
        _mk_event("VOLUME_CHANGED", "2026-07-24T09:16:00", detail={"i": 2}),
        _mk_event("OI_CHANGED", "2026-07-24T09:17:00", detail={"i": 3}),
        _mk_event("PRICE_CHANGED", "2026-07-24T09:18:00", detail={"i": 4}),
    ]
    batch_result = mee_runner.generate_episodes_for_events(tuple(events))

    stream = mee_runner.LiveMarketEpisodeStream()
    for ev in events:
        stream.handle_event(ev)
    live_result = stream.episodes

    assert len(batch_result) == len(live_result) == 1
    b, l = batch_result[0], live_result[0]
    # Content parity (Deliverable 9): identical episode_id and
    # identical growth content. `provenance.detection_context` is
    # expected to differ (REPLAY vs LIVE, exactly like Series 75's own
    # event provenance) -- everything content-relevant must match.
    assert b.episode_id == l.episode_id
    assert b.episode_type == l.episode_type
    assert b.start_time == l.start_time
    assert b.latest_update == l.latest_update
    assert b.end_time == l.end_time
    assert b.originating_event_ids == l.originating_event_ids
    assert b.originating_observation_ids == l.originating_observation_ids
    assert b.current_state == l.current_state


# ---------------------------------------------------------------------------
# Append-only journal recording — structural proof
# ---------------------------------------------------------------------------
def test_journal_append_only_structurally():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "episodes.jsonl")
        j = mee_journal.MarketEpisodeJournal(path)

        ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
        episodes = mee_engine.process_event((), ev1)
        j.record_episode(episodes[0])
        with open(path, "rb") as fh:
            snapshot_1 = fh.read()

        ev2 = _mk_event("VOLUME_CHANGED", "2026-07-24T09:16:00")
        episodes = mee_engine.process_event(episodes, ev2)
        j.record_episode(episodes[0])
        with open(path, "rb") as fh:
            snapshot_2 = fh.read()

        # Prior bytes are an exact, untouched prefix — nothing was
        # rewritten, only appended.
        assert snapshot_2.startswith(snapshot_1)
        assert len(snapshot_2) > len(snapshot_1)

        records = j.read_all()
        assert len(records) == 2


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------
def test_duplicate_event_is_idempotent():
    ev = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
    episodes = mee_engine.process_event((), ev)
    episodes_again = mee_engine.process_event(episodes, ev)
    assert len(episodes_again) == 1
    assert episodes_again[0] == episodes[0]
    assert episodes_again[0].originating_event_ids.count(ev.event_id) == 1


# ---------------------------------------------------------------------------
# INV-1: at most one active episode per event
# ---------------------------------------------------------------------------
def test_event_belongs_to_at_most_one_active_episode():
    ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"i": 1})
    ev2 = _mk_event("PRICE_CHANGED", "2026-07-24T09:16:00", detail={"i": 2})
    episodes = mee_engine.process_event((), ev1)
    episodes = mee_engine.process_event(episodes, ev2)
    # ev2 should have joined the single existing episode, not spawned
    # a second one it could "also" belong to.
    assert len(episodes) == 1
    owners = [e for e in episodes if ev2.event_id in e.originating_event_ids]
    assert len(owners) == 1


# ---------------------------------------------------------------------------
# INV-2: episodes never merge retroactively
# ---------------------------------------------------------------------------
def test_episodes_never_merge_retroactively():
    ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"i": 1})
    ev2 = _mk_event("PRICE_CHANGED", "2026-08-24T09:15:00", detail={"i": 2})  # far away in time -> separate episode
    episodes = mee_engine.process_event((), ev1)
    episodes = mee_engine.process_event(episodes, ev2)
    assert len(episodes) == 2
    id1, id2 = episodes[0].episode_id, episodes[1].episode_id
    assert id1 != id2

    # A subsequent event compatible (in isolation) with BOTH by type
    # still only ever joins one — never causes a merge back into one id.
    ev3 = _mk_event("PRICE_CHANGED", "2026-08-24T09:16:00", detail={"i": 3})
    episodes2 = mee_engine.process_event(episodes, ev3)
    assert len(episodes2) == 2
    ids_after = {e.episode_id for e in episodes2}
    assert ids_after == {id1, id2}


# ---------------------------------------------------------------------------
# INV-3: closing never changes prior contents
# ---------------------------------------------------------------------------
def test_closing_never_changes_prior_contents():
    ev = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
    episodes = mee_engine.process_event((), ev)
    original = episodes[0]
    closed = mee_engine.advance_time(episodes, "2026-07-24T10:30:00")
    # `original` object itself is untouched (dataclass frozen + a new
    # object was returned).
    assert original.current_state == mee_taxonomy.STATE_ACTIVE
    assert original.end_time is None
    assert closed[0].originating_event_ids == original.originating_event_ids
    assert closed[0].originating_observation_ids == original.originating_observation_ids
    assert closed[0].start_time == original.start_time
    assert closed[0].episode_id == original.episode_id


# ---------------------------------------------------------------------------
# INV-4: closed episode never reopens
# ---------------------------------------------------------------------------
def test_closed_episode_never_reopens():
    ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"i": 1})
    episodes = mee_engine.process_event((), ev1)
    closed = mee_engine.advance_time(episodes, "2026-07-24T10:30:00")
    assert closed[0].current_state == mee_taxonomy.STATE_CLOSED
    original_id = closed[0].episode_id

    # A new, otherwise-compatible-looking event arrives close in time
    # to the CLOSED episode's latest_update -- it must NOT reopen it.
    ev2 = _mk_event("PRICE_CHANGED", "2026-07-24T10:31:00", detail={"i": 2})
    episodes2 = mee_engine.process_event(closed, ev2)
    assert len(episodes2) == 2
    closed_episode = [e for e in episodes2 if e.episode_id == original_id][0]
    assert closed_episode.current_state == mee_taxonomy.STATE_CLOSED
    assert ev2.event_id not in closed_episode.originating_event_ids
    new_episode = [e for e in episodes2 if e.episode_id != original_id][0]
    assert ev2.event_id in new_episode.originating_event_ids
    assert new_episode.episode_id != original_id


# ---------------------------------------------------------------------------
# INV-5: deterministic join tie-break
# ---------------------------------------------------------------------------
def test_deterministic_join_tiebreak():
    # Construct two independent, ALREADY-OPEN episodes directly (not
    # via sequential process_event calls, since two events close
    # enough in time/type to both be independently "compatible" with a
    # later probe event would also be compatible with EACH OTHER and
    # simply merge into one on creation -- exactly INV-2's point).
    # Here we fabricate two open episodes with distinct episode_ids and
    # differing latest_update values, and confirm a probe event
    # compatible with both always joins the one with the most recent
    # latest_update, deterministically and repeatably.
    from bujji.market_episode.models import Episode, EpisodeProvenance

    prov = EpisodeProvenance(originating_source="test", detection_context="LIVE", schema_version="1.0.0")

    ep_older = Episode(
        episode_id="EPS-aaaaaaaaaaaaaaaaaaaaaaaa",
        episode_type=mee_taxonomy.PRICE_MOVEMENT_EPISODE,
        start_time="2026-07-24T09:00:00",
        latest_update="2026-07-24T09:10:00",
        end_time=None,
        originating_event_ids=("MEVT-older",),
        originating_observation_ids=("OBS-older",),
        current_state=mee_taxonomy.STATE_ACTIVE,
        provenance=prov,
        schema_version="1.0.0",
    )
    ep_newer = Episode(
        episode_id="EPS-zzzzzzzzzzzzzzzzzzzzzzzz",
        episode_type=mee_taxonomy.PRICE_MOVEMENT_EPISODE,
        start_time="2026-07-24T09:05:00",
        latest_update="2026-07-24T09:12:00",
        end_time=None,
        originating_event_ids=("MEVT-newer",),
        originating_observation_ids=("OBS-newer",),
        current_state=mee_taxonomy.STATE_ACTIVE,
        provenance=prov,
        schema_version="1.0.0",
    )
    episodes = (ep_older, ep_newer)

    ev_c = _mk_event("PRICE_CHANGED", "2026-07-24T09:13:00", detail={"i": "c"})
    result1 = mee_engine.process_event(episodes, ev_c)
    result2 = mee_engine.process_event(episodes, ev_c)
    owner1 = [e.episode_id for e in result1 if ev_c.event_id in e.originating_event_ids]
    owner2 = [e.episode_id for e in result2 if ev_c.event_id in e.originating_event_ids]
    assert owner1 == owner2 == [ep_newer.episode_id]


# ---------------------------------------------------------------------------
# INV-9: time-based transitions independent of event-driven growth
# ---------------------------------------------------------------------------
def test_advance_time_independent_of_process_event():
    ev = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00")
    episodes = mee_engine.process_event((), ev)
    # advance_time alone, with no new event, must be able to progress
    # the lifecycle.
    advanced = mee_engine.advance_time(episodes, "2026-07-24T09:31:00")  # >= default quiescent window
    assert advanced[0].current_state == mee_taxonomy.STATE_QUIESCENT
    assert advanced[0].originating_event_ids == episodes[0].originating_event_ids  # untouched by pure time advance

    # process_event alone never performs a silence check — feeding the
    # SAME episodes (pre-advance) plus a fresh compatible event should
    # reactivate/grow without requiring advance_time to have run first.
    ev2 = _mk_event("PRICE_CHANGED", "2026-07-24T09:31:30", detail={"i": 2})
    grown = mee_engine.process_event(episodes, ev2)
    assert grown[0].current_state == mee_taxonomy.STATE_ACTIVE


# ---------------------------------------------------------------------------
# VALID_TRANSITIONS table sanity
# ---------------------------------------------------------------------------
def test_valid_transitions_table_forbids_closed_outgoing():
    assert mee_taxonomy.VALID_TRANSITIONS[mee_taxonomy.STATE_CLOSED] == ()
    assert not mee_taxonomy.is_valid_transition(mee_taxonomy.STATE_CLOSED, mee_taxonomy.STATE_ACTIVE)
    assert mee_taxonomy.is_valid_transition(mee_taxonomy.STATE_QUIESCENT, mee_taxonomy.STATE_ACTIVE)
    assert mee_taxonomy.is_valid_transition(mee_taxonomy.STATE_ACTIVE, mee_taxonomy.STATE_ACTIVE)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
def test_query_helpers():
    ev1 = _mk_event("PRICE_CHANGED", "2026-07-24T09:15:00", detail={"i": 1})
    episodes = mee_engine.process_event((), ev1)
    ep = episodes[0]
    assert mee_query.episode_by_id(episodes, ep.episode_id) == ep
    assert mee_query.episodes_by_state(episodes, mee_taxonomy.STATE_ACTIVE) == episodes
    assert mee_query.episodes_for_event(episodes, ev1.event_id) == episodes
    assert mee_query.episodes_for_observation(episodes, ev1.originating_observation_ids[0]) == episodes
    assert mee_query.episodes_in_time_range(episodes, "2026-07-24T00:00:00", "2026-07-25T00:00:00") == episodes


# ---------------------------------------------------------------------------
# Deliverable 10 — cross-series demonstration, run twice, byte-identical
# ---------------------------------------------------------------------------
def _run_demonstration():
    instrument = "NIFTY"
    obs1 = _mk_observation(moc_taxonomy.TYPE_PRICE, instrument, "2026-07-24T09:15:00", 100.0)
    obs2 = _mk_observation(moc_taxonomy.TYPE_PRICE, instrument, "2026-07-24T09:16:00", 105.0)
    obs3 = _mk_observation(moc_taxonomy.TYPE_PRICE, instrument, "2026-07-24T09:17:00", 106.0)

    events1, _ = lme_engine.compare_observations(obs1, None, detection_context="REPLAY")
    events2, _ = lme_engine.compare_observations(obs2, obs1, detection_context="REPLAY")
    events3, _ = lme_engine.compare_observations(obs3, obs2, detection_context="REPLAY")

    price_change_events = tuple(
        e for e in (events1 + events2 + events3) if e.event_type in mee_taxonomy.EPISODE_FORMING_EVENT_TYPES
    )
    assert price_change_events, "demonstration requires at least one episode-forming event from real 75 engine output"

    episodes = mee_runner.generate_episodes_for_events(price_change_events)
    assert len(episodes) == 1
    grown = episodes[0]

    # No further compatible events; simulate time advancing well past
    # the configured silence window -> episode closes.
    closed_time = "2026-07-24T10:30:00"
    closed = mee_engine.advance_time(episodes, closed_time)
    assert closed[0].current_state == mee_taxonomy.STATE_CLOSED
    return closed[0]


def test_deliverable_10_demonstration_runs_twice_identically():
    result_1 = _run_demonstration()
    result_2 = _run_demonstration()
    assert result_1 == result_2
    assert mee_serialization.episode_to_json(result_1) == mee_serialization.episode_to_json(result_2)


# ---------------------------------------------------------------------------
# AST isolation
# ---------------------------------------------------------------------------
_FORBIDDEN_MODULE_PREFIXES = (
    "mic_v2",
    "bujji.mic_replay",
    "bujji.production_runtime",
    "bujji.trading_brain",
    "bujji.strategy_selector",
    "fyers_apiv3",
)

_FORBIDDEN_MEANING_TERMS = (
    "bullish",
    "bearish",
    "accumulation",
    "distribution",
    "buildup",
    "short-covering",
    "shortcovering",
    "trend",
    "momentum",
    "compression",
    "expansion",
    "support",
    "resistance",
    "acceptance",
    "rejection",
)


def _market_episode_source_files():
    import bujji.market_episode as pkg

    package_dir = os.path.dirname(pkg.__file__)
    return sorted(glob.glob(os.path.join(package_dir, "*.py")))


def test_ast_isolation_no_forbidden_imports():
    for path in _market_episode_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                        assert not alias.name.startswith(forbidden), f"{path} imports forbidden module {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for forbidden in _FORBIDDEN_MODULE_PREFIXES:
                    assert not mod.startswith(forbidden), f"{path} imports from forbidden module {mod}"


def test_ast_isolation_no_uuid4_no_unseeded_randomness():
    for path in _market_episode_source_files():
        with open(path, "r") as fh:
            source = fh.read()
        tree = ast.parse(source, filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                func_name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                assert func_name != "uuid4", f"{path} calls uuid4()"
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in ("random", "uuid"), f"{path} imports {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                assert node.module not in ("random", "uuid"), f"{path} imports from {node.module}"


def test_ast_isolation_no_forbidden_meaning_terms():
    for path in _market_episode_source_files():
        with open(path, "r") as fh:
            source = fh.read().lower()
        for term in _FORBIDDEN_MEANING_TERMS:
            assert term not in source, f"{path} contains forbidden interpretive term {term!r}"
