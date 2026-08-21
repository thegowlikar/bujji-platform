"""The daily-loss brake must count losses that were already REALIZED.

THE DEFECT. The brake read `getattr(self._broker, "realized_pnl", 0.0)`.
No broker defines that attribute:

    PaperBroker.realized_pnl        -> AttributeError (it is `_realized_pnl`,
                                       a private dict behind get_realized_pnl())
    FyersBroker.realized_pnl        -> AttributeError
    FyersBroker.get_realized_pnl    -> does not exist either

So the getattr DEFAULT was taken on every single call and the realized half
of the daily loss limit was permanently 0.0.

WHAT THAT COSTS. The brake fires on
`realized + unrealized <= -daily_loss_limit`, with the limit configured at
25000. A session that banks -20000 on a closed leg and then opens another
position carrying -10000 unrealized has breached the limit by 5000 -- and the
brake scored it -10000 and held. The loss the operator actually took is the
half that was invisible.

Zero is not "unknown". Returning 0.0 for a value that could not be read is a
positive claim that nothing was realized, and it fails OPEN. The fix returns
None instead and discloses the gap once per session, loudly, naming the
broker that cannot answer.
"""
from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_realized_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner_with(broker):
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._broker = broker
    r._logger = logging.getLogger("test-realized")
    r._governor_result_summary = {}
    return mod, r


class TestTheAttributeNeverExisted:
    """Pins the premise, so this cannot be 'fixed' by re-adding the getattr."""

    def test_no_broker_exposes_a_realized_pnl_attribute(self):
        from bujji.broker.fyers import FyersBroker
        from bujji.broker.paper import PaperBroker

        assert not hasattr(PaperBroker(), "realized_pnl")
        assert not hasattr(FyersBroker, "realized_pnl")

    def test_paper_broker_does_expose_the_getter(self):
        from bujji.broker.paper import PaperBroker

        assert callable(PaperBroker().get_realized_pnl)


class TestTheBrakeNowSeesRealizedLosses:
    def test_a_realized_loss_is_read_through_the_getter(self):
        class _B:
            def get_realized_pnl(self):
                return -20000.0

        _mod, runner = _runner_with(_B())
        assert runner._session_realized_pnl() == -20000.0

    def test_the_combined_breach_now_trips_the_brake(self):
        """-20000 realized + -10000 unrealized against a 25000 limit is a
        breach. With realized pinned at 0.0 it was scored -10000 and held."""
        mod, _runner = _runner_with(object())
        reason = mod._emergency_brake(
            unrealized_pnl=-10000.0, realized_pnl=-20000.0,
            daily_loss_limit=25000.0,
            consecutive_blind_cycles=0, max_consecutive_blind_cycles=3)
        assert reason is not None and "EMERGENCY_LOSS" in reason

        blind = mod._emergency_brake(
            unrealized_pnl=-10000.0, realized_pnl=0.0,      # the old behaviour
            daily_loss_limit=25000.0,
            consecutive_blind_cycles=0, max_consecutive_blind_cycles=3)
        assert blind is None, (
            "this is what the brake used to see -- the same session, held")


class TestAnUnreadableValueIsNoneNotZero:
    def test_a_broker_with_neither_returns_None(self):
        _mod, runner = _runner_with(object())
        assert runner._session_realized_pnl() is None

    def test_the_gap_is_disclosed_once_and_recorded(self, caplog):
        _mod, runner = _runner_with(object())
        with caplog.at_level(logging.WARNING):
            runner._session_realized_pnl()
            runner._session_realized_pnl()
            runner._session_realized_pnl()
        hits = [r for r in caplog.records
                if "DAILY-LOSS BRAKE IS INCOMPLETE" in r.getMessage()]
        assert len(hits) == 1, (
            f"disclosed {len(hits)} times; once per session -- an alarm on "
            f"every cycle trains the operator to ignore it")
        assert "UNAVAILABLE" in runner._governor_result_summary[
            "daily_loss_brake_realized_source"]

    def test_a_raising_getter_does_not_end_the_session(self):
        class _B:
            def get_realized_pnl(self):
                raise RuntimeError("broker down")

        _mod, runner = _runner_with(_B())
        assert runner._session_realized_pnl() is None

    def test_the_brake_is_called_with_the_helper_not_a_getattr_default(self):
        """BUILT is not WIRED: prove the management pass actually uses it."""
        import ast

        tree = ast.parse((REPO_ROOT / "bujji_options_os_runner.py").read_text())
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_run_one_management_pass")
        call = next(c for c in ast.walk(fn)
                    if isinstance(c, ast.Call)
                    and getattr(c.func, "id", None) == "_emergency_brake")
        kw = {k.arg: k.value for k in call.keywords}
        assert "realized_pnl" in kw
        value = kw["realized_pnl"]
        assert isinstance(value, ast.Call), "realized_pnl is not computed at all"
        assert getattr(value.func, "attr", None) == "_session_realized_pnl", (
            "the brake is not reading realized P&L through the helper -- if "
            "this is a getattr with a 0.0 default again, the realized half of "
            "the daily loss limit is silently zero")
