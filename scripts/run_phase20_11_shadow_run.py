#!/usr/bin/env python
"""Phase 20.11 -- Shadow Decision Runtime historical replay.

Read-only. Reuses `HistoricalObservationStore` (Phase 15Q/20.0) and
`compose_market_state` (Phase 20.1) EXACTLY as `scripts/run_mic_v0_
validation.py` (Phase 20.1B) already does, to classify real trading
days -- no strategy discovery, optimization, or backtesting is rerun;
Phase 20.5's own published Trend Following / Mean Reversion evidence
(Phase 20.4's real findings) is reused verbatim for every cycle. No
broker connection, no paper execution, no order capability -- this
script only ever calls `run_shadow_cycle()` and records
`DecisionObservation`s.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, "/opt/bujji/app")


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-store-path",
                         default="/opt/bujji/app/data/historical_reality/normalized/historical_observations.db")
    parser.add_argument("--instrument", default="NIFTY_FUT_CONTINUOUS")
    parser.add_argument("--start-date", default="2026-01-01T00:00:00+05:30")
    parser.add_argument("--end-date", default="2026-08-13T23:59:59+05:30")
    parser.add_argument("--report-out", default=None)
    args = parser.parse_args()

    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, IntelligenceContext
    from bujji.core.models import Candle as CoreCandle
    from bujji.mic_v0.engine import compose_market_state
    from bujji.strategy_intelligence import StrategyEvidence, score_strategy
    from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
    from bujji.opportunity_ranking import OpportunityCandidate
    from bujji.capital_intelligence import assess_risk_allocation
    from bujji.opportunity_portfolio import rank_portfolio_choices
    from bujji.decision_orchestration import compose_decision
    from bujji.shadow_decision_runtime import ShadowDecisionLog, explain_observation, run_shadow_cycle

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

    # Phase 20.5's own published Cycle-1 evidence -- verbatim, never rederived.
    trend_evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
    )
    mr_evidence = StrategyEvidence(
        strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
        net_expectancy=-1664.0, gross_expectancy=-1218.0,
        train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
    )

    store = HistoricalObservationStore(args.historical_store_path)

    print(f"Fetching real {args.instrument} FIVE_MINUTE rows {args.start_date} .. {args.end_date} ...")
    futures_rows = store.range(args.instrument, "FIVE_MINUTE", args.start_date, args.end_date)
    print(f"  -> {len(futures_rows)} real rows")

    print("Fetching real India VIX DAILY history ...")
    vix_rows = store.range("NSE:INDIAVIX-INDEX", "DAILY", "2008-01-01T00:00:00+05:30", args.end_date)
    vix_by_date = {r.observation.identity.timestamp[:10]: r.payload["close"] for r in vix_rows}
    sorted_vix_dates = sorted(vix_by_date)
    print(f"  -> {len(vix_by_date)} real VIX daily closes")

    by_date = defaultdict(list)
    for r in futures_rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    log = ShadowDecisionLog()
    days_processed = 0
    days_skipped_no_vix = 0

    MIC_REGIME_TO_MARKET_STRATEGY_REGIME = {"TREND": "TREND_UP", "RANGE": "RANGE", "UNCLEAR": "TRANSITION"}

    print(f"Running shadow cycles over {len(by_date)} real trading days ...")
    for date_str in sorted(by_date):
        if date_str not in vix_by_date:
            days_skipped_no_vix += 1
            continue  # honest skip -- no fabricated VIX value for a missing date.

        rows = sorted(by_date[date_str], key=lambda r: r.observation.identity.timestamp)
        if len(rows) < 6:
            continue
        candles = [
            CoreCandle(
                timestamp=datetime.fromisoformat(r.observation.identity.timestamp),
                open=r.payload["open"], high=r.payload["high"], low=r.payload["low"], close=r.payload["close"],
                volume=r.payload.get("volume") or 0.0,
            )
            for r in rows
        ]
        idx = sorted_vix_dates.index(date_str)
        trailing_vix = [vix_by_date[d] for d in sorted_vix_dates[:idx]]
        current_vix = vix_by_date[date_str]

        as_of = candles[-1].timestamp
        context = IntelligenceContext(as_of_time=as_of, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
        market_state = compose_market_state(candles, current_vix, trailing_vix, context)
        days_processed += 1

        strategy_regime = MIC_REGIME_TO_MARKET_STRATEGY_REGIME.get(market_state.market_regime, "TRANSITION")
        environment = MarketEnvironment(
            mic_regime=strategy_regime, risk_state=market_state.risk_state,
            volatility_state=market_state.volatility_state, execution_profile_name="NORMAL",
            data_quality_ok=(market_state.data_quality == "SUFFICIENT"),
        )

        for evidence, favorable, unfavorable in (
            (trend_evidence, TREND_FAVORABLE, TREND_UNFAVORABLE),
            (mr_evidence, MR_FAVORABLE, MR_UNFAVORABLE),
        ):
            score = score_strategy(evidence)
            assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
            allocation = assess_risk_allocation(OpportunityCandidate(assessment=assessment))
            portfolio = rank_portfolio_choices([allocation])
            final_decision = compose_decision(allocation, portfolio)
            observation = run_shadow_cycle(market_state, final_decision, market_state.as_of_time)
            log.record(observation)

    print(f"  -> {days_processed} real trading days processed, {days_skipped_no_vix} skipped (no VIX)")
    print(f"  -> {len(log.observations)} DecisionObservations recorded")
    print()

    summary = log.summarize(f"{args.start_date[:10]}_to_{args.end_date[:10]}")
    print(summary.render())
    print()

    print("--- Sample observations (first 3) ---")
    for obs in log.observations[:3]:
        print(explain_observation(obs))
        print()

    print("--- Proof: evidence_score constant across every real day above ---")
    print(f"Trend Following evidence_score: {score_strategy(trend_evidence).evidence_score}")
    print(f"Mean Reversion evidence_score: {score_strategy(mr_evidence).evidence_score}")

    if args.report_out:
        with open(args.report_out, "w") as f:
            f.write(summary.render() + "\n\n")
            for obs in log.observations:
                f.write(explain_observation(obs) + "\n\n")
        print(f"\nFull raw output written to {args.report_out}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
