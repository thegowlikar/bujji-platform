"""Tests -- Phase 15E Premium Behaviour Intelligence."""
from __future__ import annotations

import dataclasses
import json

from bujji.premium_behaviour.engine import evaluate
from bujji.premium_behaviour.models import (
    ACCEL_ACCELERATING, ACCEL_DECELERATING, DIRECTION_FALLING, DIRECTION_RISING, DIRECTION_STEADY,
    DIRECTION_UNKNOWN, PremiumBehaviourState, PremiumObservation,
)
from bujji.premium_behaviour.recovery import hydrate_premium_behaviour
from bujji.state_persistence.models import RECOVERY_COMPLETE, RECOVERY_PARTIAL


def advance_all(state, obs_list):
    for o in obs_list:
        state = state.advance(o)
    return state


# ---------------------------------------------------------------------------
# Absolute behaviour.
# ---------------------------------------------------------------------------
def test_no_observations_yet_is_unknown_not_flat():
    reading = evaluate(PremiumBehaviourState())
    assert reading.ce.direction == DIRECTION_UNKNOWN
    assert reading.combined.direction == DIRECTION_UNKNOWN
    assert reading.confidence == "NONE"


def test_single_observation_is_unknown_not_flat():
    """One real observation is not enough to claim ANY direction --
    'no movement yet' must never be reported as STEADY/bearish/bullish."""
    state = PremiumBehaviourState().advance(PremiumObservation("t0", 100.0, 90.0, 24000.0))
    reading = evaluate(state)
    assert reading.ce.direction == DIRECTION_UNKNOWN
    assert reading.pe.direction == DIRECTION_UNKNOWN


def test_ce_rising_detected():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 120.0, 88.0, 24000.0),
    ])
    reading = evaluate(state)
    assert reading.ce.direction == DIRECTION_RISING


def test_pe_falling_detected():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 100.0, 70.0, 24000.0),
    ])
    reading = evaluate(state)
    assert reading.pe.direction == DIRECTION_FALLING


def test_steady_within_threshold_not_a_coin_flip():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 100.1, 90.0, 24000.0),  # +0.1% -- well under the 0.5% steady threshold.
    ])
    reading = evaluate(state)
    assert reading.ce.direction == DIRECTION_STEADY


def test_acceleration_requires_three_real_observations():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 110.0, 90.0, 24000.0),
    ])
    reading = evaluate(state)
    assert reading.ce.acceleration == "UNKNOWN"


def test_accelerating_rise_detected():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 105.0, 90.0, 24000.0),   # +5%
        PremiumObservation("t2", 120.0, 90.0, 24000.0),   # +14.3% -- rate increased.
    ])
    reading = evaluate(state)
    assert reading.ce.direction == DIRECTION_RISING
    assert reading.ce.acceleration == ACCEL_ACCELERATING


def test_decelerating_rise_detected():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 120.0, 90.0, 24000.0),   # +20%
        PremiumObservation("t2", 122.0, 90.0, 24000.0),   # +1.7% -- rate decreased.
    ])
    reading = evaluate(state)
    assert reading.ce.acceleration == ACCEL_DECELERATING


# ---------------------------------------------------------------------------
# Relative behaviour.
# ---------------------------------------------------------------------------
def test_ce_expanding_faster_than_pe():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 100.0, 24000.0),
        PremiumObservation("t1", 130.0, 105.0, 24000.0),  # CE +30%, PE +5%.
    ])
    reading = evaluate(state)
    assert reading.ce_vs_pe_relative == "CE_EXPANDING_FASTER"


def test_symmetric_ce_pe_expansion():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 100.0, 24000.0),
        PremiumObservation("t1", 110.0, 110.0, 24000.0),
    ])
    reading = evaluate(state)
    assert reading.ce_vs_pe_relative == "SYMMETRIC"


def test_premium_vs_underlying_co_expanding():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 130.0, 100.0, 24300.0),  # combined rises, spot rises meaningfully (+1.25%).
    ])
    reading = evaluate(state)
    assert reading.premium_vs_underlying == "CO_EXPANDING"


def test_premium_vs_underlying_diverging():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 60.0, 50.0, 24300.0),  # combined falls while spot rises meaningfully.
    ])
    reading = evaluate(state)
    assert reading.premium_vs_underlying == "DIVERGING"


def test_premium_vs_underlying_unknown_when_spot_flat():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", 130.0, 100.0, 24005.0),  # spot barely moved (well under 0.5%).
    ])
    reading = evaluate(state)
    assert reading.premium_vs_underlying == DIRECTION_UNKNOWN


# ---------------------------------------------------------------------------
# Missing data / UNKNOWN preservation.
# ---------------------------------------------------------------------------
def test_missing_ce_premium_excluded_not_fabricated():
    state = advance_all(PremiumBehaviourState(), [
        PremiumObservation("t0", 100.0, 90.0, 24000.0),
        PremiumObservation("t1", None, 95.0, 24000.0),   # a real cycle with no CE quote.
        PremiumObservation("t2", 130.0, 100.0, 24000.0),
    ])
    reading = evaluate(state)
    # CE direction compares the two REAL CE values (t0 -> t2), skipping the None.
    assert reading.ce.direction == DIRECTION_RISING
    assert reading.ce.current_value == 130.0
    assert reading.ce.previous_value == 100.0


def test_combined_premium_none_when_either_leg_missing():
    obs = PremiumObservation("t0", 100.0, None, 24000.0)
    assert obs.combined_premium is None


# ---------------------------------------------------------------------------
# Window/lookback bound.
# ---------------------------------------------------------------------------
def test_lookback_window_is_bounded():
    state = PremiumBehaviourState(lookback=3)
    for i in range(10):
        state = state.advance(PremiumObservation(f"t{i}", 100.0 + i, 90.0, 24000.0))
    assert len(state.history) == 3
    assert state.history[0].timestamp == "t7"
    assert state.history[-1].timestamp == "t9"


def test_confidence_scales_with_real_history():
    state = PremiumBehaviourState()
    confidences = []
    for i in range(6):
        state = state.advance(PremiumObservation(f"t{i}", 100.0 + i, 90.0, 24000.0))
        confidences.append(evaluate(state).confidence)
    assert confidences[0] == "NONE"
    assert confidences[-1] == "HIGH"


# ---------------------------------------------------------------------------
# Recovery.
# ---------------------------------------------------------------------------
def _write_snapshots(path, obs_dicts):
    """obs_dicts: list of (ts, ce_bid, ce_ask, pe_bid, pe_ask, spot).
    Writes real MarketSnapshot-shaped JSONL, matching
    ShadowSessionRunner's own _snapshot_to_dict output."""
    from bujji.market_perception.models import (
        HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
    )
    with open(path, "w") as f:
        for ts, ce_bid, ce_ask, pe_bid, pe_ask, spot in obs_dicts:
            legs = (
                OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=ce_bid, ask=ce_ask,
                          spread=None, volume=None, open_interest=1000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
                OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=pe_bid, ask=pe_ask,
                          spread=None, volume=None, open_interest=1000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
            )
            chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-13", atm_strike=24450.0,
                                         config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
            snapshot = MarketSnapshot(
                snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
                health_status=HEALTH_OK, missing_fields=(),
                spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
                futures=None, option_chain=chain,
            )
            f.write(json.dumps(dataclasses.asdict(snapshot), default=str) + "\n")


def test_recovery_missing_file_is_recovery_complete(tmp_path):
    state, report = hydrate_premium_behaviour(str(tmp_path / "nope.jsonl"))
    assert report.status == RECOVERY_COMPLETE
    assert state == PremiumBehaviourState()


def test_recovery_matches_continuous_replay(tmp_path):
    obs = [
        ("t0", 99.0, 101.0, 89.0, 91.0, 24000.0),
        ("t1", 109.0, 111.0, 84.0, 86.0, 24050.0),
        ("t2", 119.0, 121.0, 79.0, 81.0, 24120.0),
        ("t3", 129.0, 131.0, 74.0, 76.0, 24200.0),
    ]
    path = str(tmp_path / "market_snapshots.jsonl")
    _write_snapshots(path, obs)

    hydrated_state, report = hydrate_premium_behaviour(path)
    assert report.status == RECOVERY_COMPLETE
    assert report.events_replayed == 4

    # Reference: build the identical PremiumObservation sequence directly.
    ref_state = PremiumBehaviourState()
    for ts, ce_bid, ce_ask, pe_bid, pe_ask, spot in obs:
        ref_state = ref_state.advance(PremiumObservation(ts, (ce_bid + ce_ask) / 2, (pe_bid + pe_ask) / 2, spot))

    assert hydrated_state == ref_state
    assert evaluate(hydrated_state).to_dict() == evaluate(ref_state).to_dict()


def test_recovery_with_torn_final_line_degrades_safely(tmp_path):
    obs = [
        ("t0", 99.0, 101.0, 89.0, 91.0, 24000.0),
        ("t1", 109.0, 111.0, 84.0, 86.0, 24050.0),
    ]
    path = str(tmp_path / "market_snapshots.jsonl")
    _write_snapshots(path, obs)
    with open(path, "a") as f:
        f.write('{"snapshot_version": "1.0", "timestamp": "2026-08-04T09:2')  # torn.

    state, report = hydrate_premium_behaviour(path)
    assert report.status == RECOVERY_PARTIAL
    assert report.events_skipped_malformed == 1
    assert report.events_replayed == 2
    assert len(state.history) == 2
