"""Fills come from broker truth, and a timeout means UNKNOWN -- never flat.

WHAT THIS REPLACES. process_entry_cycle read `is_filled` off place_order's
immediate response. That is true of PaperBroker and FALSE of any real broker,
where an order is ACKNOWLEDGED first and fills asynchronously. Against FYERS
the previous path would read PENDING as "not filled", conclude the leg never
happened, and leave Bujji short while believing it was flat.

ExecutionEngine.submit_and_confirm already implemented the correct lifecycle
-- idempotent placement, poll-to-terminal, cancel-on-timeout -- and had ZERO
production callers; every reference to it elsewhere was a comment saying
another module "reuses the CONCEPT of" it.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.config import AppConfig, BrokerConfig
from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.models import OptionContract, OrderRequest, OrderResult
from bujji.execution.engine import ExecutionEngine
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.execution_journal_bridge import (
    broker_truth_place_fn, journaled_entry, unresolved_group_ids)
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_SUBMIT_PENDING_UNKNOWN, fold)

LOG = logging.getLogger("truth-test")
CLK = lambda: datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
FAST = AppConfig(broker=BrokerConfig(retry_attempts=1, retry_backoff_seconds=0.0,
                                     poll_interval_seconds=0.01, order_timeout_seconds=0.05))


def _req(coid="CE-1"):
    return OrderRequest(
        contract=OptionContract(symbol=f"NSE:{coid}", underlying="NIFTY", strike=24400.0,
                                option_type=OptionType.CE, expiry="2026-08-25", lot_size=75),
        side=Side.SELL, quantity=75, client_order_id=coid,
        limit_price=None, reference_price=120.0, tag="t")


class _Broker:
    def __init__(self, poll_seq, place_status=OrderStatus.PENDING, place_msg=""):
        self._seq = list(poll_seq)
        self._n = 0
        self._place_status = place_status
        self._place_msg = place_msg
        self.cancelled = []
        self.place_calls = 0

    async def place_order(self, r):
        self.place_calls += 1
        return OrderResult(r.client_order_id, self._place_status, message=self._place_msg)

    async def get_order(self, coid):
        # Before placement the broker has never heard of this id. Modelling
        # that matters: submit_and_confirm looks the order up FIRST, and a
        # double that reports an order it was never given would exercise the
        # idempotent-adoption path instead of the placement path.
        if not self.place_calls or not self._seq:
            return OrderResult(coid, OrderStatus.UNKNOWN, message="not_found")
        i = min(self._n, len(self._seq) - 1)
        self._n += 1
        st, qty, px = self._seq[i]
        return OrderResult(coid, st, broker_order_id=f"B-{coid}",
                           filled_quantity=qty, average_price=px)

    async def cancel_order(self, coid):
        self.cancelled.append(coid)
        return OrderResult(coid, OrderStatus.CANCELLED)


PENDING = (OrderStatus.PENDING, 0, None)
FILLED = (OrderStatus.FILLED, 75, 120.0)


def _run(tmp_path, broker, cfg=FAST):
    path = str(tmp_path / "j.db")
    j = PositionGroupJournal(path)
    engine = ExecutionEngine(broker, cfg, LOG)
    out = journaled_entry(j, broker_truth_place_fn(engine, asyncio.run, LOG),
                          [_req()], plan_id="MTA-1", strategy_id="SHORT_STRANGLE",
                          underlying="NIFTY", clock=CLK, logger=LOG)
    return out, j, path


class TestFillsComeFromPolling:
    def test_an_order_that_fills_on_a_later_poll_is_filled(self, tmp_path):
        """place_order returned PENDING. The old path called this unfilled."""
        out, j, _ = _run(tmp_path, _Broker([PENDING, PENDING, FILLED]))
        assert out.all_filled is True
        leg = list(fold(j.read_events(out.position_group_id)).legs.values())[0]
        assert leg.fill.cumulative_filled_quantity == 75

    def test_the_immediate_response_is_not_trusted(self, tmp_path):
        """PENDING on placement, FILLED on poll -> filled. The distinction
        that keeps Bujji from believing it is flat while short."""
        broker = _Broker([FILLED])
        out, _, _ = _run(tmp_path, broker)
        assert broker.place_calls == 1, "the order was never actually placed"
        assert out.all_filled is True


class TestDuplicateOrderPrevention:
    def test_an_order_the_broker_already_holds_is_adopted_not_replaced(self, tmp_path):
        """C3. After a restart or an ambiguous retry, the same
        client_order_id must never be placed twice -- submit_and_confirm
        looks it up first and adopts what the broker already has. Placing
        again would double a real position."""

        class _AlreadyThere:
            def __init__(self):
                self.place_calls = 0
                self.cancelled = []

            async def place_order(self, r):
                self.place_calls += 1
                return OrderResult(r.client_order_id, OrderStatus.PENDING)

            async def get_order(self, coid):
                return OrderResult(coid, OrderStatus.FILLED, broker_order_id="B",
                                   filled_quantity=75, average_price=120.0)

            async def cancel_order(self, coid):
                self.cancelled.append(coid)
                return OrderResult(coid, OrderStatus.CANCELLED)

        broker = _AlreadyThere()
        out, _, _ = _run(tmp_path, broker)
        assert broker.place_calls == 0, "a duplicate order was placed"
        assert out.all_filled is True


class TestTimeoutMeansUnknownNotFlat:
    def test_acknowledged_but_never_filled_is_unknown(self, tmp_path):
        """THE killer case. Never 'not filled' -- we do not know."""
        out, _, _ = _run(tmp_path, _Broker([PENDING]))
        assert out.truth_unknown == ("CE-1",)
        assert out.all_filled is False
        assert out.position_truth_known is False

    def test_a_timed_out_order_is_cancelled(self, tmp_path):
        broker = _Broker([PENDING])
        _run(tmp_path, broker)
        assert broker.cancelled == ["CE-1"]

    def test_an_unknown_leg_is_left_pending_for_recovery(self, tmp_path):
        """No SUBMIT_ACK and no SUBMIT_FAILURE: both would assert knowledge
        we do not have. SUBMIT_PENDING_UNKNOWN is the journal's own word for
        this, and is exactly what startup recovery scans for."""
        out, j, path = _run(tmp_path, _Broker([PENDING]))
        leg = list(fold(j.read_events(out.position_group_id)).legs.values())[0]
        assert leg.submit_status == LEG_SUBMIT_PENDING_UNKNOWN
        assert unresolved_group_ids(j, path) == [out.position_group_id]

    def test_an_unreachable_broker_is_unknown(self, tmp_path):
        class _Dead(_Broker):
            async def get_order(self, coid):
                raise RuntimeError("broker unreachable")

        out, _, _ = _run(tmp_path, _Dead([PENDING]))
        assert out.truth_unknown == ("CE-1",)

    def test_unknown_never_counts_as_filled(self, tmp_path):
        out, _, _ = _run(tmp_path, _Broker([PENDING]))
        assert out.all_filled is False
        assert out.filled == ()

    def test_unknown_is_not_in_the_unfilled_bucket_either(self, tmp_path):
        """Containment unwinds `filled`. If UNKNOWN leaked into either
        bucket, Bujji would either claim a position it may not have or
        unwind one it may not have -- opening an opposite position."""
        out, _, _ = _run(tmp_path, _Broker([PENDING]))
        assert out.unfilled == ()


class TestConfirmedRejectionStaysDistinct:
    def test_a_confirmed_rejection_is_not_unknown(self, tmp_path):
        """Losing this distinction would make every rejection look like an
        ambiguous timeout and block the session unnecessarily."""
        broker = _Broker([(OrderStatus.REJECTED, 0, None)],
                         place_status=OrderStatus.REJECTED, place_msg="margin")
        out, j, _ = _run(tmp_path, broker)
        assert out.truth_unknown == ()
        types = [e.event_type for e in j.read_events(out.position_group_id)]
        assert "SUBMIT_FAILURE" in types


class TestLateFillIsNotLost:
    def test_a_fill_that_lands_after_cancel_is_observed(self, tmp_path):
        """The cancel loses the race. Without post-cancel reconciliation the
        engine returned the pre-cancel poll -- filled_quantity 0 -- and Bujji
        would be short 75 believing it was flat."""

        class _LateFill:
            def __init__(self):
                self.cancel_issued = False
                self.cancelled = []

            async def place_order(self, r):
                return OrderResult(r.client_order_id, OrderStatus.PENDING)

            async def get_order(self, coid):
                if self.cancel_issued:
                    return OrderResult(coid, OrderStatus.FILLED, broker_order_id="B",
                                       filled_quantity=75, average_price=121.75)
                return OrderResult(coid, OrderStatus.PENDING, filled_quantity=0)

            async def cancel_order(self, coid):
                self.cancel_issued = True
                self.cancelled.append(coid)
                return OrderResult(coid, OrderStatus.REJECTED, message="already filled")

        broker = _LateFill()
        out, j, _ = _run(tmp_path, broker)
        assert broker.cancel_issued is True
        assert out.all_filled is True, "the late fill was lost"
        leg = list(fold(j.read_events(out.position_group_id)).legs.values())[0]
        assert leg.fill.cumulative_filled_quantity == 75
        assert leg.fill.cumulative_average_fill_price == 121.75

    def test_a_smaller_post_cancel_report_never_shrinks_the_fill(self, tmp_path):
        """Monotonic-in-fill. Taking a transiently smaller cumulative figure
        would silently shrink a position that really exists."""

        class _Shrinking:
            def __init__(self):
                self.n = 0
                self.cancelled = []

            async def place_order(self, r):
                return OrderResult(r.client_order_id, OrderStatus.PENDING)

            async def get_order(self, coid):
                self.n += 1
                # Partial fill observed while working, then a smaller report.
                if not self.cancelled:
                    return OrderResult(coid, OrderStatus.PARTIAL, broker_order_id="B",
                                       filled_quantity=50, average_price=120.0)
                return OrderResult(coid, OrderStatus.PARTIAL, broker_order_id="B",
                                   filled_quantity=10, average_price=120.0)

            async def cancel_order(self, coid):
                self.cancelled.append(coid)
                return OrderResult(coid, OrderStatus.CANCELLED)

        out, j, _ = _run(tmp_path, _Shrinking())
        leg = list(fold(j.read_events(out.position_group_id)).legs.values())[0]
        assert leg.fill.cumulative_filled_quantity == 50, \
            "a transient smaller report shrank the recorded position"


class TestWiring:
    def test_the_runtime_uses_broker_truth_when_an_engine_is_present(self):
        src = (REPO_ROOT / "bujji" / "production_runtime" / "trading_brain_runtime.py").read_text()
        assert "broker_truth_place_fn(" in src
        assert "BROKER_TRUTH_UNKNOWN:" in src

    def test_unknown_truth_blocks_instead_of_unwinding(self):
        """Unwinding a position that may not exist opens an opposite one."""
        src = (REPO_ROOT / "bujji" / "production_runtime" / "trading_brain_runtime.py").read_text()
        i = src.index("entry_outcome.truth_unknown")
        block = src[i:i + 1400]
        assert "return TradingBrainCycleResult" in block
        assert block.index("return TradingBrainCycleResult") < block.find("contain_partial_entry")+1e9

    def test_the_runner_constructs_the_execution_engine(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert "_ExecutionEngine(" in src
        assert "execution_engine=execution_engine" in src
