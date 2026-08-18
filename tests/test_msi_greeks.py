"""Tests -- Phase 15E Greeks Intelligence (msi_greeks + greeks_adapter)."""
from __future__ import annotations

from datetime import datetime, timezone

from bujji.market_perception.greeks_adapter import build_greeks_assessment
from bujji.market_perception.models import (
    HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
)
from bujji.msi_greeks.engine import assess_atm_greeks


def _leg(strike, option_type, bid, ask):
    return OptionLeg(symbol=f"{option_type}{strike}", strike=strike, option_type=option_type, ltp=None,
                      bid=bid, ask=ask, spread=(ask - bid) if bid and ask else None, volume=None,
                      open_interest=10000.0, iv=None, delta=None, gamma=None, theta=None, vega=None)


def _snapshot(spot=24450.0, ce=(129.0, 131.0), pe=(119.0, 121.0), expiry="2026-08-13", ts="2026-08-04T09:15:00+05:30"):
    legs = (_leg(24450.0, "CE", *ce), _leg(24450.0, "PE", *pe))
    chain = OptionChainSnapshot(underlying="NIFTY", expiry=expiry, atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    return MarketSnapshot(
        snapshot_version="1.0", timestamp=ts, source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(),
        spot=SpotSnapshot(symbol="NIFTY", ltp=spot), vix=VixSnapshot(value=13.0),
        futures=None, option_chain=chain,
    )


NOW = datetime(2026, 8, 4, 9, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Engine-level: real Black-Scholes sanity.
# ---------------------------------------------------------------------------
def test_atm_delta_near_half_for_both_legs():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available and r.pe.available
    assert 0.3 < r.ce.delta < 0.7
    assert -0.7 < r.pe.delta < -0.3


def test_gamma_positive_for_both_legs():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.gamma > 0
    assert r.pe.gamma > 0


def test_theta_negative_for_both_legs():
    """Time decay -- a long option's theta must be negative (real
    textbook property), never positive/fabricated."""
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.theta_per_day < 0
    assert r.pe.theta_per_day < 0


def test_vega_positive_for_both_legs():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.vega_per_pct > 0
    assert r.pe.vega_per_pct > 0


def test_deep_itm_ce_delta_approaches_one():
    # Deep ITM by 1050 points -- enough real time value margin (70 pts
    # over 20 days) for the solver to find a real IV within its [1%,
    # 300%] bounds; too little time value (e.g. only 30 pts) implies an
    # IV below the solver's honest lower bound, which correctly refuses
    # rather than guessing -- see test_premium_below_intrinsic_is_unknown_not_fabricated.
    r = assess_atm_greeks(spot=25500.0, strike=24450.0, t_years=20 / 365, ce_premium=1150.0, pe_premium=5.0, timestamp="t")
    assert r.ce.available
    assert r.ce.delta > 0.9


def test_deep_otm_ce_delta_approaches_zero():
    r = assess_atm_greeks(spot=23000.0, strike=24450.0, t_years=7 / 365, ce_premium=2.0, pe_premium=1450.0, timestamp="t")
    assert r.ce.available
    assert r.ce.delta < 0.1


def test_missing_premium_is_unknown_not_fabricated():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=None, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False
    assert r.ce.reason == "missing_premium"
    assert r.ce.delta is None
    assert r.pe.available is True  # PE independently still computable.


def test_zero_or_negative_time_to_expiry_is_unknown():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=0.0, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False
    assert "time_to_expiry" in r.ce.reason
    r2 = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=-0.01, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r2.ce.available is False


def test_missing_spot_is_unknown():
    r = assess_atm_greeks(spot=None, strike=24450.0, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False
    assert "spot" in r.ce.reason


def test_missing_strike_is_unknown():
    r = assess_atm_greeks(spot=24450.0, strike=None, t_years=7 / 365, ce_premium=130.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False


def test_premium_below_intrinsic_is_unknown_not_fabricated():
    """A CE premium below (spot - strike) is an arbitrage-violating /
    stale quote -- solve_implied_volatility already refuses this; the
    Greeks layer must honor that refusal, never solve anyway."""
    r = assess_atm_greeks(spot=25500.0, strike=24450.0, t_years=7 / 365, ce_premium=1.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False
    assert "iv_unsolvable" in r.ce.reason


def test_zero_premium_is_unknown():
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=0.0, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False


def test_ce_and_pe_solved_independently():
    """One leg failing must never suppress the other leg's real,
    independently-computable Greeks."""
    r = assess_atm_greeks(spot=24450.0, strike=24450.0, t_years=7 / 365, ce_premium=None, pe_premium=120.0, timestamp="t")
    assert r.ce.available is False
    assert r.pe.available is True
    assert r.pe.delta is not None


# ---------------------------------------------------------------------------
# Adapter-level: real MarketSnapshot -> GreeksAssessment.
# ---------------------------------------------------------------------------
def test_adapter_builds_real_assessment_from_snapshot():
    snapshot = _snapshot()
    result = build_greeks_assessment(snapshot, NOW)
    assert result is not None
    assert result.ce.available
    assert result.pe.available
    assert result.spot == 24450.0
    assert result.strike == 24450.0


def test_adapter_returns_none_when_no_option_chain():
    snapshot = _snapshot()
    import dataclasses
    snapshot = dataclasses.replace(snapshot, option_chain=None)
    assert build_greeks_assessment(snapshot, NOW) is None


def test_adapter_returns_none_when_spot_missing():
    snapshot = _snapshot()
    import dataclasses
    snapshot = dataclasses.replace(snapshot, spot=SpotSnapshot(symbol="NIFTY", ltp=None))
    assert build_greeks_assessment(snapshot, NOW) is None


def test_adapter_degrades_one_leg_when_one_sided_quote():
    snapshot = _snapshot(ce=(None, None))
    result = build_greeks_assessment(snapshot, NOW)
    assert result is not None
    assert result.ce.available is False
    assert result.pe.available is True


def test_adapter_past_expiry_is_unknown_both_legs():
    snapshot = _snapshot(expiry="2020-01-01")
    result = build_greeks_assessment(snapshot, NOW)
    assert result is not None
    assert result.ce.available is False
    assert result.pe.available is False
    assert result.t_years is None
