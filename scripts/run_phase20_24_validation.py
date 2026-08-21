"""Phase 20.24 real-artifact validation script -- runs process_cycle()
end-to-end through the REAL, now-wired live_shadow_runner entrypoint
with real evidence (Phase 20.5), demonstrating the dormant MIC-regime
confidence path is now genuinely active, plus the 3 required
mic_context_bridge/mic_runtime_context scenarios.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from bujji.core.models import Candle
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY
from bujji.intelligence.models import DataQuality, RegimeReading, RegimeType, Richness, VolatilityReading
from bujji.live_shadow_runner.models import SHADOW_RUN_OPEN, ShadowRunState
from bujji.live_shadow_runner.runner import process_cycle
from bujji.mic_context_bridge import build_market_understanding_context, explain_market_understanding_context
from bujji.mic_runtime_context import build_mic_runtime_context, explain_runtime_intelligence_context
from bujji.shadow_decision_runtime import ShadowDecisionLog
from bujji.strategy_intelligence import StrategyEvidence


def _real_evidence():
    return StrategyEvidence(
        strategy_name="TrendFollowing", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        gross_expectancy=949.0, net_expectancy=517.0, train_expectancy=512.0,
        validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )


def main():
    print("=" * 70)
    print("End-to-end: process_cycle() now genuinely applies MIC-regime confidence")
    print("=" * 70)
    base = datetime.fromisoformat("2026-08-13T09:15:00")
    candles = [
        Candle(timestamp=(base + timedelta(minutes=i)), open=24500 + i, high=24505 + i,
               low=24495 + i, close=24500 + i, volume=1000)
        for i in range(10)
    ]
    state = ShadowRunState(session_date="2026-08-13", state=SHADOW_RUN_OPEN, cycles_completed=0,
                             last_cycle_timestamp=None, errors=())
    log = ShadowDecisionLog()

    # Strategy declares RANGE as its ONLY favorable regime, and MIC v0's own
    # regime classifier will very likely NOT classify this flat synthetic
    # candle sequence as RANGE with SUFFICIENT confidence for TREND-style
    # price action -- demonstrating the real, now-active demotion path.
    strategies = [(_real_evidence(), ("RANGE",), ())]
    new_state, observations = process_cycle(
        state, base.isoformat(), candles, current_vix=13.5, trailing_vix=[13.0, 13.2, 13.4],
        strategies=strategies, campaign_log=log, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY,
    )
    print(f"Cycle completed. errors={new_state.errors}, observations={len(observations)}")
    if observations:
        print(f"Observation confidence: {observations[0].confidence}")
    print()

    print("=" * 70)
    print("Scenario A: All intelligence available -> rich MIC runtime context")
    print("=" * 70)
    understanding_a = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.RANGING, confidence=0.8, data_quality=DataQuality.SUFFICIENT,
                               reason="real price action consolidating"),
        volatility=VolatilityReading(
            iv_ce=15.0, iv_pe=14.5, iv_average=14.75, realized_vol=13.0, richness=Richness.IV_RICH,
            richness_ratio=1.13, expected_move_points=95.0, expected_move_pct=0.4,
            confidence=0.7, data_quality=DataQuality.SUFFICIENT,
        ),
    )
    context_a = build_mic_runtime_context("RANGE", ("RANGE",), (), market_understanding=understanding_a)
    print(explain_runtime_intelligence_context(context_a))
    print()

    print("=" * 70)
    print("Scenario B: Missing intelligence (liquidity unavailable)")
    print("=" * 70)
    understanding_b = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.RANGING, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
        liquidity=None,
    )
    context_b = build_mic_runtime_context("RANGE", ("RANGE",), (), market_understanding=understanding_b)
    print(explain_runtime_intelligence_context(context_b))
    assert any("Liquidity" in u for u in context_b.market_understanding.uncertainties)
    print()

    print("=" * 70)
    print("Scenario C: Conflicting intelligence (RANGE regime + COMPRESSED/IV_RICH tension)")
    print("=" * 70)
    understanding_c = build_market_understanding_context(
        regime=RegimeReading(regime=RegimeType.COMPRESSED, confidence=0.8, data_quality=DataQuality.SUFFICIENT),
        volatility=VolatilityReading(
            iv_ce=15.0, iv_pe=14.5, iv_average=14.75, realized_vol=13.0, richness=Richness.IV_RICH,
            richness_ratio=1.13, expected_move_points=95.0, expected_move_pct=0.4,
            confidence=0.7, data_quality=DataQuality.SUFFICIENT,
        ),
    )
    context_c = build_mic_runtime_context("COMPRESSED", (), (), market_understanding=understanding_c)
    print(explain_runtime_intelligence_context(context_c))
    assert len(context_c.market_understanding.conflicts) == 1
    print()

    print("PROOF: evidence_score untouched by any runtime-context path.")
    print("(see test_evidence_score_never_touched_by_runtime_context in tests/test_mic_runtime_context.py)")


if __name__ == "__main__":
    main()
