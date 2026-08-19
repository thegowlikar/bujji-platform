"""Naked positions get a tighter management loop -- and the cap follows it.

The interval IS the width of the window in which an unbounded loss runs
unchecked: stop-loss, daily loss limit and emergency brake are all evaluated
once per pass. Measured over 168,194 real 5-minute NIFTY bars, the worst
single bar ranged 611.8 points -- ~Rs 39,764 against a real 240.95-point
straddle credit, 2.5x the stop, inside ONE 300-second interval.

The trap this had to avoid: `max_cycles: 78` was documented as "6h15m / 5min,
one session's worth". At 60s it would have ended management after 78 MINUTES,
leaving an open naked position unwatched until the mandatory exit -- strictly
worse than the cadence it was meant to improve.
"""
from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_trade_construction import taxonomy as mtc
from bujji_options_os_runner import OptionsOSRunner

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class _Stub:
    """Only what the two methods under test touch.

    The REAL classifier is bound here rather than faked, so these tests
    exercise the actual path the runner takes."""

    _position_is_undefined_risk = OptionsOSRunner._position_is_undefined_risk

    def __init__(self, family=None, now=None, mgmt=None, entry=True):
        self._logger = logging.getLogger("cadence-test")
        self._governor_result_summary = {}
        if family is not None:
            self._governor_result_summary["strategy_selected"] = family
        self._config = {"position_management": mgmt or {}}
        self._entry_prices = {"X": 1.0} if entry else {}
        self._now = now or datetime.datetime(2026, 8, 20, 9, 30, tzinfo=IST)
        self._stage = None
        self._emergency_closed = False
        self.passes = 0

    def _clock(self):
        return self._now

    def _run_one_management_pass(self, label):
        self.passes += 1
        self._entry_prices = {}          # close immediately; we only measure setup


def _risk(family):
    return OptionsOSRunner._position_is_undefined_risk(_Stub(family=family))


class TestRiskClassification:
    @pytest.mark.parametrize("family", sorted(mtc.UNDEFINED_RISK_FAMILIES))
    def test_every_undefined_risk_family_is_naked(self, family):
        assert _risk(family) is True

    @pytest.mark.parametrize("family", sorted(mtc.DEFINED_RISK_FAMILIES))
    def test_every_defined_risk_family_is_not_naked(self, family):
        assert _risk(family) is False

    def test_the_two_shapes_this_decision_was_about(self):
        assert _risk("NEUTRAL_PREMIUM_SELLING") is True    # short strangle
        assert _risk("VOLATILITY_COMPRESSION") is True     # short straddle
        assert _risk("IRON_CONDOR") is False
        assert _risk("BULL_PUT_SPREAD") is False

    def test_a_missing_family_fails_closed_to_naked(self):
        """Being wrong this way costs a few quote calls. Being wrong the
        other way costs an unwatched naked position."""
        assert OptionsOSRunner._position_is_undefined_risk(_Stub(family=None)) is True

    def test_an_unrecognised_family_fails_closed_to_naked(self):
        assert _risk("SOME_FUTURE_SHAPE") is True


def _run(stub):
    OptionsOSRunner._position_management(stub)
    return stub._governor_result_summary.get("management_cadence")


class TestCadenceSelection:
    def test_a_naked_position_gets_the_tighter_loop(self):
        c = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING",
                       mgmt={"cycle_interval_seconds": 300,
                             "undefined_risk_cycle_interval_seconds": 60}))
        assert c["interval_seconds"] == 60 and c["undefined_risk"] is True

    def test_a_defined_risk_position_keeps_the_wider_loop(self):
        """Wings cap the loss whatever happens between passes, so there is
        nothing to buy with the extra calls."""
        c = _run(_Stub(family="IRON_CONDOR",
                       mgmt={"cycle_interval_seconds": 300,
                             "undefined_risk_cycle_interval_seconds": 60}))
        assert c["interval_seconds"] == 300 and c["undefined_risk"] is False

    def test_both_intervals_are_configurable(self):
        c = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING",
                       mgmt={"cycle_interval_seconds": 300,
                             "undefined_risk_cycle_interval_seconds": 15}))
        assert c["interval_seconds"] == 15

    def test_the_production_config_carries_the_tighter_interval(self):
        import yaml

        cfg = yaml.safe_load((REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        mgmt = cfg["position_management"]
        assert mgmt["undefined_risk_cycle_interval_seconds"] == 60
        assert mgmt["cycle_interval_seconds"] == 300


class TestTighterNeverWider:
    """The naked interval is a CEILING on the base, not an independent key.

    Reading a separate key outright caused a real failure: tests set
    `cycle_interval_seconds: 0` to skip sleeping, the naked branch read the
    other key, got its 60s default, and the whole suite began sleeping. The
    same shape would let a config set the naked loop SLOWER than the winged
    one, which inverts the entire point.
    """

    def test_an_explicit_base_of_zero_is_honoured_for_naked_positions(self):
        c = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING",
                       mgmt={"cycle_interval_seconds": 0, "max_cycles": 3}))
        assert c["interval_seconds"] == 0

    def test_a_naked_position_is_never_revalued_less_often_than_a_winged_one(self):
        """Even if the config asks for it."""
        mgmt = {"cycle_interval_seconds": 120,
                "undefined_risk_cycle_interval_seconds": 600}
        naked = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING", mgmt=dict(mgmt)))
        winged = _run(_Stub(family="IRON_CONDOR", mgmt=dict(mgmt)))
        assert naked["interval_seconds"] <= winged["interval_seconds"]
        assert naked["interval_seconds"] == 120

    def test_a_base_tighter_than_the_naked_key_wins(self):
        c = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING",
                       mgmt={"cycle_interval_seconds": 30,
                             "undefined_risk_cycle_interval_seconds": 60}))
        assert c["interval_seconds"] == 30


class TestTheCapFollowsTheInterval:
    """The trap. A cap sized for 300s silently means something different at
    60s, and the failure is silent: management just stops."""

    def test_a_60s_loop_gets_a_cap_that_covers_the_session(self):
        c = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING",
                       now=datetime.datetime(2026, 8, 20, 9, 30, tzinfo=IST),
                       mgmt={"undefined_risk_cycle_interval_seconds": 60,
                             "monitor_until": "15:15:00", "max_cycles": 78}))
        # 09:30 -> 15:15 is 345 minutes; at 60s that needs ~345 cycles, not 78.
        assert c["max_cycles"] >= 345
        assert c["configured_max_cycles"] == 78

    def test_the_configured_value_remains_a_floor(self):
        """It still bounds a runaway loop -- it just stops under-covering."""
        c = _run(_Stub(family="IRON_CONDOR",
                       now=datetime.datetime(2026, 8, 20, 15, 10, tzinfo=IST),
                       mgmt={"cycle_interval_seconds": 300,
                             "monitor_until": "15:15:00", "max_cycles": 78}))
        assert c["max_cycles"] == 78, "a tiny window must not shrink the safety cap"

    def test_a_wider_interval_needs_fewer_cycles_than_a_tighter_one(self):
        args = dict(now=datetime.datetime(2026, 8, 20, 9, 30, tzinfo=IST),
                    mgmt={"cycle_interval_seconds": 300,
                          "undefined_risk_cycle_interval_seconds": 60,
                          "monitor_until": "15:15:00", "max_cycles": 78})
        naked = _run(_Stub(family="NEUTRAL_PREMIUM_SELLING", **args))
        winged = _run(_Stub(family="IRON_CONDOR", **args))
        assert naked["max_cycles"] > winged["max_cycles"]

    def test_a_zero_interval_does_not_divide_by_zero(self):
        """Tests drive the loop with interval 0 to avoid real sleeping."""
        c = _run(_Stub(family="IRON_CONDOR",
                       mgmt={"cycle_interval_seconds": 0, "max_cycles": 10}))
        assert c["max_cycles"] == 10

    def test_no_open_position_records_nothing_and_returns(self):
        stub = _Stub(family="IRON_CONDOR", entry=False)
        OptionsOSRunner._position_management(stub)
        assert stub.passes == 0
        assert "management_cadence" not in stub._governor_result_summary
