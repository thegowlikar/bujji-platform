"""The emergency brake actually reduces risk, and never over-reduces.

WHAT THE AUDIT FOUND (2026-08-21, 37-agent fan-out, adversarially verified).
The daily-loss / blind-cycle brake -- the last-resort capital protection --
set a flag, called TradingBrainRuntime.run_market_close_sequence() and
returned. That method is four lines: transition(POSTMARKET),
transition(COMPLETE). It places NO order. The in-code comment claimed it
"reus[ed] the same mandatory close-everything sequence _eod_close uses"; the
brake called only the half that fires nothing, and its `return` skipped
evaluate_and_enforce_exit -- the only call on that path that submits.

Net effect on the exact event the brake exists for: position left OPEN,
nothing submitted, "EMERGENCY CLOSE" logged. And on the second firing the
transition from COMPLETE (no outgoing edges) raised IllegalRuntimeTransition,
killing _session_archive.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import inspect
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OrderStatus
from bujji.core.models import OrderResult
import bujji.production_runtime.trade_lifecycle_executor as tle_mod
from bujji.production_runtime.trade_lifecycle_executor import (
    STATUS_EXECUTED, STATUS_REJECTED, STATUS_UNKNOWN, TradeLifecycleExecutor)

CLK = lambda: dt.datetime(2026, 8, 21, 15, 0)


def _pos(symbol, qty):
    return {"symbol": symbol, "qty": qty, "avg_price": 120.0, "side": "SELL"}


class _Registry:
    def __init__(self, positions):
        self._p = list(positions)

    async def positions_for_group(self, pg):
        return list(self._p)

    def contract_for_symbol(self, pg, symbol):
        return object()

    async def get_group_reality(self, pg):
        class _R:
            is_open = bool([p for p in self._p if p["qty"] > 0])
        return _R()


class _Lifecycle:
    def __init__(self):
        self.closed = []

    def mark_closed(self, pg):
        self.closed.append(pg)


def _reduce(monkeypatch, positions, result, quantity_by_symbol=None, reduce_qty=75):
    sent = []

    def _build(position, contract, quantity, coid, reference_price=None):
        sent.append((position["symbol"], quantity))
        return object()

    monkeypatch.setattr(tle_mod, "build_reduce_order", _build)
    exe = TradeLifecycleExecutor(broker=None, registry=_Registry(positions),
                                 lifecycle_runtime=_Lifecycle(),
                                 place_fn=lambda r: result)
    out = asyncio.run(exe._execute_reduce("PG-1", "MANDATORY_EXIT", reduce_qty,
                                          CLK, None, quantity_by_symbol))
    return out, sent


FILLED = OrderResult("X", OrderStatus.FILLED, filled_quantity=75, average_price=1.0)
PENDING = OrderResult("X", OrderStatus.PENDING, filled_quantity=0)
REJECTED = OrderResult("X", OrderStatus.REJECTED, message="margin")


class TestNeverOverReduces:
    def test_each_leg_is_sent_its_own_broker_quantity(self, monkeypatch):
        """Over-reducing a short does not stop at zero -- it OPENS A LONG."""
        _, sent = _reduce(monkeypatch, [_pos("CE", 75), _pos("PE", 25)], FILLED,
                          {"CE": 75, "PE": 25})
        assert sent == [("CE", 75), ("PE", 25)]

    def test_an_already_flat_leg_is_skipped_not_sent(self, monkeypatch):
        """Sending the requested figure to a flat leg opens the opposite side."""
        _, sent = _reduce(monkeypatch, [_pos("CE", 75), _pos("PE", 0)], FILLED,
                          {"CE": 75, "PE": 0})
        assert sent == [("CE", 75)]

    def test_the_reduction_never_exceeds_its_mandate(self, monkeypatch):
        """Broker holds more than we were authorised to reduce -- reduce only
        what was authorised."""
        _, sent = _reduce(monkeypatch, [_pos("CE", 200)], FILLED, {"CE": 200}, reduce_qty=75)
        assert sent == [("CE", 75)]

    def test_prior_behaviour_is_preserved_without_the_map(self, monkeypatch):
        """quantity_by_symbol is optional; every existing caller is unchanged."""
        _, sent = _reduce(monkeypatch, [_pos("CE", 75), _pos("PE", 75)], FILLED, None)
        assert sent == [("CE", 75), ("PE", 75)]


class TestForcedExitStatusIsHonest:
    def test_a_filled_flatten_is_executed(self, monkeypatch):
        out, _ = _reduce(monkeypatch, [_pos("CE", 75)], FILLED, {"CE": 75})
        assert out.status == STATUS_EXECUTED

    def test_a_rejected_flatten_is_rejected(self, monkeypatch):
        out, _ = _reduce(monkeypatch, [_pos("CE", 75)], REJECTED, {"CE": 75})
        assert out.status == STATUS_REJECTED

    def test_a_working_flatten_is_unknown_not_rejected(self, monkeypatch):
        out, _ = _reduce(monkeypatch, [_pos("CE", 75)], PENDING, {"CE": 75})
        assert out.status == STATUS_UNKNOWN


class TestTheBrakeSubmits:
    def test_the_brake_calls_the_order_firing_path(self):
        """It called run_market_close_sequence -- two state transitions."""
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        i = src.index("if brake_reason is not None:")
        block = src[i:i + 500]
        assert "_execute_emergency_close(" in block
        assert "run_market_close_sequence()" not in block

    def test_the_emergency_close_forces_an_exit(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        i = src.index("def _execute_emergency_close")
        block = src[i:i + 2600]
        assert "evaluate_and_enforce_exit(" in block
        assert "force_exit_reason=brake_reason" in block

    def test_the_governor_accepts_a_forced_exit(self):
        from bujji.production_runtime.trading_session_governor.session_governor import (
            TradingSessionGovernor)
        sig = inspect.signature(TradingSessionGovernor.evaluate_and_enforce_exit)
        assert "force_exit_reason" in sig.parameters
        assert sig.parameters["force_exit_reason"].default is None


class TestFlatIsVerifiedNotAssumed:
    class _Stub:
        import logging as _l
        _logger = _l.getLogger("brake-test")

        def __init__(self, broker):
            self._broker = broker

    def _flat(self, broker):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "runner_brake", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.OptionsOSRunner._broker_reports_flat(self._Stub(broker))

    def test_no_open_legs_is_flat(self):
        class _B:
            async def get_open_positions(self):
                return []
        assert self._flat(_B())[0] is True

    def test_an_open_leg_is_not_flat(self):
        class _B:
            async def get_open_positions(self):
                return [_pos("CE", 75)]
        assert self._flat(_B())[0] is False

    def test_a_failed_read_is_unknown_never_flat(self):
        """'I could not ask' must never become 'there is nothing there'."""
        class _B:
            async def get_open_positions(self):
                raise RuntimeError("broker unreachable")
        assert self._flat(_B())[0] is None

    def test_a_none_response_is_unknown_never_flat(self):
        class _B:
            async def get_open_positions(self):
                return None
        assert self._flat(_B())[0] is None

    def test_the_read_is_unfiltered(self):
        """Every other read goes through the registry, which intersects with
        an in-memory table of registered symbols -- so an unregistered
        position is invisible to it by construction."""
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        i = src.index("def _broker_reports_flat")
        block = src[i:i + 1400]
        assert "self._broker.get_open_positions()" in block
        assert "_registry" not in block


class TestCloseSequenceIsIdempotent:
    def test_calling_it_twice_does_not_raise(self):
        """It raised IllegalRuntimeTransition from COMPLETE, which killed
        _session_archive and lost the session's outcome record."""
        from bujji.production_runtime.runtime_state_machine import (
            RuntimeState, RuntimeStateMachine)
        from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime

        from bujji.core.event_bus import EventBus

        class _Root:
            runtime_state_machine = RuntimeStateMachine(
                EventBus(), lambda: dt.datetime(2026, 8, 21, 15, 0),
                initial=RuntimeState.ENTRY_ENABLED)

        runtime = TradingBrainRuntime.__new__(TradingBrainRuntime)
        runtime._root = _Root()
        runtime.run_market_close_sequence()
        assert _Root.runtime_state_machine.state is RuntimeState.COMPLETE
        runtime.run_market_close_sequence()   # must not raise
        assert _Root.runtime_state_machine.state is RuntimeState.COMPLETE

    def test_it_documents_that_it_places_no_order(self):
        """The misreading that caused the defect."""
        from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime
        assert "PLACES NO ORDER" in (TradingBrainRuntime.run_market_close_sequence.__doc__ or "")


class TestEveryLazyImportIsInScope:
    """FOUND LIVE 2026-08-21 09:54:35, in my own code.

    The blind-cycle brake fired correctly -- "3 consecutive unpriced cycles
    with an open position -- cannot see, will not hold" -- and then raised
    `NameError: name 'PositionHealthThresholds' is not defined` instead of
    placing the exit. The position stayed open.

    Cause: this runner imports function-locally (118 such imports).
    _run_one_management_pass has its own PositionHealthThresholds import;
    _execute_emergency_close is a DIFFERENT function and never had one. Every
    surrounding safety behaviour held -- the exception was caught, the broker
    was read, flat=False was reported with both legs named, and
    CRITICAL_UNFLATTENED_POSITION was raised rather than a clean close -- but
    the close itself did not happen.

    A unit test of the brake's WIRING could not catch this; only executing the
    body does. These tests execute it.
    """

    @staticmethod
    def _names_used_but_not_bound(func_name: str):
        """Names a method references that it neither imports, assigns, nor
        receives as a parameter -- and which are not module-level or builtins."""
        import ast
        import builtins

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)
        module_level = set()
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module_level |= {a.asname or a.name.split(".")[0] for a in node.names}
            elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
                module_level.add(node.name)
            elif isinstance(node, ast.Assign):
                module_level |= {t.id for t in node.targets if isinstance(t, ast.Name)}

        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == func_name)
        bound = {a.arg for a in fn.args.args}
        for n in ast.walk(fn):
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                bound |= {a.asname or a.name.split(".")[-1] for a in n.names}
            elif isinstance(n, ast.Assign):
                # TARGETS ONLY. Walking the whole Assign also collects names
                # from the VALUE, which made this test count
                # `PositionHealthThresholds` as bound merely because it
                # appeared on the right-hand side -- so the first version of
                # this test passed against the very bug it was written for.
                # The negative control caught that.
                for tgt in n.targets:
                    bound |= {x.id for x in ast.walk(tgt) if isinstance(x, ast.Name)}
            elif isinstance(n, (ast.AugAssign, ast.AnnAssign)):
                bound |= {x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)}
            elif isinstance(n, ast.withitem) and n.optional_vars is not None:
                bound |= {x.id for x in ast.walk(n.optional_vars) if isinstance(x, ast.Name)}
            elif isinstance(n, ast.For):
                bound |= {x.id for x in ast.walk(n.target) if isinstance(x, ast.Name)}
            elif isinstance(n, (ast.comprehension,)):
                bound |= {t.id for t in ast.walk(n.target) if isinstance(t, ast.Name)}
            elif isinstance(n, ast.ExceptHandler) and n.name:
                bound.add(n.name)
            elif isinstance(n, (ast.FunctionDef, ast.Lambda)) and n is not fn:
                bound |= {a.arg for a in n.args.args}

        used = {n.id for n in ast.walk(fn)
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
        return sorted(used - bound - module_level - set(dir(builtins)))

    def test_the_emergency_close_has_no_unbound_names(self):
        """THE regression. This returned ['PositionHealthThresholds']."""
        assert self._names_used_but_not_bound("_execute_emergency_close") == []

    def test_the_broker_flat_check_has_no_unbound_names(self):
        assert self._names_used_but_not_bound("_broker_reports_flat") == []

    def test_the_eod_closure_has_no_unbound_names(self):
        assert self._names_used_but_not_bound("_run_eod_closure") == []

    def test_the_reconciler_has_no_unbound_names(self):
        assert self._names_used_but_not_bound("_reconcile_broker_positions") == []

    def test_the_management_pass_has_no_unbound_names(self):
        assert self._names_used_but_not_bound("_run_one_management_pass") == []

    def test_orphan_registration_has_no_unbound_names(self):
        assert self._names_used_but_not_bound("_register_orphaned_legs") == []
