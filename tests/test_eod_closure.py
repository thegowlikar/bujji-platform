"""Session closure is broker-truth-driven, idempotent, and fail-closed.

WHAT THIS REPLACES. `_eod_close()` ran one management pass and then called
`run_market_close_sequence()` -- four lines that transition POSTMARKET then
COMPLETE. Nothing discovered broker positions, nothing cancelled working
orders, and nothing asked the broker whether the account was flat before the
session declared COMPLETE and the process exited. A position the management
pass did not close -- a rejected exit, a timed-out exit, or a position the
in-memory registry could not see -- carried overnight with nothing watching
it, and `finalize_session(final_positions=(), unrealized_pnl=0.0)` wrote a
summary.json asserting there was nothing open.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.eod_closure import (
    STATE_BROKER_TRUTH_UNKNOWN, STATE_COMPLETE, STATE_UNFLATTENED,
    build_flatten_request, discover_broker_positions, run_eod_closure)

LOG = logging.getLogger("eod-test")


def _pos(symbol, qty, side="SELL"):
    return {"symbol": symbol, "qty": qty, "side": side, "avg_price": 120.0}


class _Broker:
    """`sequence` is the list of position-lists returned by successive reads."""

    def __init__(self, sequence, fail_read=False, fail_cancel=False):
        self.seq = [list(s) for s in sequence] or [[]]
        self.n = 0
        self.fail_read = fail_read
        self.fail_cancel = fail_cancel
        self.placed = []
        self.cancelled = []

    async def get_open_positions(self):
        if self.fail_read:
            raise RuntimeError("broker unreachable")
        i = min(self.n, len(self.seq) - 1)
        self.n += 1
        return list(self.seq[i])

    async def get_order(self, coid):
        return OrderResult(coid, OrderStatus.UNKNOWN, message="not_found")

    async def cancel_order(self, coid):
        if self.fail_cancel:
            raise RuntimeError("cancel failed")
        self.cancelled.append(coid)
        return OrderResult(coid, OrderStatus.CANCELLED)


def _fills(broker):
    def place(request):
        broker.placed.append((request.contract.symbol, request.side.value, request.quantity))
        return OrderResult(request.client_order_id, OrderStatus.FILLED,
                           filled_quantity=request.quantity, average_price=1.0)
    return place


def _never_fills(broker):
    def place(request):
        broker.placed.append((request.contract.symbol, request.side.value, request.quantity))
        return OrderResult(request.client_order_id, OrderStatus.PENDING, filled_quantity=0)
    return place


def _close(broker, place, tmp_path, attempts=2, name="j.db"):
    path = str(tmp_path / name)
    journal = PositionGroupJournal(path)
    return run_eod_closure(
        broker=broker, place_fn=place, run_async=asyncio.run, journal=journal,
        journal_db_path=path, underlying="NIFTY", lot_size=75, session_id="S1",
        logger=LOG, max_attempts=attempts)


class TestCompleteRequiresProvenFlat:
    def test_a_flat_account_completes_without_placing_anything(self, tmp_path):
        b = _Broker([[]])
        r = _close(b, _fills(b), tmp_path)
        assert r.state == STATE_COMPLETE and r.flat is True and r.session_closed is True
        assert b.placed == []

    def test_a_position_that_flattens_completes(self, tmp_path):
        b = _Broker([[_pos("CE", 75)], []])
        r = _close(b, _fills(b), tmp_path)
        assert r.session_closed is True

    def test_an_unflattened_position_never_completes(self, tmp_path):
        b = _Broker([[_pos("CE", 75)]] * 3)
        r = _close(b, _never_fills(b), tmp_path)
        assert r.state == STATE_UNFLATTENED
        assert r.flat is False
        assert r.session_closed is False

    def test_a_partial_exit_leaves_the_residual_and_does_not_complete(self, tmp_path):
        b = _Broker([[_pos("CE", 75)], [_pos("CE", 25)], [_pos("CE", 25)]])
        r = _close(b, _fills(b), tmp_path)
        assert r.session_closed is False
        assert "CEx25" in r.detail


class TestUnknownIsNeverFlat:
    def test_a_failed_position_read_is_unknown_not_flat(self, tmp_path):
        b = _Broker([], fail_read=True)
        r = _close(b, _fills(b), tmp_path)
        assert r.state == STATE_BROKER_TRUTH_UNKNOWN
        assert r.flat is None
        assert r.session_closed is False

    def test_a_none_position_list_is_unknown(self):
        class _B:
            async def get_open_positions(self):
                return None
        assert discover_broker_positions(_B(), asyncio.run)[0] is None

    def test_a_malformed_row_is_unknown_not_flat(self):
        """A row we cannot parse is not evidence of an empty account."""
        class _B:
            async def get_open_positions(self):
                return [{"symbol": "CE", "qty": "not-a-number"}]
        assert discover_broker_positions(_B(), asyncio.run)[0] is None

    def test_session_closed_is_false_whenever_flat_is_not_true(self, tmp_path):
        b = _Broker([], fail_read=True)
        r = _close(b, _fills(b), tmp_path)
        assert r.session_closed is False


class TestResidualsComeFromTheBroker:
    def test_each_leg_gets_its_own_quantity(self, tmp_path):
        """75/25 must be exited 75/25. One leg's figure applied to another
        over-reduces, and over-reducing a short OPENS A LONG."""
        b = _Broker([[_pos("CE", 75), _pos("PE", 25)], []])
        _close(b, _fills(b), tmp_path)
        assert b.placed == [("CE", "BUY", 75), ("PE", "BUY", 25)]

    def test_only_live_legs_are_exited(self, tmp_path):
        b = _Broker([[_pos("CE", 75)], []])
        _close(b, _fills(b), tmp_path)
        assert b.placed == [("CE", "BUY", 75)]

    def test_a_long_position_is_closed_by_selling(self, tmp_path):
        b = _Broker([[_pos("CE", 75, "BUY")], []])
        _close(b, _fills(b), tmp_path)
        assert b.placed == [("CE", "SELL", 75)]

    def test_an_unknown_side_is_refused_never_guessed(self):
        """Guessing wrong DOUBLES the exposure instead of closing it."""
        with pytest.raises(ValueError, match="cannot determine exit side"):
            build_flatten_request(_pos("CE", 75, "???"), underlying="NIFTY",
                                  lot_size=75, client_order_id="X")

    def test_a_refused_leg_does_not_complete_the_session(self, tmp_path):
        b = _Broker([[_pos("CE", 75, "???")]] * 3)
        r = _close(b, _fills(b), tmp_path)
        assert r.session_closed is False
        assert b.placed == [], "an order was placed for a position whose side we could not read"


class TestIdempotence:
    def test_three_runs_against_a_flat_account_place_nothing(self, tmp_path):
        b = _Broker([[]])
        for i in range(3):
            r = _close(b, _fills(b), tmp_path, name=f"j{i}.db")
            assert r.state == STATE_COMPLETE
        assert b.placed == []

    def test_a_second_run_after_a_successful_flatten_places_nothing(self, tmp_path):
        b = _Broker([[_pos("CE", 75)], []])
        _close(b, _fills(b), tmp_path, name="a.db")
        before = len(b.placed)
        _close(b, _fills(b), tmp_path, name="b.db")
        assert len(b.placed) == before, "a second EOD run duplicated the exit"

    def test_each_attempt_cancels_before_discovering(self, tmp_path):
        """A fill landing while a cancel is in flight must appear in the
        residual, so discovery has to follow cancellation."""
        b = _Broker([[_pos("CE", 75)], []])
        r = _close(b, _fills(b), tmp_path)
        cancel_i = r.steps.index("attempt1:CANCEL_WORKING_ORDERS")
        discover_i = r.steps.index("attempt1:DISCOVER_BROKER_POSITIONS")
        assert cancel_i < discover_i

    def test_a_failed_cancel_does_not_stop_closure(self, tmp_path):
        b = _Broker([[_pos("CE", 75)], []], fail_cancel=True)
        r = _close(b, _fills(b), tmp_path)
        assert r.session_closed is True


class TestProductionWiring:
    """Rule 14: fail if someone later builds a second EOD path."""

    RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()

    def test_eod_close_calls_the_canonical_machine(self):
        i = self.RUNNER.index("def _eod_close")
        block = self.RUNNER[i:i + 1400]
        assert "self._run_eod_closure()" in block

    def test_complete_is_reachable_only_from_a_verified_flat(self):
        """run_market_close_sequence is what advances to COMPLETE. In the
        runner it must appear exactly twice -- the brake and EOD -- and each
        must be gated on proven flatness."""
        # Count the CALL form, not the bare name -- the bare name also
        # appears in comments explaining the defect this replaced.
        assert self.RUNNER.count(
            "self._trading_brain_runtime.run_market_close_sequence()") == 2
        i = self.RUNNER.index("def _run_eod_closure")
        block = self.RUNNER[i:i + 2600]
        gate = block.index("if result.session_closed:")
        call = block.index("run_market_close_sequence()")
        assert gate < call, "COMPLETE is reachable without a proven flat"

    def test_the_closure_uses_the_same_place_fn_as_entries_and_exits(self):
        i = self.RUNNER.index("def _run_eod_closure")
        assert "place_fn=self._exit_place_fn" in self.RUNNER[i:i + 2600]

    def test_a_raising_closure_fails_closed(self):
        i = self.RUNNER.index("def _run_eod_closure")
        block = self.RUNNER[i:i + 2600]
        assert "except Exception" in block
        assert '"session_closed"] = False' in block

    def test_finalization_no_longer_hardcodes_flat(self):
        assert "finalize_session(final_positions=(), realized_pnl=realized, unrealized_pnl=0.0)" \
            not in self.RUNNER
        assert "final_positions=final_positions" in self.RUNNER

    def test_finalization_reports_unknown_when_truth_was_not_established(self):
        i = self.RUNNER.index("def _session_archive")
        block = self.RUNNER[i:i + 2400]
        assert '"final_positions_status"] = "UNKNOWN"' in block


class TestFyersSchemaGate:
    """Rule 13: every safety property here rests on an unverified raw shape."""

    def test_the_gate_exists_and_is_false(self):
        """get_open_positions reads `netQty`/`netAvg` off each netPositions
        row. The top-level shape was confirmed against an EMPTY book; the
        per-row names have never been seen against a real open position.

        If `netQty` were named something else every row would read qty 0,
        every position would filter out as flat, and the account would look
        EMPTY -- a silent failure that manufactures flatness, which is the
        exact direction this whole layer exists to prevent."""
        from bujji.broker.fyers import FYERS_POSITION_SCHEMA_VERIFIED
        assert FYERS_POSITION_SCHEMA_VERIFIED is False, (
            "This may only be flipped by an operator observing a REAL open "
            "position and confirming the field names against the live payload "
            "-- never by reasoning about the code.")

    def test_the_broker_exposes_it(self):
        from bujji.broker.fyers import FyersBroker
        assert FyersBroker.position_schema_verified is False

    def test_the_unverified_names_are_still_the_ones_in_use(self):
        """If the reader ever stops using these, the gate above is stale."""
        import inspect
        from bujji.broker.fyers import FyersBroker
        src = inspect.getsource(FyersBroker.get_open_positions)
        assert "netQty" in src and "netAvg" in src
