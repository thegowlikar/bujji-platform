#!/usr/bin/env python
"""Phase 20.12 -- Shadow Market Campaign framework validation.

IMPORTANT SCOPE NOTE (see the phase report's own Limitations section):
this script validates the `bujji.shadow_market_campaign` layer
(collector/validator/report) against REAL historical trading days --
it does NOT constitute the phase's own "20-30 live NSE sessions"
requirement, which needs the runtime to actually run during real,
FUTURE market hours (09:15-15:30 IST) across the coming weeks. This
script proves the framework computes correctly on real data; it is not
a substitute for that live operational campaign.

Reuses HistoricalObservationStore + compose_market_state exactly as
Phase 20.1B/20.11's own scripts already do -- no strategy discovery,
optimization, or backtesting rerun. Phase 20.5's own published
evidence is reused verbatim. Read-only, no broker, no order capability.
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
    parser.add_argument("--start-date", default="2026-08-01T00:00:00+05:30")
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
    from bujji.shadow_decision_runtime import ShadowDecisionLog, run_shadow_cycle
    from bujji.shadow_market_campaign import (
        build_campaign_report, build_campaign_session, validate_session_behavior,
    )

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")
    MIC_REGIME_TO_MARKET_STRATEGY_REGIME = {"TREND": "TREND_UP", "RANGE": "RANGE", "UNCLEAR": "TRANSITION"}

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
    vix_rows = store.range("NSE:INDIAVIX-INDEX", "DAILY", "2008-01-01T00:00:00+05:30", args.end_date)
    vix_by_date = {r.observation.identity.timestamp[:10]: r.payload["close"] for r in vix_rows}
    sorted_vix_dates = sorted(vix_by_date)

    by_date = defaultdict(list)
    for r in futures_rows:
        by_date[r.observation.identity.timestamp[:10]].append(r)

    session_dates = sorted(d for d in by_date if d in vix_by_date)
    print(f"Building one CampaignSession per real trading day, {len(session_dates)} candidate real days ...")

    all_reports = []
    sessions_built = 0
    for date_str in session_dates:
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

        log = ShadowDecisionLog()
        # Each real 5-minute candle in the day becomes one shadow cycle,
        # feeding compose_market_state a growing real prefix (no lookahead) --
        # exercising the campaign layer's stability/coverage metrics across a
        # genuinely varying real intraday sequence, not one end-of-day snapshot.
        for i in range(5, len(candles)):
            prefix = candles[: i + 1]
            as_of = prefix[-1].timestamp
            context = IntelligenceContext(as_of_time=as_of, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
            market_state = compose_market_state(prefix, current_vix, trailing_vix, context)
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

        day_open = f"{date_str}T09:15:00+05:30"
        day_close = f"{date_str}T15:30:00+05:30"
        session = build_campaign_session(date_str, day_open, day_close, log)
        metrics = validate_session_behavior(session, log.observations)
        all_reports.append((session, metrics))
        sessions_built += 1
        print(f"  {date_str}: {session.observation_count} observations, health={session.health_status}, "
              f"stability={metrics.decision_stability}, coverage={metrics.market_coverage_pct:.0%}")

    print(f"\n{sessions_built} real CampaignSessions built (framework validation only -- "
          f"NOT the phase's own live 20-30 NSE session requirement).")

    if all_reports:
        print("\n--- Sample report (last session) ---")
        session, metrics = all_reports[-1]
        print(build_campaign_report(session, metrics))

    if args.report_out:
        with open(args.report_out, "w") as f:
            for session, metrics in all_reports:
                f.write(build_campaign_report(session, metrics) + "\n\n")
        print(f"\nFull raw output written to {args.report_out}")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
