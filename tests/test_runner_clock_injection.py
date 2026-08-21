"""The session clock is injectable, so time-driven behaviour is testable.

WHY THIS EXISTS. `_position_management` stops monitoring when the clock
passes `monitor_until`. Before injection that branch could only be reached
by running the suite at the right hour: the runner read `now_ist()`
directly, so the loop's exit condition depended on when someone happened to
run the tests. It exited immediately all afternoon (real time already past
15:15) and slept for 6h30m the first time a run started after midnight
(2026-08-18, 04:47 IST -- `04:47 >= 15:15` is False, so all 78 production
default cycles slept for real).

So the monitor_until branch was never actually verified. It fired by
accident, or hung. These tests drive it deliberately.
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

import pytest

from bujji_options_os_runner import OptionsOSRunner
from tests.test_options_os_runner import DAY, base_config

IST = ZoneInfo("Asia/Kolkata")


def at(hh: int, mm: int) -> datetime.datetime:
    return datetime.datetime(2026, 5, 25, hh, mm, tzinfo=IST)


def _runner(tmp_path, clock=None, **overrides):
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    config["position_management"] = {
        "cycle_interval_seconds": 0, "monitor_until": "15:15:00", "max_cycles": 10,
        **overrides,
    }
    return OptionsOSRunner(
        config=config, as_of_date=DAY, session_id="CLOCK-TEST",
        logger=logging.getLogger("test-clock"), clock=clock,
    )


class TestInjection:
    def test_the_default_is_the_real_clock(self, tmp_path):
        """Production behaviour must be unchanged by the injection point."""
        from bujji.core.clock import now_ist
        assert _runner(tmp_path)._clock_fn is now_ist

    def test_an_injected_clock_is_used(self, tmp_path):
        fixed = at(11, 30)
        assert _runner(tmp_path, clock=lambda: fixed)._clock() == fixed

    def test_clock_stays_a_bound_method_for_component_wiring(self, tmp_path):
        """Components are wired with `clock=self._clock` throughout, so the
        injection had to sit behind that call rather than replace it."""
        runner = _runner(tmp_path, clock=lambda: at(10, 0))
        assert callable(runner._clock)
        assert runner._clock() == at(10, 0)


class TestMonitorUntilIsNowReachable:
    """The branch that could previously only be hit by luck."""

    def test_the_loop_stops_once_the_clock_passes_monitor_until(self, tmp_path):
        runner = _runner(tmp_path, clock=lambda: at(15, 30))   # past 15:15
        summary = runner.run()
        assert summary["management_cycles"] == 1, (
            "expected exactly one pass then a clock-driven stop")

    def test_the_loop_keeps_going_while_the_clock_is_inside_the_window(self, tmp_path):
        """The mirror case, and the one that used to hang: before the
        cutoff the loop must run to its safety cap instead."""
        runner = _runner(tmp_path, clock=lambda: at(10, 0), max_cycles=3)
        summary = runner.run()
        assert summary["management_cycles"] == 3, "expected the cap to bound it"

    def test_a_clock_exactly_at_monitor_until_stops(self, tmp_path):
        """The comparison is `>=`, so the boundary itself terminates."""
        runner = _runner(tmp_path, clock=lambda: at(15, 15))
        assert runner.run()["management_cycles"] == 1


class TestNoRealSleeping:
    def test_a_zero_interval_session_completes_promptly(self, tmp_path):
        """A unit test must never sleep on the real clock. With
        cycle_interval_seconds=0 the loop skips its sleep entirely."""
        import time
        started = time.monotonic()
        _runner(tmp_path, clock=lambda: at(10, 0), max_cycles=5).run()
        assert time.monotonic() - started < 30, "the loop slept for real"
