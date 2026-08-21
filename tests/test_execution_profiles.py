"""Phase 20.2 -- execution_profiles tests."""
from __future__ import annotations

import pytest

from bujji.broker.simulation.fill_simulator import LatencyMode
from bujji.broker.simulation.slippage import SlippageMode
from bujji.execution_profiles import EXTREME, NORMAL, STRESS, get_profile
from bujji.execution_profiles.profiles import UnknownExecutionProfileError


def test_all_three_profiles_are_level_c_calibration_pending():
    for p in (NORMAL, STRESS, EXTREME):
        assert p.level == "C"
        assert p.calibration_status == "CALIBRATION_PENDING"


def test_profiles_use_percentage_slippage_never_fixed_tick():
    for p in (NORMAL, STRESS, EXTREME):
        assert p.slippage.mode == SlippageMode.PERCENTAGE


def test_slippage_percentage_increases_with_stress():
    assert NORMAL.slippage.percentage < STRESS.slippage.percentage < EXTREME.slippage.percentage


def test_latency_increases_with_stress():
    for p in (NORMAL, STRESS, EXTREME):
        assert p.latency.mode == LatencyMode.FIXED
    assert NORMAL.latency.fixed_ms < STRESS.latency.fixed_ms < EXTREME.latency.fixed_ms


def test_normal_profile_never_rejects_on_liquidity():
    assert NORMAL.rejection.reject_on_insufficient_liquidity is False


def test_stress_and_extreme_reject_only_when_liquidity_score_supplied():
    assert STRESS.rejection.reject_on_insufficient_liquidity is True
    assert EXTREME.rejection.reject_on_insufficient_liquidity is True
    assert STRESS.rejection.min_liquidity_score < EXTREME.rejection.min_liquidity_score


def test_charges_identical_across_all_profiles():
    assert NORMAL.charges == STRESS.charges == EXTREME.charges


def test_get_profile_returns_named_profile():
    assert get_profile("NORMAL") is NORMAL
    assert get_profile("STRESS") is STRESS
    assert get_profile("EXTREME") is EXTREME


def test_get_profile_rejects_unknown_name():
    with pytest.raises(UnknownExecutionProfileError):
        get_profile("MADE_UP")


def test_profiles_are_immutable():
    with pytest.raises(Exception):
        NORMAL.name = "X"
