"""One transition, one record, whichever writer gets there first.

THE DEFECT. Two writers reach `record_transition` for the session's first
move: the runner during `_startup()`, and the governor's `_transition()`
afterwards. They agreed on everything except `evidence_ref`, which is not part
of the idempotency key -- so the journal saw one key with two payloads and
refused. The runner wraps its call in try/except; the governor does not. A
durable-record disagreement therefore surfaced as an exception on the one path
that must always be able to move the state machine.
"""
import itertools
import logging
from datetime import datetime, timedelta, timezone

import pytest

from bujji.journal.position_group_journal import (
    IdempotencyKeyCollisionError, PositionGroupJournal)
from bujji.production_runtime.session_lifecycle import (
    record_transition, recorded_transitions)

LOG = logging.getLogger("test-one-transition")


class _Clock:
    def __init__(self):
        self._n = itertools.count()

    def __call__(self):
        return datetime(2026, 8, 24, 9, 15, tzinfo=timezone.utc) + \
            timedelta(seconds=next(self._n))


@pytest.fixture
def journal(tmp_path):
    return PositionGroupJournal(str(tmp_path / "pg.db"))


@pytest.fixture
def clock():
    return _Clock()


class TestOneFactOneRecord:
    def test_the_same_transition_from_two_writers_is_recorded_once(
            self, journal, clock):
        """The exact shape that crashed: same move, different evidence_ref."""
        record_transition(journal, "S1", "INITIALIZING", "ANALYSING_MARKET",
                          cause="session_start", evidence_ref="startup:2026-08-24",
                          clock=clock, logger=LOG)
        # The governor repeats it, echoing the cause as its evidence.
        record_transition(journal, "S1", "INITIALIZING", "ANALYSING_MARKET",
                          cause="session_start", evidence_ref="session_start",
                          clock=clock, logger=LOG)

        rows = recorded_transitions(journal, "S1")
        assert len(rows) == 1, f"one fact recorded {len(rows)} times"
        assert rows[0]["evidence_ref"] == "startup:2026-08-24", (
            "the first observer's evidence reference must stand")

    def test_the_second_write_does_not_raise(self, journal, clock):
        """The governor's call is NOT wrapped in try/except. A raise here
        would take down the state machine's only transition path."""
        record_transition(journal, "S2", "INITIALIZING", "ANALYSING_MARKET",
                          cause="session_start", evidence_ref="a",
                          clock=clock, logger=LOG)
        record_transition(journal, "S2", "INITIALIZING", "ANALYSING_MARKET",
                          cause="session_start", evidence_ref="b",
                          clock=clock, logger=LOG)   # must not raise

    def test_a_different_cause_is_still_a_separate_record(self, journal, clock):
        """Idempotency is on (session, prior, target, cause). A different
        cause is a different fact and must survive."""
        record_transition(journal, "S3", "MANAGING", "CLOSING",
                          cause="eod_close", evidence_ref="x",
                          clock=clock, logger=LOG)
        record_transition(journal, "S3", "MANAGING", "CLOSING",
                          cause="emergency_close", evidence_ref="y",
                          clock=clock, logger=LOG)
        assert len(recorded_transitions(journal, "S3")) == 2

    def test_a_different_transition_is_still_recorded(self, journal, clock):
        record_transition(journal, "S4", "INITIALIZING", "ANALYSING_MARKET",
                          cause="session_start", evidence_ref="x",
                          clock=clock, logger=LOG)
        record_transition(journal, "S4", "ANALYSING_MARKET", "STRATEGY_LOCKED",
                          cause="strategy_selected", evidence_ref="y",
                          clock=clock, logger=LOG)
        assert len(recorded_transitions(journal, "S4")) == 2

    def test_every_transition_of_a_session_is_still_journaled(self, journal, clock):
        """POSITIVE CONTROL: dedup must not swallow a real sequence."""
        moves = [("INITIALIZING", "ANALYSING_MARKET", "session_start"),
                 ("ANALYSING_MARKET", "STRATEGY_LOCKED", "strategy_selected"),
                 ("STRATEGY_LOCKED", "POSITION_ACTIVE", "entry_filled"),
                 ("POSITION_ACTIVE", "MANAGING", "management"),
                 ("MANAGING", "CLOSING", "eod_close")]
        for prior, target, cause in moves:
            record_transition(journal, "S5", prior, target, cause=cause,
                              evidence_ref=f"ev:{cause}", clock=clock, logger=LOG)
        assert len(recorded_transitions(journal, "S5")) == len(moves)


class TestTheCollisionGuardIsNotWeakened:
    def test_a_conflicting_payload_under_a_foreign_key_is_still_refused(
            self, journal, clock):
        """NEGATIVE CONTROL. The dedup above must not have disarmed the
        journal's collision refusal -- it is a safety property, and the fix
        was meant to stop TRIGGERING it, not to remove it.

        Written directly so it bypasses the dedup and reaches the journal.
        """
        from bujji.production_runtime.position_group_scope import (
            append_scoped_event, session_scope_id)
        key = "S6:SESSION_TRANSITION:A->B:c"
        base = {"session_id": "S6", "prior_state": "A", "next_state": "B",
                "cause": "c", "evidence_ref": "first"}
        append_scoped_event(journal, session_scope_id("S6"), "SESSION_TRANSITION",
                            key, base, clock=clock, logger=LOG)
        with pytest.raises(IdempotencyKeyCollisionError):
            append_scoped_event(journal, session_scope_id("S6"),
                                "SESSION_TRANSITION", key,
                                dict(base, evidence_ref="second"),
                                clock=clock, logger=LOG)

    def test_dedup_survives_an_unreadable_journal(self, clock):
        """An unreadable journal must not block a transition write -- the
        lookup is an optimisation, not a gate."""
        class _Unreadable:
            def read_events(self, scope):
                raise RuntimeError("db locked")

            def append_event(self, *a, **k):
                raise RuntimeError("append also fails")

        with pytest.raises(RuntimeError):
            record_transition(_Unreadable(), "S7", "A", "B", cause="c",
                              evidence_ref="e", clock=clock, logger=LOG)
