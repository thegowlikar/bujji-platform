"""A FAILED emergency close must not abandon the position.

WHAT HAPPENED, from the systemd journal of 2026-08-21 (paper session
OPTIONS_OS_2026-08-21_c0f9414e, naked short strangle, 65 lots per leg):

  09:52:34  Position OPEN -- NIFTY...24450CE / NIFTY...24050PE, both SELL
  09:52:35  tick feed priced 0/2 legs  -> BLIND CYCLE [1]
  09:53:35  BLIND CYCLE [2]
  09:54:35  BLIND CYCLE [3]
  09:54:35  EMERGENCY CLOSE: 3 consecutive unpriced cycles -- cannot see,
            will not hold                                  <- brake correct
  09:54:35  EMERGENCY CLOSE FAILED TO EXECUTE (NameError)  <- close crashed
  09:54:35  CRITICAL_UNFLATTENED_POSITION: broker flat=False, both legs open
  09:55:35  POSITION_MANAGEMENT halted: emergency close executed.   <- FALSE
  15:33:44  EOD sweep finally flattens it

Five hours and 38 minutes of an undefined-risk position with no management
pass, no stop-loss evaluation, and no retry -- because `_emergency_closed`
was set BEFORE the close was attempted and never reconsidered, and the
management loop treats it as a permanent halt.

The NameError is fixed. The fail-open is the general case and survives it:
a broker timeout, a rejection, a network drop or any exception on the exit
path produces exactly the same abandonment.

THE INVARIANT: `_emergency_closed` means the BROKER CONFIRMED FLAT. Anything
else -- False, or None for "could not establish" -- keeps the loop running so
the next pass retries. UNKNOWN is not FLAT.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_brake_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Valuation:
    total_unrealized_pnl = -1000.0


class _Engine:
    async def revalue_all(self, prices, clock, price_timestamps=None):
        return {"PG-1": _Valuation()}


class _Registry:
    async def positions_for_group(self, pg):
        return [{"symbol": "NIFTY2026-08-2524450CE", "qty": 65, "side": "SELL"},
                {"symbol": "NIFTY2026-08-2524050PE", "qty": 65, "side": "SELL"}]


class _Governor:
    _position_group_id = "PG-1"


def _make_runner(mod, *, close_outcome):
    """A runner stubbed down to the brake block, with a controllable close.

    `close_outcome` is what the broker says AFTER the emergency close:
    True (flat), False (legs still open), None (could not establish), or the
    string "raise" to simulate the close blowing up entirely -- the real
    2026-08-21 shape.
    """
    import logging

    runner = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    runner._logger = logging.getLogger("test-brake")
    runner._entry_prices = {"NIFTY2026-08-2524450CE": 24.6,
                            "NIFTY2026-08-2524050PE": 23.1}
    runner._clock = lambda: dt.datetime(2026, 8, 21, 9, 54, 35)
    runner._portfolio_engine = _Engine()
    runner._registry = _Registry()
    runner._governor = _Governor()
    runner._canonical_position_id = "POS-test"
    runner._valuation_history = []
    runner._blind_cycles = 3
    runner._priced_from_ticks_cycles = 0
    runner._consecutive_blind_cycles = 3
    runner._price_provider = object()          # a tick source EXISTS and failed
    runner._governor_result_summary = {}

    class _Broker:
        realized_pnl = 0.0

    runner._broker = _Broker()
    runner._config = {"position_management": {"max_consecutive_blind_cycles": 3},
                      "capital_snapshot": {"daily_loss_limit": 25000.0}}
    runner._reconcile_broker_positions = lambda _label: None
    # No tick prices -> blind cycle -> the blind brake fires, exactly as live.
    runner._current_leg_prices = lambda _as_of: ({}, False)

    calls = {"n": 0}

    def _fake_close(reason, valuation, stage_label, symbols_before_exit):
        calls["n"] += 1
        if close_outcome == "raise":
            # The real 2026-08-21 shape: _execute_emergency_close catches its
            # own exception, records the error, and reports NOT flat.
            runner._governor_result_summary["emergency_close_execution_error"] = (
                "NameError: name 'PositionHealthThresholds' is not defined")
            runner._governor_result_summary["emergency_close_broker_flat"] = False
            return
        runner._governor_result_summary["emergency_close_broker_flat"] = close_outcome

    runner._execute_emergency_close = _fake_close
    runner._close_calls = calls
    return runner


class TestAFailedCloseDoesNotAbandonThePosition:

    def test_the_real_2026_08_21_shape_keeps_management_alive(self):
        """The close raises, the broker still shows both legs -- the loop must
        NOT be halted."""
        mod = _runner_module()
        runner = _make_runner(mod, close_outcome="raise")
        runner._run_one_management_pass("POSITION_MANAGEMENT[3]")

        assert runner._close_calls["n"] == 1, "the brake did not attempt a close"
        assert getattr(runner, "_emergency_closed", False) is False, (
            "management was halted after a close that FAILED -- this is the "
            "5h38m abandonment of 2026-08-21")

    @pytest.mark.parametrize("outcome", [False, None])
    def test_not_flat_and_could_not_establish_both_keep_management_alive(self, outcome):
        """UNKNOWN is not FLAT. `None` means the broker read failed, which is
        the strongest possible reason to keep watching."""
        mod = _runner_module()
        runner = _make_runner(mod, close_outcome=outcome)
        runner._run_one_management_pass("POSITION_MANAGEMENT[3]")
        assert getattr(runner, "_emergency_closed", False) is False

    def test_a_confirmed_flat_close_DOES_halt_management(self):
        """The halt must still work when it is true -- otherwise the loop
        would keep re-closing an already-flat book."""
        mod = _runner_module()
        runner = _make_runner(mod, close_outcome=True)
        runner._run_one_management_pass("POSITION_MANAGEMENT[3]")
        assert runner._emergency_closed is True

    def test_a_stale_flat_from_an_earlier_pass_is_not_reused(self):
        """`emergency_close_broker_flat` is cleared before each attempt, so a
        True left by a previous pass cannot be read as this pass's result."""
        mod = _runner_module()
        runner = _make_runner(mod, close_outcome="raise")
        runner._governor_result_summary["emergency_close_broker_flat"] = True
        runner._run_one_management_pass("POSITION_MANAGEMENT[4]")
        assert getattr(runner, "_emergency_closed", False) is False


class TestTheLoopHaltCondition:

    def test_the_loop_halts_only_on_the_confirmed_flag(self):
        """`_position_management` breaks on `_emergency_closed`. Paired with
        the tests above, that flag now only becomes True on a confirmed flat,
        so the break is reachable only for a genuinely closed book."""
        import inspect
        mod = _runner_module()
        source = inspect.getsource(mod.OptionsOSRunner._position_management)
        assert "_emergency_closed" in source
        assert "confirmed flat" in source, (
            "the halt message must not claim the close executed when it may "
            "not have -- that sentence was printed live on 2026-08-21 after a "
            "close that raised NameError")
