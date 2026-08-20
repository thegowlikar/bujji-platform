"""Exits use the same safety machine as entries, and never fake certainty.

BYPASS AUDIT FINDING (2026-08-21). The entry path had been upgraded to
broker-truth execution; the exit path still called broker.place_order
directly -- no journal, no poll-to-terminal, no cancel-on-timeout. That is
strictly MORE dangerous than the entry case it mirrored, because an exit
failure happens while a naked position is already live.

Three concrete defects, all on the live exit path:

  1. `_aggregate_status` mapped PENDING and UNKNOWN to STATUS_REJECTED --
     telling the runner an exit FAILED while it was still working at the
     exchange. Re-submitting on that basis duplicates the exit; giving up on
     it abandons the position.
  2. `LIFECYCLE_ORDER_FILLED` was published unconditionally, immediately
     after place_order returned, without ever reading result.is_filled.
  3. Closure could be marked from a broker read taken while an exit order
     was still unsettled.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
from bujji.production_runtime.trade_lifecycle_executor import (
    STATUS_EXECUTED, STATUS_PARTIAL, STATUS_REJECTED, STATUS_UNKNOWN,
    TradeLifecycleExecutor)

_agg = TradeLifecycleExecutor._aggregate_status


def _r(status, qty=0):
    return OrderResult("X", status, filled_quantity=qty)


class TestUnknownIsNeverRejection:
    @pytest.mark.parametrize("status", [OrderStatus.PENDING, OrderStatus.UNKNOWN])
    def test_a_working_or_unknown_exit_is_not_reported_as_rejected(self, status):
        """THE defect. 'We do not know yet' read as 'the exit failed'."""
        assert _agg([_r(status)]) == STATUS_UNKNOWN

    def test_unknown_outranks_a_partial(self):
        """If any leg's state is unestablished, the GROUP's state is
        unestablished. PARTIAL would assert the rest is settled."""
        assert _agg([_r(OrderStatus.FILLED, 75), _r(OrderStatus.PENDING)]) == STATUS_UNKNOWN

    def test_unknown_outranks_a_rejection(self):
        assert _agg([_r(OrderStatus.REJECTED), _r(OrderStatus.UNKNOWN)]) == STATUS_UNKNOWN

    def test_unknown_is_not_executed(self):
        assert _agg([_r(OrderStatus.UNKNOWN)]) != STATUS_EXECUTED


class TestHonestStatusesSurvive:
    def test_all_filled_is_executed(self):
        assert _agg([_r(OrderStatus.FILLED, 75), _r(OrderStatus.FILLED, 75)]) == STATUS_EXECUTED

    def test_all_rejected_is_rejected(self):
        assert _agg([_r(OrderStatus.REJECTED), _r(OrderStatus.REJECTED)]) == STATUS_REJECTED

    def test_filled_plus_rejected_is_partial(self):
        assert _agg([_r(OrderStatus.FILLED, 75), _r(OrderStatus.REJECTED)]) == STATUS_PARTIAL

    def test_no_orders_is_rejected(self):
        """Nothing was submitted -- that is a genuine failure, not ambiguity."""
        assert _agg([]) == STATUS_REJECTED


class TestClosureRequiresBrokerTruth:
    """Behavioural, not source-text: what matters is whether mark_closed is
    actually reached, and a broker reporting no open leg is exactly the
    condition that makes this dangerous."""

    @staticmethod
    def _executor(place_result, monkeypatch):
        import asyncio

        closed = []

        class _Registry:
            async def positions_for_group(self, pg):
                return [{"symbol": "NSE:CE", "quantity": 75, "avg_price": 120.0,
                         "side": "SELL"}]

            def contract_for_symbol(self, pg, symbol):
                return object()

            async def get_group_reality(self, pg):
                # The broker reports NO open leg -- the exact reading that
                # would tempt a premature closure while an exit is unsettled.
                class _R:
                    is_open = False
                return _R()

        class _Lifecycle:
            def mark_closed(self, pg):
                closed.append(pg)

        exe = TradeLifecycleExecutor(
            broker=None, registry=_Registry(), lifecycle_runtime=_Lifecycle(),
            place_fn=lambda req: place_result)

        # build_reduce_order is exercised elsewhere; stub it so this test is
        # about the closure decision alone.
        import bujji.production_runtime.trade_lifecycle_executor as mod
        monkeypatch.setattr(mod, "build_reduce_order", lambda *a, **k: object())
        return exe, closed, asyncio

    def test_an_unknown_exit_never_marks_the_group_closed(self, monkeypatch):
        exe, closed, asyncio = self._executor(
            OrderResult("X", OrderStatus.PENDING, filled_quantity=0), monkeypatch)
        result = asyncio.run(exe._execute_reduce(
            "PG-1", "REDUCE", 75, lambda: __import__("datetime").datetime(2026, 8, 21)))
        assert result.status == STATUS_UNKNOWN
        assert closed == [], "a position was marked CLOSED on unresolved broker truth"

    def test_a_confirmed_filled_exit_does_mark_it_closed(self, monkeypatch):
        """The other half: the guard must not block a real closure."""
        exe, closed, asyncio = self._executor(
            OrderResult("X", OrderStatus.FILLED, filled_quantity=75, average_price=1.0),
            monkeypatch)
        result = asyncio.run(exe._execute_reduce(
            "PG-1", "REDUCE", 75, lambda: __import__("datetime").datetime(2026, 8, 21)))
        assert result.status == STATUS_EXECUTED
        assert closed == ["PG-1"]


class TestTelemetryMatchesReality:
    def test_filled_is_not_published_unconditionally(self):
        """It was: published straight after place_order returned, without
        ever reading result.is_filled. A rejected exit emitted 'FILLED'."""
        src = (REPO_ROOT / "bujji" / "production_runtime"
               / "trade_lifecycle_executor.py").read_text()
        assert 'LIFECYCLE_ORDER_UNFILLED' in src
        assert 'result.is_filled else "LIFECYCLE_ORDER_UNFILLED"' in src

    def test_no_unconditional_filled_publish_remains(self):
        src = (REPO_ROOT / "bujji" / "production_runtime"
               / "trade_lifecycle_executor.py").read_text()
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith('self._publish("LIFECYCLE_ORDER_FILLED"'):
                pytest.fail(f"unconditional FILLED publish still present: {stripped}")


class TestExitsUseTheEntryMachine:
    def test_the_executor_accepts_a_broker_truth_place_fn(self):
        import inspect
        sig = inspect.signature(TradeLifecycleExecutor.__init__)
        assert "place_fn" in sig.parameters

    def test_every_placement_goes_through_place(self):
        """No direct broker.place_order may remain on the exit path."""
        src = (REPO_ROOT / "bujji" / "production_runtime"
               / "trade_lifecycle_executor.py").read_text()
        offenders = [l.strip() for l in src.splitlines()
                     if "self._broker.place_order(" in l and "return await" not in l]
        assert offenders == [], f"direct broker placement remains: {offenders}"

    def test_the_runner_supplies_broker_truth_to_exits(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert "place_fn=_exit_place_fn" in src

    def test_the_default_preserves_prior_behaviour(self):
        """place_fn is optional so every existing construction site -- and
        every test built on them -- keeps its exact prior behaviour."""
        import inspect
        assert inspect.signature(
            TradeLifecycleExecutor.__init__).parameters["place_fn"].default is None
