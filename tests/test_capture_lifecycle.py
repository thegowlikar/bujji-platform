"""Phase 17F.5 gap closure — CaptureLifecycleTracker.

The 17F.5 audit's headline finding: `CaptureEvent` and
`append_capture_event()` were fully implemented and tested since 17F.0.1,
but nothing ever emitted one live. These tests prove the tracker that
closes that gap: exactly one event per real transition, correct
open/close pairing via `related_event_id`, idempotence while a condition
stays open, and that recoveries/point-events without a prior open
condition are handled honestly rather than fabricated.
"""
import pytest

from bujji.market_reality import replay, taxonomy
from bujji.market_reality.capture_lifecycle import (
    OPENING_REASONS,
    POINT_REASONS,
    CaptureLifecycleTracker,
)
from bujji.market_reality.certification import StaticCertificationGate
from bujji.market_reality.store import RawObservationStore

CERT = taxonomy.CERTIFIED_AVAILABLE
SOURCE = "fyers"
ACCESS_METHOD = "direct_sdk_fyers_broker_py"


def _tracker(tmp_path):
    store = RawObservationStore(tmp_path, StaticCertificationGate(CERT))
    return store, CaptureLifecycleTracker(store=store, source=SOURCE, access_method=ACCESS_METHOD)


def test_record_condition_emits_exactly_one_event(tmp_path):
    store, tracker = _tracker(tmp_path)
    result = tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    assert result is not None
    assert result.outcome == taxonomy.OUTCOME_ACCEPTED
    events = replay.replay_capture_events(store)
    assert len(events) == 1
    assert events[0].reason == taxonomy.REASON_DISCONNECT
    assert tracker.has_open_condition is True
    assert tracker.open_reason == taxonomy.REASON_DISCONNECT


def test_repeated_condition_while_open_is_idempotent_no_op(tmp_path):
    """The exact bug this tracker exists to prevent: a poller failing
    every 60s against a broken connection must not flood the log with a
    fresh DISCONNECT every cycle."""
    store, tracker = _tracker(tmp_path)
    tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    second = tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:21:00+00:00", knowledge_time="2026-08-12T09:21:01+00:00",
    )
    third = tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:22:00+00:00", knowledge_time="2026-08-12T09:22:01+00:00",
    )
    assert second is None
    assert third is None
    events = replay.replay_capture_events(store)
    assert len(events) == 1  # Still exactly one -- the original.


def test_recovery_closes_the_open_condition_via_related_event_id(tmp_path):
    store, tracker = _tracker(tmp_path)
    tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    disconnect_id = tracker._open_event_id  # noqa: SLF001 -- white-box check of internal linkage.

    result = tracker.record_recovery(
        event_time="2026-08-12T09:24:00+00:00", knowledge_time="2026-08-12T09:24:01+00:00",
    )
    assert result is not None
    events = replay.replay_capture_events(store)
    assert len(events) == 2
    assert events[0].reason == taxonomy.REASON_DISCONNECT
    assert events[1].reason == taxonomy.REASON_RECONNECT_RECOVERED
    assert events[1].related_event_id == disconnect_id == events[0].event_id
    assert tracker.has_open_condition is False
    assert tracker.open_reason is None


def test_recovery_with_nothing_open_is_an_honest_no_op(tmp_path):
    """A 'recovery' with no recorded failure would fabricate a disconnect
    that was never observed -- this tracker refuses to assert it."""
    store, tracker = _tracker(tmp_path)
    result = tracker.record_recovery(
        event_time="2026-08-12T09:24:00+00:00", knowledge_time="2026-08-12T09:24:01+00:00",
    )
    assert result is None
    assert replay.replay_capture_events(store) == []


def test_a_new_condition_can_open_again_after_recovery_closes_the_prior_one(tmp_path):
    store, tracker = _tracker(tmp_path)
    tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    tracker.record_recovery(
        event_time="2026-08-12T09:24:00+00:00", knowledge_time="2026-08-12T09:24:01+00:00",
    )
    second_open = tracker.record_condition(
        reason=taxonomy.REASON_AUTH_FAILURE,
        event_time="2026-08-12T09:30:00+00:00", knowledge_time="2026-08-12T09:30:01+00:00",
    )
    assert second_open is not None
    events = replay.replay_capture_events(store)
    assert len(events) == 3
    assert events[2].reason == taxonomy.REASON_AUTH_FAILURE
    assert tracker.has_open_condition is True


def test_point_events_are_never_deduplicated(tmp_path):
    store, tracker = _tracker(tmp_path)
    tracker.record_point_event(
        reason=taxonomy.REASON_QUEUE_OVERFLOW, count=17,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    tracker.record_point_event(
        reason=taxonomy.REASON_QUEUE_OVERFLOW, count=42,
        event_time="2026-08-12T09:25:00+00:00", knowledge_time="2026-08-12T09:25:01+00:00",
    )
    events = replay.replay_capture_events(store)
    assert len(events) == 2
    assert [e.count for e in events] == [17, 42]


def test_point_event_does_not_affect_open_condition_state(tmp_path):
    store, tracker = _tracker(tmp_path)
    tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    tracker.record_point_event(
        reason=taxonomy.REASON_RATE_LIMIT_SKIP,
        event_time="2026-08-12T09:21:00+00:00", knowledge_time="2026-08-12T09:21:01+00:00",
    )
    assert tracker.has_open_condition is True
    assert tracker.open_reason == taxonomy.REASON_DISCONNECT


def test_record_condition_rejects_a_point_reason(tmp_path):
    _, tracker = _tracker(tmp_path)
    with pytest.raises(ValueError):
        tracker.record_condition(
            reason=taxonomy.REASON_QUEUE_OVERFLOW,
            event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
        )


def test_record_condition_rejects_reconnect_recovered_itself(tmp_path):
    _, tracker = _tracker(tmp_path)
    with pytest.raises(ValueError):
        tracker.record_condition(
            reason=taxonomy.REASON_RECONNECT_RECOVERED,
            event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
        )


def test_record_point_event_rejects_an_opening_reason(tmp_path):
    _, tracker = _tracker(tmp_path)
    with pytest.raises(ValueError):
        tracker.record_point_event(
            reason=taxonomy.REASON_DISCONNECT,
            event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
        )


def test_opening_and_point_reason_sets_are_disjoint_and_cover_the_pairable_vocabulary():
    assert set(OPENING_REASONS) & set(POINT_REASONS) == set()
    assert taxonomy.REASON_RECONNECT_RECOVERED not in OPENING_REASONS
    assert taxonomy.REASON_RECONNECT_RECOVERED not in POINT_REASONS
    for reason in OPENING_REASONS + POINT_REASONS + (taxonomy.REASON_RECONNECT_RECOVERED,):
        assert reason in taxonomy.ALL_CAPTURE_REASONS


def test_events_are_never_certification_gated(tmp_path):
    """Capture events describe the observer, not broker data -- an
    UNCERTIFIED gate must not block them (mirrors capture_events.py's own
    'NOT CERTIFICATION-GATED' design note)."""
    store = RawObservationStore(tmp_path, StaticCertificationGate(taxonomy.NOT_CERTIFIED))
    tracker = CaptureLifecycleTracker(store=store, source=SOURCE, access_method=ACCESS_METHOD)
    result = tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    assert result.outcome == taxonomy.OUTCOME_ACCEPTED


def test_replay_reconstructs_an_unclosed_interval_honestly(tmp_path):
    """A process killed before recovery must leave the DISCONNECT
    correctly unclosed -- this tracker must never fabricate a synthetic
    recovery to 'tidy up' an interval it never actually observed closing."""
    store, tracker = _tracker(tmp_path)
    tracker.record_condition(
        reason=taxonomy.REASON_DISCONNECT,
        event_time="2026-08-12T09:20:00+00:00", knowledge_time="2026-08-12T09:20:01+00:00",
    )
    events = replay.replay_capture_events(store)
    assert len(events) == 1
    assert events[0].related_event_id is None


def test_multiple_disconnect_recovery_cycles_all_link_correctly(tmp_path):
    store, tracker = _tracker(tmp_path)
    cycles = [
        ("2026-08-12T09:20:00+00:00", "2026-08-12T09:24:00+00:00"),
        ("2026-08-12T10:00:00+00:00", "2026-08-12T10:05:00+00:00"),
        ("2026-08-12T11:00:00+00:00", "2026-08-12T11:01:00+00:00"),
    ]
    for down_at, up_at in cycles:
        tracker.record_condition(
            reason=taxonomy.REASON_DISCONNECT, event_time=down_at, knowledge_time=down_at,
        )
        tracker.record_recovery(event_time=up_at, knowledge_time=up_at)

    events = replay.replay_capture_events(store)
    assert len(events) == 6
    disconnects = [e for e in events if e.reason == taxonomy.REASON_DISCONNECT]
    recoveries = [e for e in events if e.reason == taxonomy.REASON_RECONNECT_RECOVERED]
    assert len(disconnects) == 3
    assert len(recoveries) == 3
    assert [r.related_event_id for r in recoveries] == [d.event_id for d in disconnects]
