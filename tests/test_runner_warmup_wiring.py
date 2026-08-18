"""The warm-up + stability gate, as the runner actually uses it.

Two properties dominate. First, absent config the runner must behave
EXACTLY as before -- this is opt-in, and a change that silently altered
every session's regime derivation would be far worse than the starvation
it fixes. Second, an unstable verdict must seed NOTHING: handing the
recorder evidence whose reading depends on the sampling interval is
precisely what the gate exists to prevent.
"""
from __future__ import annotations

import datetime
import logging
from zoneinfo import ZoneInfo

import pytest

from bujji.regime_stability import (
    WarmupConfigurationError, WarmupPlan, build_spot_only_snapshot, poll_spot_series,
)

IST = ZoneInfo("Asia/Kolkata")


def clock_from(values):
    it = iter(values)
    return lambda: next(it)


def ts(minute, second=0):
    return datetime.datetime(2026, 8, 18, 9, minute, second, tzinfo=IST)


class TestSpotOnlySnapshot:
    def test_absent_legs_are_declared_not_invented(self):
        s = build_spot_only_snapshot("NSE:NIFTY50-INDEX", 24601.05,
                                     ts(22).isoformat(), 12.5)
        assert s.spot.ltp == 24601.05
        assert s.vix.value is None
        assert s.futures is None
        assert s.option_chain is None
        assert set(s.missing_fields) == {"vix", "futures", "option_chain"}

    def test_it_is_marked_degraded_so_it_cannot_pass_as_a_full_snapshot(self):
        s = build_spot_only_snapshot("NSE:NIFTY50-INDEX", 24601.05,
                                     ts(22).isoformat(), 1.0)
        assert s.health_status == "DEGRADED"
        assert s.source == "fyers_live_warmup"

    def test_no_price_yields_no_snapshot(self):
        assert build_spot_only_snapshot("X", None, ts(22).isoformat(), 1.0) is None


class TestPlanValidation:
    def test_a_plan_too_short_for_the_widest_stride_is_refused_up_front(self):
        """Better to fail at startup than to poll for minutes and then
        discover the gate must refuse for a configuration reason."""
        with pytest.raises(WarmupConfigurationError, match="below the 4"):
            WarmupPlan(8, 30.0, (1, 2, 4)).validate()

    def test_the_message_says_how_many_polls_would_work(self):
        with pytest.raises(WarmupConfigurationError, match="at least 16"):
            WarmupPlan(8, 30.0, (1, 2, 4)).validate()

    def test_a_sufficient_plan_passes(self):
        WarmupPlan(16, 30.0, (1, 2, 4)).validate()

    def test_duration_is_the_entry_delay(self):
        assert WarmupPlan(16, 30.0, (1, 2, 4)).duration_seconds == 450.0

    @pytest.mark.parametrize("polls,interval", [(0, 30.0), (-1, 30.0), (16, 0.0), (16, -5.0)])
    def test_nonsense_plans_are_refused(self, polls, interval):
        with pytest.raises(WarmupConfigurationError):
            WarmupPlan(polls, interval, (1, 2, 4)).validate()


class TestPolling:
    def test_it_polls_the_configured_number_of_times(self):
        prices = iter([24600.0 + i for i in range(16)])
        got = poll_spot_series(lambda: next(prices), clock_from([ts(22, i) for i in range(16)]),
                               WarmupPlan(16, 30.0, (1, 2, 4)), sleep=lambda _s: None)
        assert len(got) == 16
        assert got[0].spot.ltp == 24600.0 and got[-1].spot.ltp == 24615.0

    def test_a_failed_poll_is_skipped_never_backfilled(self):
        """Repeating a stale price would manufacture a DUPLICATE_OBSERVATION
        and teach the builder the market stood still."""
        seq = [24600.0, None, 24602.0, 24603.0]
        it = iter(seq)
        got = poll_spot_series(lambda: next(it), clock_from([ts(22, i) for i in range(4)]),
                               WarmupPlan(4, 1.0, (1,)), sleep=lambda _s: None)
        assert [s.spot.ltp for s in got] == [24600.0, 24602.0, 24603.0]

    def test_a_raising_poll_does_not_end_the_warmup(self):
        calls = {"n": 0}
        def fetch():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("transient")
            return 24600.0 + calls["n"]
        got = poll_spot_series(fetch, clock_from([ts(22, i) for i in range(4)]),
                               WarmupPlan(4, 1.0, (1,)), sleep=lambda _s: None,
                               logger=logging.getLogger("t"))
        assert len(got) == 3

    def test_it_sleeps_between_polls_but_not_before_the_first(self):
        waits = []
        prices = iter([24600.0 + i for i in range(4)])
        poll_spot_series(lambda: next(prices), clock_from([ts(22, i) for i in range(4)]),
                         WarmupPlan(4, 30.0, (1,)), sleep=waits.append)
        assert waits == [30.0, 30.0, 30.0]


class TestRunnerIntegration:
    def _runner(self, tmp_path, warmup=None):
        from bujji_options_os_runner import OptionsOSRunner
        from tests.test_options_os_runner import DAY, base_config
        cfg = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
        if warmup is not None:
            cfg["providers"]["regime"]["warmup"] = warmup
        return OptionsOSRunner(config=cfg, as_of_date=DAY, session_id="WARM",
                               logger=logging.getLogger("t"))

    def test_absent_config_means_no_warmup_and_unchanged_behaviour(self, tmp_path):
        """Opt-in. A change that silently altered every session's regime
        derivation would be worse than the starvation it fixes."""
        r = self._runner(tmp_path)
        assert r._warm_up_observation_memory(adapter=None) is None

    def test_an_invalid_plan_raises_at_startup_not_after_polling(self, tmp_path):
        r = self._runner(tmp_path, warmup={"polls": 8, "interval_seconds": 30})
        with pytest.raises(WarmupConfigurationError):
            r._warm_up_observation_memory(adapter=None)
