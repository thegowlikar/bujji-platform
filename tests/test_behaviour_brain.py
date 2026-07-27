"""Behaviour Brain — unit tests. This brain is INERT BY DESIGN below
MIN_TRADES_REQUIRED (30) because BUJJI's real trade journal currently
has zero completed trades and only ~5 correlated real backtest days
exist -- nowhere near enough for a genuine behavioural reading. There is
therefore no real-data regression fixture here (unlike every other
brain in the MIC) -- that gets added once real trades accumulate. These
tests cover the hard data-quantity gate and, using synthetic trade
sequences sized above the floor, the arithmetic once the gate is met."""
import pytest

from bujji.intelligence.behaviour_brain import (
    MIN_TRADES_REQUIRED,
    STREAK_ALERT_THRESHOLD,
    BehaviourBrain,
)
from bujji.intelligence.models import DataQuality, StreakSignal


@pytest.fixture
def brain():
    return BehaviourBrain()


# ---------------------------------------------------------------------- #
# Hard data-quantity gate -- the core discipline of this brain
# ---------------------------------------------------------------------- #
def test_zero_trades_returns_unknown(brain):
    reading = brain.analyze(trades=[])
    assert reading.total_trades == 0
    assert reading.win_rate is None
    assert reading.avg_pnl is None
    assert reading.current_streak is None
    assert reading.streak_signal is StreakSignal.UNKNOWN
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert reading.confidence == 0.0
    assert "insufficient_history" in reading.reason


def test_five_real_backtest_days_is_still_insufficient(brain):
    """The actual real data on file right now (2026-07-20): ~5 correlated
    backtested trading days. Even that must still return UNKNOWN."""
    trades = [(120.0, "vwap_breach")] * 5
    reading = brain.analyze(trades=trades)
    assert reading.data_quality is DataQuality.INSUFFICIENT
    assert reading.win_rate is None
    assert f"need >= {MIN_TRADES_REQUIRED}" in reading.reason


def test_one_below_threshold_is_still_insufficient(brain):
    trades = [(100.0, "vwap_breach")] * (MIN_TRADES_REQUIRED - 1)
    reading = brain.analyze(trades=trades)
    assert reading.data_quality is DataQuality.INSUFFICIENT


def test_exactly_at_threshold_produces_a_reading(brain):
    trades = [(100.0, "vwap_breach")] * MIN_TRADES_REQUIRED
    reading = brain.analyze(trades=trades)
    assert reading.data_quality is DataQuality.SUFFICIENT
    assert reading.total_trades == MIN_TRADES_REQUIRED


# ---------------------------------------------------------------------- #
# Arithmetic correctness, once the gate is met (synthetic sequences)
# ---------------------------------------------------------------------- #
def test_win_rate_and_avg_pnl_are_correct(brain):
    trades = [(100.0, "vwap_breach")] * 20 + [(-50.0, "mtm_stop")] * 10
    reading = brain.analyze(trades=trades)
    assert reading.win_rate == pytest.approx(20 / 30 * 100.0, abs=1e-2)
    expected_avg = (20 * 100.0 + 10 * -50.0) / 30
    assert reading.avg_pnl == pytest.approx(expected_avg, abs=1e-6)


def test_current_streak_counts_consecutive_wins_from_the_end(brain):
    trades = [(-10.0, "mtm_stop")] * 25 + [(50.0, "vwap_breach")] * 5
    reading = brain.analyze(trades=trades)
    assert reading.current_streak == 5


def test_current_streak_counts_consecutive_losses_from_the_end(brain):
    trades = [(50.0, "vwap_breach")] * 26 + [(-10.0, "mtm_stop")] * 4
    reading = brain.analyze(trades=trades)
    assert reading.current_streak == -4


def test_streak_signal_flags_winning_streak_at_threshold(brain):
    trades = [(-10.0, "mtm_stop")] * (MIN_TRADES_REQUIRED - STREAK_ALERT_THRESHOLD) + \
             [(50.0, "vwap_breach")] * STREAK_ALERT_THRESHOLD
    reading = brain.analyze(trades=trades)
    assert reading.current_streak == STREAK_ALERT_THRESHOLD
    assert reading.streak_signal is StreakSignal.WINNING_STREAK


def test_streak_signal_flags_losing_streak_at_threshold(brain):
    trades = [(50.0, "vwap_breach")] * (MIN_TRADES_REQUIRED - STREAK_ALERT_THRESHOLD) + \
             [(-10.0, "mtm_stop")] * STREAK_ALERT_THRESHOLD
    reading = brain.analyze(trades=trades)
    assert reading.current_streak == -STREAK_ALERT_THRESHOLD
    assert reading.streak_signal is StreakSignal.LOSING_STREAK


def test_streak_below_threshold_is_normal(brain):
    trades = [(-10.0, "mtm_stop")] * (MIN_TRADES_REQUIRED - 1) + [(50.0, "vwap_breach")] * 1
    reading = brain.analyze(trades=trades)
    assert abs(reading.current_streak) < STREAK_ALERT_THRESHOLD
    assert reading.streak_signal is StreakSignal.NORMAL


def test_exit_reason_breakdown_groups_correctly(brain):
    trades = [(100.0, "vwap_breach")] * 15 + [(-50.0, "mtm_stop")] * 10 + [(30.0, "hard_exit")] * 5
    reading = brain.analyze(trades=trades)
    assert reading.exit_reason_breakdown["vwap_breach"]["count"] == 15
    assert reading.exit_reason_breakdown["vwap_breach"]["win_rate"] == 100.0
    assert reading.exit_reason_breakdown["mtm_stop"]["count"] == 10
    assert reading.exit_reason_breakdown["mtm_stop"]["win_rate"] == 0.0
    assert reading.exit_reason_breakdown["hard_exit"]["avg_pnl"] == pytest.approx(30.0, abs=1e-6)


def test_render_and_to_log_do_not_raise(brain):
    reading = brain.analyze(trades=[(100.0, "vwap_breach")] * MIN_TRADES_REQUIRED)
    assert "BEHAVIOUR BRAIN" in reading.render()
    log = reading.to_log()
    assert log["brain"] == "behaviour"


def test_render_handles_unknown_reading_without_raising(brain):
    reading = brain.analyze(trades=[])
    assert "NOT AVAILABLE" in reading.render()
