"""The broker-truth closure machine must run even when the strategy exit dies.

THE DEFECT. `_eod_close()` was two bare statements in sequence:

    self._run_one_management_pass("EOD_CLOSE")
    self._run_eod_closure()

so ANY exception from the first meant the second never ran.

That is backwards. The management pass is the strategy-aware exit -- it prices
legs from the valuation and applies the exit policy. `_run_eod_closure()` is
the BACKSTOP: it reads the broker's own account, cancels working orders, and
flattens whatever the strategy did not. It is also the only one of the two that
is fully guarded -- it catches everything and records BROKER_TRUTH_UNKNOWN
rather than ever claiming flatness it did not establish.

Gating the backstop on the optimisation removes it precisely when it is needed.

The paths that raise are ordinary. `positions_for_group()` and
`get_group_reality()` reach the broker through PositionRealityRegistry and do
NOT catch, so a failing live position read -- the single most likely reason a
position is still open at 15:30 -- took the closure machine down with it.

TERMINATION. A KeyboardInterrupt or SystemExit arriving during the management
pass is a real stop request and must not be swallowed. It is re-raised AFTER
the flatten attempt, never instead of one: delaying a signal is acceptable,
masking it is not, and neither is abandoning a position to honour it promptly.
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
        "_runner_backstop_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner(*, pass_raises=None):
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-backstop")
    r._governor_result_summary = {}
    r._stage = None
    calls = []

    def _mgmt(label):
        calls.append(("management", label))
        if pass_raises is not None:
            raise pass_raises

    r._run_one_management_pass = _mgmt
    r._run_eod_closure = lambda: calls.append(("closure", None))
    r._calls = calls
    return r


def _ran_closure(runner):
    return any(c[0] == "closure" for c in runner._calls)


class TestTheBackstopAlwaysRuns:
    def test_it_runs_on_the_ordinary_path(self):
        r = _runner()
        r._eod_close()
        assert _ran_closure(r)

    @pytest.mark.parametrize("exc", [
        RuntimeError("broker read failed"),
        ValueError("valuation blew up"),
        KeyError("position_group_id"),
        AttributeError("registry is None"),
    ])
    def test_it_runs_even_when_the_management_pass_raises(self, exc):
        r = _runner(pass_raises=exc)
        r._eod_close()
        assert _ran_closure(r), (
            f"{type(exc).__name__} in the strategy exit prevented the "
            f"broker-truth flatten from running at all")
        assert type(exc).__name__ in r._governor_result_summary[
            "eod_management_pass_error"]

    def test_a_position_read_error_does_not_take_the_closure_down(self):
        """The specific live shape: get_open_positions() now raises rather than
        reporting an empty book, and PositionRealityRegistry does not catch."""
        from bujji.broker.errors import PositionReadError

        r = _runner(pass_raises=PositionReadError("positions response not ok"))
        r._eod_close()
        assert _ran_closure(r)


class TestTerminationIsHonouredButNotAheadOfTheFlatten:
    @pytest.mark.parametrize("sig", [KeyboardInterrupt, SystemExit])
    def test_the_signal_is_re_raised(self, sig):
        r = _runner(pass_raises=sig())
        with pytest.raises(sig):
            r._eod_close()

    @pytest.mark.parametrize("sig", [KeyboardInterrupt, SystemExit])
    def test_but_only_after_the_flatten_was_attempted(self, sig):
        r = _runner(pass_raises=sig())
        with pytest.raises(sig):
            r._eod_close()
        assert _ran_closure(r), (
            f"{sig.__name__} during the management pass skipped the flatten -- "
            f"honouring a stop request promptly is not worth abandoning a "
            f"position to do it")
        order = [c[0] for c in r._calls]
        assert order.index("closure") > order.index("management")


class TestTheErrorIsRecordedNotSwallowed:
    def test_the_summary_carries_the_failure(self):
        r = _runner(pass_raises=RuntimeError("boom"))
        r._eod_close()
        assert "eod_management_pass_error" in r._governor_result_summary
        assert "boom" in r._governor_result_summary["eod_management_pass_error"]

    def test_a_clean_pass_records_no_error(self):
        r = _runner()
        r._eod_close()
        assert "eod_management_pass_error" not in r._governor_result_summary
