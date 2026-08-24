"""The EOD path must record an exit order before it can send one.

Until this was wired, `run_eod_closure` called `place_fn` with nothing in the
journal naming the order. A process that died between the send and the
response left an exit that no later run could find, and the next attempt's
residual read could send a second one.
"""
import asyncio
import datetime
import itertools
import logging

import pytest

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime import exit_lifecycle as EL
from bujji.production_runtime.eod_closure import STATE_UNFLATTENED, run_eod_closure
from bujji.production_runtime.position_group_scope import position_group_ids

LOG = logging.getLogger("test-eod-intent")


class _Clock:
    def __init__(self):
        self._n = itertools.count()

    def __call__(self):
        return (datetime.datetime(2026, 8, 23, 15, 20, tzinfo=datetime.timezone.utc)
                + datetime.timedelta(seconds=next(self._n)))


class _Status:
    def __init__(self, value):
        self.value = value


class _Outcome:
    def __init__(self, coid, status, filled=0, price=None):
        self.client_order_id = coid
        self.order_id = "BRK-" + coid[-4:]
        self.status = _Status(status)
        self.filled_quantity = filled
        self.average_price = price


def _pos(symbol="NIFTY24000CE", qty=75, side="SELL"):
    return {"symbol": symbol, "qty": qty, "side": side}


class _Broker:
    def __init__(self, reads):
        self._reads = list(reads)
        self.placed = []

    async def get_open_positions(self):
        # The LAST scripted read repeats, rather than falling back to an empty
        # book. Falling back to [] would silently hand every test a flat
        # account at the final reconcile -- reporting success for a position
        # the test deliberately left open.
        if len(self._reads) > 1:
            return self._reads.pop(0)
        return self._reads[0] if self._reads else []

    async def get_orders(self):
        return []

    async def cancel_order(self, coid):
        return None


def _run(broker, place_fn, tmp_path, clock, attempts=2):
    path = str(tmp_path / "j.db")
    journal = PositionGroupJournal(path)
    result = run_eod_closure(
        broker=broker, place_fn=place_fn, run_async=asyncio.run, journal=journal,
        journal_db_path=path, underlying="NIFTY", lot_size=75, session_id="S1",
        logger=LOG, max_attempts=attempts, clock=clock)
    return result, journal


class TestIntentIsJournaledBeforePlacement:
    def test_the_order_is_in_the_journal_when_place_fn_is_called(
            self, tmp_path):
        """POSITIVE CONTROL that the wiring is live, checked from INSIDE the
        placement itself -- the only moment at which the ordering matters."""
        clock = _Clock()
        seen = {}
        path = str(tmp_path / "j.db")
        journal = PositionGroupJournal(path)

        def place(request):
            coid = request.client_order_id
            # Every exit attempt in this session, wherever its exposure lives.
            seen[coid] = [h["broker_client_order_id"]
                          for gid in journal.read_all_group_ids()
                          for h in EL.attempt_history(journal, gid)]
            return _Outcome(coid, "FILLED", filled=75, price=1.0)

        run_eod_closure(
            broker=_Broker([[_pos()], []]), place_fn=place, run_async=asyncio.run,
            journal=journal, journal_db_path=path, underlying="NIFTY",
            lot_size=75, session_id="S1", logger=LOG, max_attempts=2, clock=clock)

        assert seen, "place_fn was never called; this test proves nothing"
        for coid, intents in seen.items():
            assert coid in intents, (
                f"{coid} was placed with no attempt record in the journal -- a "
                f"crash here leaves an order nothing can find")

    def test_the_exit_order_id_comes_from_the_journaled_plan(self, tmp_path):
        clock = _Clock()
        placed = []
        result, journal = _run(
            _Broker([[_pos()], []]),
            lambda r: (placed.append(r.client_order_id),
                       _Outcome(r.client_order_id, "FILLED", 75, 1.0))[1],
            tmp_path, clock)
        assert placed, "nothing was placed; this test proves nothing"
        journaled = {h["broker_client_order_id"]
                     for gid in journal.read_all_group_ids()
                     for h in EL.attempt_history(journal, gid)}
        assert placed[0] in journaled, (
            f"{placed[0]} was not the order id the journal recorded")

    def test_a_refused_intent_places_nothing(self, tmp_path):
        """NEGATIVE CONTROL: if the journal cannot record, nothing may be sent."""
        clock = _Clock()
        path = str(tmp_path / "j.db")
        real = PositionGroupJournal(path)
        placed = []

        class _Unwritable:
            def read_all_group_ids(self):
                return real.read_all_group_ids()

            def read_events(self, gid):
                return real.read_events(gid)

            def append_event(self, *a, **k):
                raise RuntimeError("disk full")

        result = run_eod_closure(
            broker=_Broker([[_pos()], []]),
            place_fn=lambda r: placed.append(r.client_order_id),
            run_async=asyncio.run, journal=_Unwritable(), journal_db_path=path,
            underlying="NIFTY", lot_size=75, session_id="S1", logger=LOG,
            max_attempts=2, clock=clock)

        assert placed == [], "an order was placed that the journal never recorded"
        assert result.state == STATE_UNFLATTENED
        assert result.flat is False
        assert "could not be journaled" in result.detail


class TestARetryNeverDoublesTheExit:
    def test_an_acked_unfilled_exit_is_not_re_sent_on_the_next_attempt(
            self, tmp_path):
        """The behaviour this wiring CHANGES, and the reason it changes.

        The loop used to re-send the residual every attempt. If the first
        order was still live at the venue with quantity unfilled, the second
        one over-exits -- and over-exiting a short OPENS A LONG.
        """
        clock = _Clock()
        placed = []
        result, _ = _run(
            _Broker([[_pos()], [_pos()], [_pos()]]),
            lambda r: (placed.append(r.client_order_id),
                       _Outcome(r.client_order_id, "PENDING", 0))[1],
            tmp_path, clock, attempts=3)

        assert len(placed) == 1, (
            f"the residual was exited {len(placed)} times while the first exit "
            f"was still live at the venue: {placed}")
        assert result.flat is False
        assert result.state == STATE_UNFLATTENED

    def test_a_rejected_exit_IS_retried_under_a_new_order_id(self, tmp_path):
        """The other half. Blocking a retry after a REJECTION would leave the
        position open, which is the opposite failure."""
        clock = _Clock()
        placed = []
        _run(_Broker([[_pos()], [_pos()], [_pos()]]),
             lambda r: (placed.append(r.client_order_id),
                        _Outcome(r.client_order_id, "REJECTED", 0))[1],
             tmp_path, clock, attempts=3)

        assert len(placed) == 3, f"a rejected exit was never retried: {placed}"
        assert len(set(placed)) == 3, (
            f"a retry re-used a rejected order id: {placed}")

    def test_a_send_that_raised_leaves_the_leg_unresolved_and_unrepeated(
            self, tmp_path):
        """An exception from the send says the CALL failed, not that the ORDER
        was never received. Re-sending on that basis is a guess."""
        clock = _Clock()
        placed = []

        def place(request):
            placed.append(request.client_order_id)
            raise RuntimeError("connection reset")

        result, journal = _run(_Broker([[_pos()], [_pos()], [_pos()]]),
                               place, tmp_path, clock, attempts=3)
        assert len(placed) == 1, f"an order of unknown fate was re-sent: {placed}"
        # No journal group claims this broker residual, so its attempt history
        # lives on the SESSION identity -- which is NOT a position group.
        scope = EL.session_scope_id("S1")
        _, unresolved = EL.outstanding(journal, scope)
        assert "NIFTY24000CE" in unresolved
        assert position_group_ids(journal) == [], (
            "flattening an orphan residual minted a position group")
        assert result.flat is False

    def test_a_flat_account_still_places_nothing(self, tmp_path):
        """POSITIVE CONTROL: the counts above must be able to be zero."""
        clock = _Clock()
        placed = []
        result, _ = _run(_Broker([[]]), lambda r: placed.append(r), tmp_path, clock)
        assert placed == []
        assert result.flat is True
