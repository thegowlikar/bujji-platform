"""The lifecycle executor must journal an exit before it can place one.

This is the SECOND placement site. Until it was wired it called
`self._place(order_request)` with nothing in the journal naming the order,
under a timestamp-derived client_order_id -- the same defect the EOD path had,
serving strategy exit and emergency close.
"""
import ast
import asyncio
import datetime
import itertools
import logging
import pathlib

import pytest

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime import exit_lifecycle as EL
from bujji.production_runtime.position_group_scope import position_group_ids
from bujji.production_runtime.trade_lifecycle_executor import (
    STATUS_FAILED_VALIDATION, TradeLifecycleExecutor)
from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
    ACTION_REDUCE_SIZE)

LOG = logging.getLogger("test-executor-exits")
PG = "PG:EXPOSURE-1"
CONTRACT = "NIFTY24000CE"
COID = "ENTRY-A"
QTY = 50


class _Clock:
    def __init__(self):
        self._n = itertools.count()

    def __call__(self):
        return (datetime.datetime(2026, 8, 23, 15, 0, tzinfo=datetime.timezone.utc)
                + datetime.timedelta(seconds=next(self._n)))


class _Recommendation:
    action = ACTION_REDUCE_SIZE


class _Evaluation:
    position_group_id = PG
    recommendation = _Recommendation()


class _Registry:
    async def positions_for_group(self, pg_id):
        return [{"symbol": CONTRACT, "qty": QTY, "side": "SELL",
                 "avg_price": 100.0}]

    def contract_for_symbol(self, pg_id, symbol):
        class _C:
            pass
        c = _C()
        c.symbol = symbol
        return c

    async def get_group_reality(self, pg_id):
        class _R:
            is_confirmed_flat = False
        return _R()


class _LifecycleRuntime:
    def __init__(self):
        self.closed = []

    def mark_closed(self, pg_id):
        self.closed.append(pg_id)


@pytest.fixture
def clock():
    return _Clock()


@pytest.fixture
def journal(tmp_path, clock):
    j = PositionGroupJournal(str(tmp_path / "pg.db"))
    j.append_event(PG, "MINTED", f"{PG}:M",
                   {"plan_id": "P1", "strategy_id": "ic", "underlying": "NIFTY"},
                   clock=clock)
    j.append_event(PG, "CONSTRUCTED", f"{PG}:C",
                   {"contract_client_order_map": {CONTRACT: COID},
                    "requested_quantities": {COID: QTY}}, clock=clock)
    j.append_event(PG, "SUBMIT_INTENT", f"{PG}:SI", {"client_order_id": COID},
                   clock=clock)
    j.append_event(PG, "SUBMIT_ACK", f"{PG}:SA",
                   {"client_order_id": COID, "broker_order_id": "B",
                    "broker_reported_status": "T"}, clock=clock)
    j.append_event(PG, "FILL_OBSERVED", f"{PG}:FO",
                   {"client_order_id": COID, "cumulative_filled_quantity_after": QTY,
                    "cumulative_average_fill_price_after": 100.0,
                    "delta_quantity": QTY, "delta_value": 5000.0,
                    "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
                   clock=clock)
    return j


def _executor(journal, place_fn, session_id="S1"):
    return TradeLifecycleExecutor(
        broker=None, registry=_Registry(), lifecycle_runtime=_LifecycleRuntime(),
        place_fn=place_fn, journal=journal, session_id=session_id, logger=LOG)


def _reduce(executor, clock, cause=EL.CAUSE_STRATEGY, quantity=QTY):
    return asyncio.run(executor.execute(
        _Evaluation(), clock, reduce_quantity=quantity,
        quantity_by_symbol={CONTRACT: quantity}, cause=cause))


def _filled(request):
    return OrderResult(request.client_order_id, OrderStatus.FILLED,
                       filled_quantity=QTY, average_price=101.0)


def _pending(request):
    return OrderResult(request.client_order_id, OrderStatus.PENDING,
                       filled_quantity=0)


def _rejected(request):
    return OrderResult(request.client_order_id, OrderStatus.REJECTED,
                       filled_quantity=0)


class TestIntentPrecedesPlacement:
    def test_the_order_is_journaled_when_place_is_called(self, journal, clock):
        """POSITIVE CONTROL, checked from INSIDE the placement -- the only
        moment at which the ordering matters."""
        seen = {}

        def place(request):
            seen[request.client_order_id] = {
                h["broker_client_order_id"]
                for h in EL.attempt_history(journal, PG)}
            return _filled(request)

        _reduce(_executor(journal, place), clock)
        assert seen, "place_fn was never called; this test proves nothing"
        for coid, journaled in seen.items():
            assert coid in journaled, (
                f"{coid} was placed with no attempt record in the journal")

    def test_a_refused_intent_places_nothing(self, journal, clock):
        placed = []

        class _Unwritable:
            def read_all_group_ids(self):
                return journal.read_all_group_ids()

            def read_events(self, gid):
                return journal.read_events(gid)

            def append_event(self, *a, **k):
                raise RuntimeError("disk full")

        result = _reduce(_executor(_Unwritable(), lambda r: placed.append(r)), clock)
        assert placed == [], "an order was placed that the journal never recorded"
        assert result.status == STATUS_FAILED_VALIDATION
        assert "could not be journaled" in result.reason

    def test_the_order_id_comes_from_the_journaled_attempt(self, journal, clock):
        placed = []
        _reduce(_executor(journal, lambda r: (placed.append(r.client_order_id),
                                              _filled(r))[1]), clock)
        attempts = {h["broker_client_order_id"]
                    for h in EL.attempt_history(journal, PG)}
        assert placed and placed[0] in attempts
        assert "REDUCE" not in placed[0], (
            "the old timestamp-derived id is still being used, so the "
            "journaled attempt is not what was actually sent")


class TestExitsCreateNoExposure:
    def test_repeated_exits_create_no_position_groups(self, journal, clock):
        for _ in range(4):
            _reduce(_executor(journal, _rejected), clock)
        assert position_group_ids(journal) == [PG]

    def test_a_rejected_exit_is_retried_under_a_new_id(self, journal, clock):
        placed = []

        def place(request):
            placed.append(request.client_order_id)
            return _rejected(request)

        for _ in range(3):
            _reduce(_executor(journal, place), clock)
        assert len(placed) == 3, f"a rejected exit was never retried: {placed}"
        assert len(set(placed)) == 3, f"a retry re-used a rejected id: {placed}"

    def test_an_acked_unfilled_exit_is_not_re_sent(self, journal, clock):
        placed = []

        def place(request):
            placed.append(request.client_order_id)
            return _pending(request)

        for _ in range(3):
            _reduce(_executor(journal, place), clock)
        assert len(placed) == 1, (
            f"the exit was sent {len(placed)} times while the first was still "
            f"live at the venue: {placed}")

    def test_a_send_that_raised_leaves_the_attempt_unresolved(self, journal, clock):
        placed = []

        def place(request):
            placed.append(request.client_order_id)
            raise RuntimeError("connection reset")

        with pytest.raises(RuntimeError):
            _reduce(_executor(journal, place), clock)
        _, unresolved = EL.outstanding(journal, PG)
        assert CONTRACT in unresolved

        # The retry does not raise, because it never reaches the broker: the
        # leg is refused before placement. That is the point -- an order whose
        # outcome nobody knows must not be re-sent.
        _reduce(_executor(journal, place), clock)
        assert len(placed) == 1, f"an order of unknown fate was re-sent: {placed}"


class TestEveryCauseSharesOnePath:
    @pytest.mark.parametrize("cause", EL.ALL_CAUSES)
    def test_each_cause_journals_its_own_name(self, journal, clock, cause):
        _reduce(_executor(journal, _rejected), clock, cause=cause)
        assert {h["cause"] for h in EL.attempt_history(journal, PG)} == {cause}

    def test_an_emergency_close_does_not_re_exit_what_a_strategy_exit_acked(
            self, journal, clock):
        placed = []

        def place(request):
            placed.append(request.client_order_id)
            return _pending(request)

        _reduce(_executor(journal, place), clock, cause=EL.CAUSE_STRATEGY)
        _reduce(_executor(journal, place), clock, cause=EL.CAUSE_EMERGENCY)
        assert len(placed) == 1, (
            "the emergency path re-sent an exit the strategy path already had "
            "live at the venue")

    def test_an_EOD_close_sees_what_the_executor_already_sent(self, journal, clock):
        """The two placement sites share ONE attempt history, so neither can
        be blind to the other's live orders."""
        _reduce(_executor(journal, _pending), clock, cause=EL.CAUSE_STRATEGY)
        eod = EL.plan(journal, session_id="S1", cause=EL.CAUSE_EOD,
                      holdings=[(PG, CONTRACT, QTY)], broker_truth=None,
                      clock=clock)
        assert eod.legs == (), (
            "EOD planned an exit for a leg the executor already has live")

    def test_a_fill_through_the_executor_reduces_the_exposure_group(
            self, journal, clock):
        """POSITIVE CONTROL: exposure must still be able to move."""
        from bujji.trading_brain.risk_governor.position_group_fold import (
            fold, net_quantity)
        _reduce(_executor(journal, _filled), clock)
        state = fold(journal.read_events(PG))
        assert net_quantity(state.legs[COID]) == 0
        assert position_group_ids(journal) == [PG]


class TestTheProductionRunnerSuppliesTheJournal:
    def test_the_runner_constructs_the_executor_with_a_journal(self):
        """RATCHET. The journal is an optional constructor argument so existing
        construction sites keep their behaviour -- which means the production
        wiring can silently regress to unjournaled placement. Asserted by AST,
        not by substring, so a comment mentioning it cannot satisfy this.
        """
        source = pathlib.Path("bujji_options_os_runner.py").read_text()
        calls = [n for n in ast.walk(ast.parse(source))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "TradeLifecycleExecutor"]
        assert calls, "the runner no longer constructs a TradeLifecycleExecutor"
        for call in calls:
            supplied = {kw.arg for kw in call.keywords}
            missing = {"journal", "session_id"} - supplied
            assert not missing, (
                f"the runner builds the lifecycle executor without {missing} -- "
                f"its exits would be placed with nothing in the journal naming "
                f"them, which is the defect M4b closes")

    def test_an_executor_without_a_journal_still_places_but_says_so(
            self, journal, clock, caplog):
        """The fallback is deliberate -- existing construction sites must keep
        working -- but it must be LOUD, not silent."""
        placed = []
        executor = TradeLifecycleExecutor(
            broker=None, registry=_Registry(),
            lifecycle_runtime=_LifecycleRuntime(),
            place_fn=lambda r: (placed.append(r.client_order_id), _filled(r))[1],
            journal=None, session_id=None, logger=LOG)
        with caplog.at_level(logging.CRITICAL):
            asyncio.run(executor.execute(_Evaluation(), clock,
                                         reduce_quantity=QTY,
                                         quantity_by_symbol={CONTRACT: QTY}))
        assert placed, "the fallback path stopped placing entirely"
        assert any("WITHOUT A JOURNAL" in r.message for r in caplog.records), (
            "unjournaled placement happened silently")
