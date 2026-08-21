"""A session that dies mid-flight must still try to flatten.

THE DEFECT. `_eod_close()` -- the ONLY broker-truth flatten -- sat inside
run()'s `try` body:

    try:
        self._startup(); self._pre_market_check(); self._market_session()
        self._continuous_session()        # <- any raise here ...
        self._eod_close()                 # <- ... skips this entirely
        self._session_archive()
    finally:
        self._shutdown()

and `_shutdown()` deliberately does not flatten -- its own comment says it
"only logs and releases whatever was constructed in _startup()".

So an unhandled exception at 11:00 with a naked short open left the position
at the broker with NO flatten ever attempted. The unit did fail and the
operator was alerted, which is not nothing -- but an autonomous system's
answer to "I crashed while short" cannot be to leave the position and send a
message.

RE-ENTRY IS DELIBERATE AND SAFE. If `_eod_close` itself is what raised, the
abort path runs it again: `run_eod_closure` discovers broker positions before
it submits anything, so a retry exits only what is STILL open. Marking the
close "done" before it finished would strand exactly the position this path
exists to catch.
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
        "_runner_abort_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Boom(RuntimeError):
    """The session's real failure. It must never be replaced."""


def _runner(*, opened, session_raises=False, eod_raises=0, archive_raises=False):
    """A runner stubbed to the lifecycle skeleton.

    `eod_raises` is how many of the FIRST _eod_close calls should raise.
    """
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-abort")
    r._governor_result_summary = {}
    r._session_cfg = {"continuous": True}
    r._entry_prices = {"NIFTY...CE": 24.6} if opened else {}
    r._canonical_position_id = "POS-1" if opened else None
    calls = []

    r._startup = lambda: calls.append("startup")
    r._pre_market_check = lambda: calls.append("pre_market")
    r._market_session = lambda: calls.append("market_session")
    def _archive():
        calls.append("archive")
        if archive_raises:
            raise _Boom("archive died after a clean close")

    r._session_archive = _archive
    r._shutdown = lambda: calls.append("shutdown")

    def _continuous():
        calls.append("continuous")
        if session_raises:
            raise _Boom("the session died")

    def _eod():
        calls.append("eod_close")
        if len([c for c in calls if c == "eod_close"]) <= eod_raises:
            raise RuntimeError("closure machine failed")

    r._continuous_session = _continuous
    r._eod_close = _eod
    r._calls = calls
    return r


class TestAnAbortStillAttemptsTheFlatten:
    def test_a_dying_session_with_a_position_calls_eod_close(self):
        r = _runner(opened=True, session_raises=True)
        with pytest.raises(_Boom):
            r.run()
        assert "eod_close" in r._calls, (
            "the session died with a position open and never attempted the "
            "broker-truth flatten")
        assert r._governor_result_summary["aborted_before_eod_close"] is True

    def test_the_original_failure_is_not_masked(self):
        """The diagnosis must survive. A secondary exception from the flatten
        would replace the real cause with its own."""
        r = _runner(opened=True, session_raises=True, eod_raises=99)
        with pytest.raises(_Boom):
            r.run()
        assert "abort_flatten_error" in r._governor_result_summary

    def test_shutdown_still_runs(self):
        r = _runner(opened=True, session_raises=True)
        with pytest.raises(_Boom):
            r.run()
        assert r._calls[-1] == "shutdown"


class TestItDoesNotFireWhenItShouldNot:
    def test_no_position_means_no_flatten_attempt(self):
        """Nothing was opened, so nothing can be left open. An alarm on
        ordinary no-trade failures trains the operator to ignore it."""
        r = _runner(opened=False, session_raises=True)
        with pytest.raises(_Boom):
            r.run()
        assert "eod_close" not in r._calls
        assert "aborted_before_eod_close" not in r._governor_result_summary

    def test_a_failure_AFTER_a_clean_close_does_not_close_twice(self):
        """This is what `_eod_close_completed` actually protects.

        The book is already flat; re-running the closure would submit a second
        set of exit orders against it. Every other test here exercises a
        failure BEFORE or DURING the close, so none of them touch this guard --
        a negative control that deleted it left the suite green, which is how
        the gap was found.
        """
        r = _runner(opened=True, archive_raises=True)
        with pytest.raises(_Boom):
            r.run()
        assert r._calls.count("eod_close") == 1, (
            f"the close ran {r._calls.count('eod_close')} times; the book was "
            f"already flat and a second run is a duplicate-order path")

    def test_a_clean_session_closes_exactly_once(self):
        """No double-flatten on the happy path -- that would be a duplicate
        order path, which is worse than the failure being recovered from."""
        r = _runner(opened=True, session_raises=False)
        r.run()
        assert r._calls.count("eod_close") == 1
        assert "aborted_before_eod_close" not in r._governor_result_summary


class TestAFailedCloseIsRetried:
    def test_eod_close_raising_is_retried_once_by_the_abort_path(self):
        """`_eod_close_completed` is set only after it RETURNS, so a close
        that raised partway is re-entered. run_eod_closure reads broker truth
        before submitting, so the retry exits only what is still open."""
        r = _runner(opened=True, session_raises=False, eod_raises=1)
        with pytest.raises(RuntimeError):
            r.run()
        assert r._calls.count("eod_close") == 2, (
            f"expected the failed close to be retried once, saw "
            f"{r._calls.count('eod_close')} attempt(s)")

    def test_a_close_that_keeps_failing_stops_and_reports(self):
        r = _runner(opened=True, session_raises=False, eod_raises=99)
        with pytest.raises(RuntimeError):
            r.run()
        assert r._calls.count("eod_close") == 2, "must not retry unboundedly"
        assert "abort_flatten_error" in r._governor_result_summary
