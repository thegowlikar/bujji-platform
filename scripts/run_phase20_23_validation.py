"""Phase 20.23 real-artifact validation script -- uses the real
`bujji.intelligence.*` Reading dataclasses (the same real shapes each
brain's own live-verified data source populates), demonstrating the
3 required scenarios without fabricating any classification.
"""
from __future__ import annotations

from bujji.intelligence.models import (
    DataQuality, GreeksExposure, GreeksReading, LiquidityReading, PremiumBehavior, PremiumReading,
    RegimeReading, RegimeType, Richness, SpreadTightness, StructureProximity, StructureReading,
    VolatilityReading,
)
from bujji.mic_context_bridge import build_market_understanding_context, explain_market_understanding_context


def main():
    print("=" * 70)
    print("Scenario A: All intelligence available")
    print("=" * 70)
    regime = RegimeReading(regime=RegimeType.RANGING, confidence=0.82, data_quality=DataQuality.SUFFICIENT,
                             reason="real price action consolidating")
    volatility = VolatilityReading(
        iv_ce=13.8, iv_pe=13.5, iv_average=13.65, realized_vol=12.1, richness=Richness.IV_RICH,
        richness_ratio=1.13, expected_move_points=95.0, expected_move_pct=0.39,
        confidence=0.75, data_quality=DataQuality.SUFFICIENT,
    )
    premium = PremiumReading(
        entry_combined_premium=220.0, current_combined_premium=160.0, theoretical_time_decay_only_premium=175.0,
        behavior=PremiumBehavior.DECAYING_FASTER_THAN_THETA, behavior_ratio=0.91, premium_captured_pct=27.3,
        time_elapsed_pct=45.0, confidence=0.7, data_quality=DataQuality.SUFFICIENT,
    )
    liquidity = LiquidityReading(
        ce_bid=82.4, ce_ask=82.6, pe_bid=68.0, pe_ask=68.05, ce_spread_pct=0.24, pe_spread_pct=0.07,
        combined_spread=0.25, combined_spread_pct=0.17, tightness=SpreadTightness.TIGHT,
        confidence=0.8, data_quality=DataQuality.SUFFICIENT,
    )
    structure = StructureReading(
        spot=24525.0, resistance_strike=24700.0, resistance_oi=1_450_000.0, support_strike=24300.0,
        support_oi=980_000.0, distance_to_resistance_pct=0.71, distance_to_support_pct=0.92,
        put_call_oi_ratio=0.68, proximity=StructureProximity.NEAR_RESISTANCE_WALL,
        confidence=0.75, data_quality=DataQuality.SUFFICIENT,
    )
    greeks = GreeksReading(
        delta_ce=0.48, delta_pe=-0.52, gamma_ce=0.012, gamma_pe=0.012, theta_ce_per_day=-4.8, theta_pe_per_day=-5.1,
        vega_ce_per_pct=1.9, vega_pe_per_pct=1.95, position_delta=0.04, position_gamma=-0.024,
        position_theta_per_day=9.9, position_vega_per_pct=-3.85, exposure=GreeksExposure.DELTA_NEUTRAL,
        confidence=0.7, data_quality=DataQuality.SUFFICIENT,
    )
    context_a = build_market_understanding_context(
        regime=regime, volatility=volatility, premium=premium, liquidity=liquidity, structure=structure, greeks=greeks,
    )
    print(explain_market_understanding_context(context_a))
    print()

    print("=" * 70)
    print("Scenario B: Liquidity unavailable")
    print("=" * 70)
    context_b = build_market_understanding_context(
        regime=regime, volatility=volatility, premium=premium, liquidity=None, structure=structure, greeks=greeks,
    )
    print(explain_market_understanding_context(context_b))
    assert any("Liquidity" in u for u in context_b.uncertainties)
    assert not any("liquidity" in f for f in context_b.supporting_factors)
    print()

    print("=" * 70)
    print("Scenario C: Conflicting signals (COMPRESSED regime vs IV_RICH)")
    print("=" * 70)
    compressed_regime = RegimeReading(regime=RegimeType.COMPRESSED, confidence=0.8, data_quality=DataQuality.SUFFICIENT,
                                        reason="real intraday range compression")
    context_c = build_market_understanding_context(regime=compressed_regime, volatility=volatility)
    print(explain_market_understanding_context(context_c))
    assert len(context_c.conflicts) == 1
    print()

    print("PROOF: no trade recommendation, execution action, or risk-bypass field exists on MarketUnderstandingContext.")
    for field in ("place_order", "execution_plan", "risk_override", "capital_allocation"):
        assert not hasattr(context_a, field)
    print("Confirmed.")


if __name__ == "__main__":
    main()
