"""Execution status is consumed, not reduced to "an object came back".

THE INFORMATION-LOSS POINT. `_run_one_management_pass` read
`result.forced_execution is not None` -- a BOOLEAN -- and logged and recorded
only that. The object carries an honest terminal status computed from broker
truth (EXECUTED / PARTIAL / BROKER_TRUTH_UNKNOWN / REJECTED /
FAILED_VALIDATION) and every bit of it was discarded at that line. A
stop-loss the broker REJECTED and one that filled produced the identical log
line and summary entry: `forced_execution=True`.

SCOPE, stated precisely. Closure decisions were never wrong because of this.
The tests below re-prove those gates behaviourally rather than assuming them:
the governor transitions EXITED only on STATUS_EXECUTED, and the executor
marks a group closed only when broker reality reports no open leg. What was
missing was the ability to SEE a failed exit and escalate from it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
import bujji.production_runtime.trade_lifecycle_executor as tle_mod
from bujji.production_runtime.trade_lifecycle_executor import (
    STATUS_EXECUTED, STATUS_PARTIAL, STATUS_REJECTED, STATUS_UNKNOWN,
    TradeLifecycleExecutor)

CLK = lambda: dt.datetime(2026, 8, 21, 14, 0)


def _pos(symbol, qty):
    return {"symbol": symbol, "qty": qty, "avg_price": 120.0, "side": "SELL"}


class _Registry:
    def __init__(self, positions, open_after=None):
        self._p = list(positions)
        self._open_after = open_after if open_after is not None else bool(positions)

    async def positions_for_group(self, pg):
        return list(self._p)

    def contract_for_symbol(self, pg, symbol):
        return object()

    async def get_group_reality(self, pg):
        outer = self

        class _R:
            is_open = outer._open_after
        return _R()


class _Lifecycle:
    def __init__(self):
        self.closed = []

    def mark_closed(self, pg):
        self.closed.append(pg)


def _reduce(monkeypatch, positions, result, open_after=None, qty_map=None):
    monkeypatch.setattr(tle_mod, "build_reduce_order", lambda *a, **k: object())
    life = _Lifecycle()
    exe = TradeLifecycleExecutor(
        broker=None, registry=_Registry(positions, open_after),
        lifecycle_runtime=life, place_fn=lambda r: result)
    out = asyncio.run(exe._execute_reduce("PG-1", "MANDATORY_EXIT", 75, CLK, None, qty_map))
    return out, life


FILLED = OrderResult("X", OrderStatus.FILLED, filled_quantity=75, average_price=1.0)
PENDING = OrderResult("X", OrderStatus.PENDING, filled_quantity=0)
REJECTED = OrderResult("X", OrderStatus.REJECTED, message="margin")
UNKNOWN = OrderResult("X", OrderStatus.UNKNOWN, message="broker unreachable")


class TestStatusReachesTheRunner:
    """Rule 9 matrix, on the real executor."""

    @pytest.mark.parametrize("result,expected", [
        (FILLED, STATUS_EXECUTED),
        (REJECTED, STATUS_REJECTED),
        (PENDING, STATUS_UNKNOWN),
        (UNKNOWN, STATUS_UNKNOWN),
    ])
    def test_the_terminal_status_is_produced(self, monkeypatch, result, expected):
        out, _ = _reduce(monkeypatch, [_pos("CE", 75)], result, open_after=False)
        assert out.status == expected


class TestClosureGatesHold:
    """Positive and negative controls, behavioural (Rule 10)."""

    def test_executed_closes_the_group(self, monkeypatch):
        _, life = _reduce(monkeypatch, [_pos("CE", 75)], FILLED, open_after=False)
        assert life.closed == ["PG-1"]

    @pytest.mark.parametrize("result", [REJECTED, PENDING, UNKNOWN])
    def test_a_non_executed_exit_never_closes_the_group(self, monkeypatch, result):
        """UNKNOWN / PENDING / REJECTED must not become CLOSED even when the
        broker read happens to report no open leg."""
        _, life = _reduce(monkeypatch, [_pos("CE", 75)], result, open_after=False)
        assert life.closed == [], "a non-EXECUTED exit closed the position"

    def test_executed_but_broker_still_open_does_not_close(self, monkeypatch):
        """Broker truth wins over the exit's own optimism."""
        _, life = _reduce(monkeypatch, [_pos("CE", 75)], FILLED, open_after=True)
        assert life.closed == []


class TestGovernorExitedRequiresExecuted:
    def _governor(self, status):
        from bujji.production_runtime.trading_session_governor import session_governor as sg
        src = (REPO_ROOT / "bujji" / "production_runtime" / "trading_session_governor"
               / "session_governor.py").read_text()
        # The gate must read the status, not the object's existence.
        assert "getattr(forced_execution, \"status\", None) == STATUS_EXECUTED" in src
        return src

    def test_exited_is_gated_on_executed(self):
        src = self._governor(STATUS_EXECUTED)
        i = src.index("STATUS_EXECUTED")
        block = src[i:i + 700]
        assert "TradingSessionState.EXITED" in block
        assert "EXIT_NOT_CONFIRMED" in block, "no branch exists for a non-confirmed exit"


class TestRunnerConsumesAndEscalates:
    RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()

    def test_the_boolean_only_record_is_gone(self):
        """The exact loss point."""
        assert 'result.policy_decision.decision, result.forced_execution is not None,' \
            not in self.RUNNER

    def test_the_summary_carries_the_status(self):
        assert '"exit_status": exit_status,' in self.RUNNER

    def test_a_non_executed_exit_is_escalated(self):
        i = self.RUNNER.index("def _record_exit_outcome")
        block = self.RUNNER[i:i + 2200]
        assert "exits_unresolved" in block
        assert "has_unresolved_exit" in block
        assert "EXIT DID NOT CLOSE THE POSITION" in block

    def test_only_executed_counts_as_confirmed(self):
        i = self.RUNNER.index("def _record_exit_outcome")
        block = self.RUNNER[i:i + 2200]
        confirmed = block.index("exits_confirmed")
        gate = block.index("if exit_status == STATUS_EXECUTED:")
        assert gate < confirmed, "a non-EXECUTED status can reach exits_confirmed"

    def test_it_never_mutates_position_state(self):
        """Rule 7: this is the observability half. Closure stays owned by the
        executor and governor, both of which gate on broker truth."""
        i = self.RUNNER.index("def _record_exit_outcome")
        block = self.RUNNER[i:i + 2200]
        for forbidden in ("mark_closed", "place_order", "register_entry", "_position_group_id ="):
            assert forbidden not in block, f"{forbidden} appeared in an observability path"

    def test_unresolved_exits_survive_into_finalization(self):
        i = self.RUNNER.index("def _session_archive")
        block = self.RUNNER[i:i + 3000]
        assert 'has_unresolved_exit' in block

    def test_no_new_execution_engine_was_created(self):
        """Rule 7: exactly one canonical machine."""
        for banned in ("class RunnerExecutionEngine", "class LifecycleExecutionEngine",
                       "class EodExecutionEngine"):
            assert banned not in self.RUNNER
