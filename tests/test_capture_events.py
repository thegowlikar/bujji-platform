"""Phase 17F.0.1 step 3 — CaptureEvent + Layer 0 schema 1.2.0.

A CaptureEvent records a fact about THE OBSERVER. These tests hold the
line that it never becomes a market observation, never enters MOC, and
never reaches a materializer.
"""
from bujji.market_reality import replay, taxonomy
from bujji.market_reality.capture_events import (
    CaptureEvent,
    build_capture_event,
    validate_capture_event,
)
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore

CERT = taxonomy.CERTIFIED_AVAILABLE
NOW = "2026-08-12T09:30:00+00:00"


def _gate():
    return StaticCertificationGate(CERT)


def _event(**overrides):
    kwargs = dict(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00",
        knowledge_time="2026-08-12T09:20:01+00:00",
        source="fyers",
        access_method="fyers_websocket",
        affected_instruments=("NSE:NIFTY50-INDEX",),
        certification_status=CERT,
    )
    kwargs.update(overrides)
    return build_capture_event(**kwargs)


def _obs(ltp=24325.8, capture="2026-08-12T09:19:00+00:00"):
    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument="NSE:NIFTY50-INDEX",
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=capture,
        event_timestamp=capture,
        certification_status=CERT,
        identity_fields={},
    )


# --- Schema versioning ----------------------------------------------------
def test_layer0_schema_bumped_to_1_2_0():
    assert taxonomy.LAYER0_SCHEMA_VERSION == "1.2.0"


def test_prior_layer0_schema_versions_still_recognized():
    """A version bump gates new consumers; it never invalidates old
    facts."""
    for version in ("1.0.0", "1.1.0", "1.2.0"):
        assert version in taxonomy.RECOGNIZED_LAYER0_SCHEMA_VERSIONS


def test_moc_schema_untouched_at_1_1_0():
    """A capture gap is not a market observation domain, so the market
    vocabulary gains nothing and its version does not move."""
    from bujji.market_observation import taxonomy as moc_taxonomy

    assert moc_taxonomy.MARKET_OBSERVATION_VERSION == "1.1.0"


def test_no_capture_type_leaked_into_moc():
    from bujji.market_observation import taxonomy as moc_taxonomy

    for name in moc_taxonomy.ALL_OBSERVATION_TYPES:
        assert "CAPTURE" not in name
        assert "GAP" not in name


def test_capture_event_is_not_an_observation_kind():
    """Structural, not conventional: because CAPTURE_EVENT is absent from
    ALL_OBSERVATION_KINDS, the observation validator cannot accept one as
    market data."""
    assert taxonomy.KIND_CAPTURE_EVENT not in taxonomy.ALL_OBSERVATION_KINDS


# --- Identity + construction ---------------------------------------------
def test_event_id_is_deterministic():
    assert _event().event_id == _event().event_id


def test_event_id_is_content_addressed():
    a = _event(reason=taxonomy.REASON_DISCONNECT)
    b = _event(reason=taxonomy.REASON_AUTH_FAILURE)
    assert a.event_id != b.event_id


def test_distinct_disconnects_are_distinct_events():
    a = _event(event_time="2026-08-12T09:20:00+00:00")
    b = _event(event_time="2026-08-12T11:42:00+00:00")
    assert a.event_id != b.event_id


def test_event_and_knowledge_times_kept_distinct():
    e = _event()
    assert e.event_time != e.knowledge_time
    assert e.to_dict()["event_time"] == "2026-08-12T09:20:00+00:00"
    assert e.to_dict()["knowledge_time"] == "2026-08-12T09:20:01+00:00"


def test_round_trip_preserves_every_field():
    original = _event(count=42, related_event_id="CAP-abc", detail="socket closed")
    restored = CaptureEvent.from_dict(original.to_dict())
    assert restored == original


def test_all_seven_reasons_are_constructible():
    for reason in taxonomy.ALL_CAPTURE_REASONS:
        ok, problems = validate_capture_event(_event(reason=reason))
        assert ok, problems
    assert len(taxonomy.ALL_CAPTURE_REASONS) == 7


# --- Validation -----------------------------------------------------------
def test_unknown_reason_rejected():
    ok, problems = validate_capture_event(_event(reason="VIBES"))
    assert not ok
    assert any("UNKNOWN_CAPTURE_REASON" in p for p in problems)


def test_malformed_timestamps_rejected():
    ok, problems = validate_capture_event(_event(event_time="whenever"))
    assert not ok
    assert "MALFORMED_EVENT_TIME" in problems


def test_missing_source_rejected():
    ok, problems = validate_capture_event(_event(source=""))
    assert not ok
    assert "MISSING_SOURCE" in problems


def test_negative_count_rejected():
    ok, problems = validate_capture_event(_event(count=-1))
    assert not ok
    assert "NEGATIVE_COUNT" in problems


# --- Store integration ----------------------------------------------------
def test_capture_event_is_stored(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    result = store.append_capture_event(_event())
    assert result.outcome == taxonomy.OUTCOME_ACCEPTED
    assert len(list(store.read_accepted_events())) == 1


def test_capture_event_is_not_certification_gated(tmp_path):
    """A capture event describes our own process. Recording 'we were
    disconnected' cannot sensibly require FYERS to certify it -- so it
    stores even when the gate would reject market data."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(taxonomy.NOT_CERTIFIED))
    assert store.append_capture_event(_event()).outcome == taxonomy.OUTCOME_ACCEPTED
    # ...while a market observation from the same uncertified source is refused.
    assert store.append(_obs(), now=NOW).outcome == taxonomy.OUTCOME_REJECTED


def test_malformed_capture_event_lands_in_rejection_store(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    result = store.append_capture_event(_event(reason="VIBES"))
    assert result.outcome == taxonomy.OUTCOME_REJECTED
    assert list(store.read_accepted_events()) == []
    assert store.rejection_count() == 1


def test_identical_capture_event_is_idempotent(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    event = _event()
    assert store.append_capture_event(event).outcome == taxonomy.OUTCOME_ACCEPTED
    assert store.append_capture_event(event).outcome == taxonomy.OUTCOME_DUPLICATE
    assert len(list(store.read_accepted_events())) == 1


def test_capture_events_survive_restart(tmp_path):
    RawObservationStore(tmp_path, _gate()).append_capture_event(_event())
    reopened = RawObservationStore(tmp_path, _gate())
    assert reopened.append_capture_event(_event()).outcome == taxonomy.OUTCOME_DUPLICATE


# --- Replay: the ordered union -------------------------------------------
def test_capture_events_appear_in_the_union_stream_in_order(tmp_path):
    """A stream that omitted blind spots would let a consumer
    reconstruct a market that never went quiet."""
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(24300.0, "2026-08-12T09:18:00+00:00"), now=NOW)
    store.append_capture_event(_event())
    store.append(_obs(24310.0, "2026-08-12T09:22:00+00:00"), now=NOW)

    items = list(replay.replay_stream(store))
    assert [i.kind for i in items] == [
        taxonomy.STREAM_OBSERVATION,
        taxonomy.STREAM_CAPTURE_EVENT,
        taxonomy.STREAM_OBSERVATION,
    ]
    assert items[1].capture_event.reason == taxonomy.REASON_DISCONNECT


def test_observation_replay_excludes_capture_events(tmp_path):
    """What a materializer consumes. A capture event must never be
    foldable into a candle."""
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    store.append_capture_event(_event())

    observations = list(replay.replay(store))
    assert len(observations) == 1
    assert all(hasattr(o, "observation_id") for o in observations)


def test_capture_events_are_counted_not_treated_as_corruption(tmp_path):
    """Lumping recorded blind spots into `malformed` would make honesty
    look like data corruption."""
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    store.append_capture_event(_event())

    observations, report = replay.replay_with_report(store)
    assert len(observations) == 1
    assert report.capture_events_seen == 1
    assert report.observations_skipped_malformed == 0
    assert report.status == replay.REPLAY_COMPLETE


def test_capture_event_only_view(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    store.append_capture_event(_event())
    store.append_capture_event(_event(reason=taxonomy.REASON_QUEUE_OVERFLOW, count=17))

    events = replay.replay_capture_events(store)
    assert len(events) == 2
    assert {e.reason for e in events} == {
        taxonomy.REASON_DISCONNECT, taxonomy.REASON_QUEUE_OVERFLOW,
    }


def test_union_stream_respects_as_of(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(24300.0, "2026-08-12T09:18:00+00:00"), now=NOW)
    store.append_capture_event(_event(event_time="2026-08-12T09:25:00+00:00"))

    items = list(replay.replay_stream(store, as_of="2026-08-12T09:20:00+00:00"))
    assert len(items) == 1
    assert items[0].is_observation


def test_union_replay_is_deterministic(tmp_path):
    store = RawObservationStore(tmp_path, _gate())
    store.append(_obs(), now=NOW)
    store.append_capture_event(_event())

    first = [(i.kind, i.timestamp) for i in replay.replay_stream(store)]
    second = [(i.kind, i.timestamp) for i in replay.replay_stream(store)]
    assert first == second


# --- Intervals are reconstructed from linked point events ----------------
def test_gap_interval_is_a_linked_pair_not_a_mutated_record(tmp_path):
    """Layer 0 is immutable, so a gap cannot be opened and later closed --
    closing it would be a mutation the store forbids. A recovery event
    links back to the disconnect it closes instead."""
    store = RawObservationStore(tmp_path, _gate())
    disconnect = _event(reason=taxonomy.REASON_DISCONNECT,
                        event_time="2026-08-12T09:20:00+00:00")
    store.append_capture_event(disconnect)
    recovery = _event(
        reason=taxonomy.REASON_RECONNECT_RECOVERED,
        event_time="2026-08-12T09:24:00+00:00",
        related_event_id=disconnect.event_id,
    )
    store.append_capture_event(recovery)

    events = replay.replay_capture_events(store)
    assert events[1].related_event_id == events[0].event_id


def test_unclosed_gap_is_left_unclosed(tmp_path):
    """A process killed before recovery leaves a disconnect with no
    partner. That is the honest representation of a blind spot whose end
    we genuinely never observed."""
    store = RawObservationStore(tmp_path, _gate())
    store.append_capture_event(_event(reason=taxonomy.REASON_DISCONNECT))

    events = replay.replay_capture_events(store)
    assert len(events) == 1
    assert events[0].related_event_id is None


def test_capture_event_excluded_when_known_after_the_knowledge_bound(tmp_path):
    """A capture event that occurred before as_of_event_time but was only
    recorded (knowledge_time) after as_of_knowledge_time must not appear
    -- capture events get the same dual-bound treatment as observations."""
    store = RawObservationStore(tmp_path, _gate())
    store.append_capture_event(_event(
        event_time="2026-08-12T09:20:00+00:00",
        knowledge_time="2026-08-12T09:26:00+00:00",
    ))

    events = replay.replay_capture_events(
        store,
        as_of_event_time="2026-08-12T09:22:00+00:00",
        as_of_knowledge_time="2026-08-12T09:22:00+00:00",
    )
    assert events == []
