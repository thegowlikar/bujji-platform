"""The session must know when its own market closes.

THE GAP. This runner's entire schedule is configured times -- entry_cutoff
14:30, mandatory_exit 15:15, observe_until 15:30 -- and it referenced the real
exchange close NOWHERE. `bujji.market_calendar` has separated
CASH_MARKET_CLOSE (15:30) from FO_MARKET_CLOSE (15:40) since the NSE circular
of 2026-05-30, and Bujji trades F&O.

The 15:30 observation end is a deliberate ten-minute buffer before the close,
not a close, and that is correct. What was missing is the CONSEQUENCE of
crossing 15:40.

After the F&O close, submitting an exit is not "an attempt that failed" -- it
is an attempt that cannot succeed. A position still open at 15:41 is an
OVERNIGHT position carrying gap risk until the next session; one still open at
15:31 has nine minutes left to flatten in. Those are categorically different
facts and the session reported them identically.

This changes nothing about what the closure machine DOES. It records which of
the two situations the operator is actually in.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.market_calendar import CASH_MARKET_CLOSE, FO_MARKET_CLOSE  # noqa: E402


def _runner_module():
    spec = importlib.util.spec_from_file_location(
        "_runner_foclose_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _runner(at: dt.time):
    mod = _runner_module()
    r = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
    r._logger = logging.getLogger("test-foclose")
    r._governor_result_summary = {}
    r._clock = lambda: dt.datetime(2026, 8, 24, at.hour, at.minute, at.second)
    r._config = {}
    r._journal = None
    r._journal_path = "/tmp/j"
    r._broker = None
    r._exit_place_fn = None
    r._session_id = "S1"

    class _Root:
        underlying = "NIFTY"
        exchange_lot_size = 65

    r._root = _Root()
    return mod, r


class TestTheTwoClosesAreDistinct:
    def test_fo_closes_after_cash(self):
        assert FO_MARKET_CLOSE > CASH_MARKET_CLOSE
        assert FO_MARKET_CLOSE == dt.time(15, 40)
        assert CASH_MARKET_CLOSE == dt.time(15, 30)


class TestBeforeTheFOClose:
    @pytest.mark.parametrize("at,expected", [
        (dt.time(15, 30, 0), "10m"),
        (dt.time(15, 33, 44), "7m"),      # the real 2026-08-21 EOD time
        (dt.time(15, 39, 0), "1m"),
    ])
    def test_the_remaining_margin_is_reported(self, at, expected):
        mod, r = _runner(at)
        assert mod._fo_close_margin(at, FO_MARKET_CLOSE) == expected

    def test_it_is_not_flagged_as_past_the_close(self, caplog):
        mod, r = _runner(dt.time(15, 33, 44))
        with caplog.at_level(logging.INFO):
            try:
                r._run_eod_closure()
            except Exception:
                pass          # the closure machine itself is not under test
        assert r._governor_result_summary["eod_started_after_fo_close"] is False
        assert any("remaining before the F&O close" in rec.getMessage()
                   for rec in caplog.records)


class TestAfterTheFOClose:
    @pytest.mark.parametrize("at", [dt.time(15, 40, 0), dt.time(15, 41, 0),
                                    dt.time(16, 5, 0)])
    def test_it_is_flagged(self, at):
        mod, r = _runner(at)
        try:
            r._run_eod_closure()
        except Exception:
            pass
        assert r._governor_result_summary["eod_started_after_fo_close"] is True

    def test_it_is_logged_at_CRITICAL_and_names_the_consequence(self, caplog):
        mod, r = _runner(dt.time(15, 42, 0))
        with caplog.at_level(logging.CRITICAL):
            try:
                r._run_eod_closure()
            except Exception:
                pass
        messages = " ".join(rec.getMessage() for rec in caplog.records)
        assert "AFTER THE F&O CLOSE" in messages
        assert "OVERNIGHT position" in messages, (
            "the log must name what the operator is now holding, not merely "
            "that a time passed")

    def test_the_boundary_is_the_FO_close_not_the_cash_close(self):
        """15:35 is past the CASH close and before the F&O close. An options
        system that treated 15:30 as its close would flag this, and be wrong:
        there are still five minutes in which an exit can fill."""
        mod, r = _runner(dt.time(15, 35, 0))
        try:
            r._run_eod_closure()
        except Exception:
            pass
        assert r._governor_result_summary["eod_started_after_fo_close"] is False


class TestTheMarginHelperIsPure:
    def test_zero_at_and_after_the_close(self):
        mod = _runner_module()
        assert mod._fo_close_margin(dt.time(15, 40), FO_MARKET_CLOSE) == "0m"
        assert mod._fo_close_margin(dt.time(16, 0), FO_MARKET_CLOSE) == "0m"
