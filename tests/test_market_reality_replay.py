"""Phase 17E — deterministic replay.

The guarantee under test:  same input events == same ordered stream.
"""
from bujji.market_reality import replay, taxonomy
from bujji.market_reality.capture import build_raw_observation
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore

CERT = taxonomy.CERTIFIED_AVAILABLE
NOW = "2026-08-12T23:59:00+00:00"


def _obs(ltp, capture, event=None):
    return build_raw_observation(
        kind=taxonomy.KIND_QUOTE,
        instrument="NSE:NIFTY50-INDEX",
        instrument_type=taxonomy.INSTRUMENT_SPOT,
        payload={"ltp": ltp},
        source="fyers",
        access_method="direct_sdk_fyers_broker_py",
        capture_timestamp=capture,
        event_timestamp=event,
        certification_status=CERT,
        identity_fields={},
    )


def _seeded_store(tmp_path, count=5):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    for i in range(count):
        store.append(
            _obs(24300.0 + i, f"2026-08-12T09:2{i}:00+00:00"), now=NOW
        )
    return store


def test_replay_preserves_exact_append_order(tmp_path):
    store = _seeded_store(tmp_path)
    prices = [obs.payload["ltp"] for obs in replay.replay(store)]
    assert prices == [24300.0, 24301.0, 24302.0, 24303.0, 24304.0]


def test_replay_is_deterministic_across_repeated_reads(tmp_path):
    store = _seeded_store(tmp_path)
    assert replay.observation_id_stream(store) == replay.observation_id_stream(store)


def test_replay_is_identical_after_restart(tmp_path):
    store = _seeded_store(tmp_path)
    before = replay.observation_id_stream(store)

    reopened = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    assert replay.observation_id_stream(reopened) == before


def test_replay_from_a_bare_directory_path(tmp_path):
    _seeded_store(tmp_path)
    assert len(replay.observation_id_stream(str(tmp_path))) == 5


def test_as_of_withholds_later_records(tmp_path):
    """A replay pinned to a past instant sees exactly what existed then
    -- the no-look-ahead guarantee applied at the source."""
    store = _seeded_store(tmp_path)
    observations = replay.replay(store, as_of="2026-08-12T09:22:00+00:00")
    prices = [obs.payload["ltp"] for obs in observations]
    assert prices == [24300.0, 24301.0, 24302.0]  # Boundary is inclusive.


def test_as_of_before_all_records_yields_nothing(tmp_path):
    store = _seeded_store(tmp_path)
    assert list(replay.replay(store, as_of="2026-08-12T08:00:00+00:00")) == []


def test_replay_report_counts_a_clean_store(tmp_path):
    store = _seeded_store(tmp_path)
    observations, report = replay.replay_with_report(store)
    assert report.status == replay.REPLAY_COMPLETE
    assert report.observations_discovered == 5
    assert report.observations_replayed == 5
    assert report.observations_skipped_malformed == 0
    assert report.first_observation_id == observations[0].observation_id
    assert report.last_observation_id == observations[-1].observation_id


def test_torn_trailing_line_is_counted_not_raised(tmp_path):
    """Simulates a crash mid-write. A truncated final line must be
    counted as malformed and skipped -- never raise, never be silently
    included as though it were a real observation."""
    store = _seeded_store(tmp_path)
    with open(store.accepted_path, "a") as fh:
        fh.write('{"event_id": "OBS-truncated", "event_ty')

    observations, report = replay.replay_with_report(store)
    assert len(observations) == 5
    assert report.observations_skipped_malformed == 1
    assert report.status == replay.REPLAY_PARTIAL


def test_duplicate_lines_are_counted_and_deduplicated(tmp_path):
    """A physically duplicated line (e.g. a botched file concat) must not
    produce a duplicated observation in the stream."""
    store = _seeded_store(tmp_path, count=2)
    with open(store.accepted_path) as fh:
        first_line = fh.readline()
    with open(store.accepted_path, "a") as fh:
        fh.write(first_line)

    observations, report = replay.replay_with_report(store)
    assert len(observations) == 2
    assert report.observations_skipped_duplicate == 1


def test_unrecognized_schema_version_is_counted_not_silently_dropped(tmp_path):
    store = _seeded_store(tmp_path, count=1)
    import json

    with open(store.accepted_path) as fh:
        record = json.loads(fh.readline())
    record["event_id"] = "OBS-fromthefuture"
    record["schema_version"] = "99.0.0"
    with open(store.accepted_path, "a") as fh:
        fh.write(json.dumps(record) + "\n")

    observations, report = replay.replay_with_report(store)
    assert len(observations) == 1
    assert report.observations_skipped_schema_mismatch == 1
    assert report.status == replay.REPLAY_PARTIAL


def test_replay_of_an_empty_store_is_empty_not_an_error(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    observations, report = replay.replay_with_report(store)
    assert observations == []
    assert report.status == replay.REPLAY_COMPLETE
    assert report.first_observation_id is None


def test_rejected_observations_never_appear_in_the_replay_stream(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    store.append(_obs(24300.0, "2026-08-12T09:20:00+00:00"), now=NOW)
    store.append(
        build_raw_observation(
            kind=taxonomy.KIND_QUOTE,
            instrument="NSE:NIFTY50-INDEX",
            instrument_type=taxonomy.INSTRUMENT_SPOT,
            payload={"ltp": 1.0, "iv": 0.3},
            source="fyers",
            access_method="direct_sdk_fyers_broker_py",
            capture_timestamp="2026-08-12T09:21:00+00:00",
            certification_status=CERT,
            identity_fields={},
        ),
        now=NOW,
    )
    assert len(replay.observation_id_stream(store)) == 1
    assert store.rejection_count() == 1


def test_replayed_observation_round_trips_with_full_fidelity(tmp_path):
    """Every field written must survive the JSONL round trip -- a replay
    that loses lineage cannot support the Phase 17D rebuild proof."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    original = _obs(24325.8, "2026-08-12T09:20:00+00:00", "2026-08-12T09:19:59+00:00")
    store.append(original, now=NOW)

    restored = list(replay.replay(store))[0]
    assert restored.observation_id == original.observation_id
    assert restored.kind == original.kind
    assert restored.instrument_type == original.instrument_type
    assert restored.payload == original.payload
    assert restored.lineage.source == original.lineage.source
    assert restored.lineage.access_method == original.lineage.access_method
    assert restored.lineage.event_timestamp == original.lineage.event_timestamp
    assert restored.lineage.capture_timestamp == original.lineage.capture_timestamp
    assert restored.lineage.confidence == original.lineage.confidence
    assert restored.lineage.transformation_history == (
        taxonomy.TRANSFORMATION_RAW_CAPTURE,
    )


def test_as_of_is_sugar_for_both_bounds_set_equal(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    store.append(
        _obs(24300.0, capture="2026-08-12T09:20:00+00:00", event="2026-08-12T09:19:00+00:00"),
        now=NOW,
    )
    via_as_of = list(replay.replay(store, as_of="2026-08-12T09:19:00+00:00"))
    via_dual = list(replay.replay(
        store,
        as_of_event_time="2026-08-12T09:19:00+00:00",
        as_of_knowledge_time="2026-08-12T09:19:00+00:00",
    ))
    assert [o.observation_id for o in via_as_of] == [o.observation_id for o in via_dual]


def test_as_of_together_with_explicit_bound_is_rejected(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    import pytest
    with pytest.raises(ValueError):
        list(replay.replay(store, as_of="2026-08-12T09:19:00+00:00", as_of_event_time="2026-08-12T09:19:00+00:00"))
    with pytest.raises(ValueError):
        list(replay.replay(store, as_of="2026-08-12T09:19:00+00:00", as_of_knowledge_time="2026-08-12T09:19:00+00:00"))


def test_known_at_10_30_excludes_a_fact_learned_at_10_31_even_if_it_happened_earlier(tmp_path):
    """The exact scenario the 17F.1.2 audit flagged: an observation whose
    market event occurred before the cutoff, but which Bujji only learned
    of AFTER the cutoff, must not appear in a query pinned to that cutoff
    -- a single collapsed `as_of` bound cannot express this distinction,
    because event_timestamp alone would let it through."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    store.append(
        _obs(24300.0, capture="2026-08-12T10:31:00+00:00", event="2026-08-12T10:29:00+00:00"),
        now=NOW,
    )
    result = list(replay.replay(
        store,
        as_of_event_time="2026-08-12T10:30:00+00:00",
        as_of_knowledge_time="2026-08-12T10:30:00+00:00",
    ))
    assert result == []


def test_known_at_10_30_includes_a_fact_that_happened_and_was_learned_in_time(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    original = _obs(24300.0, capture="2026-08-12T10:29:30+00:00", event="2026-08-12T10:29:00+00:00")
    store.append(original, now=NOW)
    result = list(replay.replay(
        store,
        as_of_event_time="2026-08-12T10:30:00+00:00",
        as_of_knowledge_time="2026-08-12T10:30:00+00:00",
    ))
    assert [o.observation_id for o in result] == [original.observation_id]


def test_knowledge_time_bound_alone_still_excludes_late_learned_facts(tmp_path):
    """Only `as_of_knowledge_time` set (event-time bound left open) --
    knowledge_time is the safety net that always applies."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    store.append(
        _obs(24300.0, capture="2026-08-12T10:31:00+00:00", event="2026-08-12T10:29:00+00:00"),
        now=NOW,
    )
    result = list(replay.replay(store, as_of_knowledge_time="2026-08-12T10:30:00+00:00"))
    assert result == []


def test_event_time_bound_is_skipped_when_event_time_is_absent(tmp_path):
    """An observation with no published event_timestamp cannot be
    excluded on event-time grounds -- there is nothing to compare. Its
    knowledge_time (always present) still governs visibility."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    original = _obs(24300.0, capture="2026-08-12T09:20:00+00:00", event=None)
    store.append(original, now=NOW)
    result = list(replay.replay(
        store,
        as_of_event_time="2026-08-12T00:00:00+00:00",
        as_of_knowledge_time="2026-08-12T09:20:00+00:00",
    ))
    assert [o.observation_id for o in result] == [original.observation_id]


def test_observation_id_stream_and_replay_with_report_accept_dual_bounds(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    store.append(
        _obs(24300.0, capture="2026-08-12T10:31:00+00:00", event="2026-08-12T10:29:00+00:00"),
        now=NOW,
    )
    assert replay.observation_id_stream(
        store,
        as_of_event_time="2026-08-12T10:30:00+00:00",
        as_of_knowledge_time="2026-08-12T10:30:00+00:00",
    ) == []
    observations, report = replay.replay_with_report(
        store,
        as_of_event_time="2026-08-12T10:30:00+00:00",
        as_of_knowledge_time="2026-08-12T10:30:00+00:00",
    )
    assert observations == []
    assert report.observations_discovered == 1
