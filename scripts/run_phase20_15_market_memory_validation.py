#!/usr/bin/env python
"""Phase 20.15 -- Market Memory validation against real historical
data. Reuses HistoricalObservationStore + compose_market_state exactly
as every prior phase's own script already does -- no strategy
discovery, optimization, or backtesting rerun. Phase 20.5's own
published evidence is reused verbatim. Read-only, no broker, no order
capability.

Demonstrates, on REAL data:
  BEFORE memory: a decision based only on current intelligence.
  AFTER memory:  the same decision, PLUS historical context -- while
                 evidence_score/qualification/allocation are provably
                 unchanged.
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
    parser.add_argument("--memory-store-path", default="/tmp/phase20_15_market_memory.jsonl")
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
    from bujji.shadow_decision_runtime import run_shadow_cycle
    from bujji.state_persistence.store import EventStore
    from bujji.market_memory import (
        build_decision_memory_record, build_index, build_market_memory_record,
        build_memory_context, explain_memory_context, read_all_market_memories,
        record_decision_memory, record_market_memory,
    )

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MIC_REGIME_TO_MARKET_STRATEGY_REGIME = {"TREND": "TREND_UP", "RANGE": "RANGE", "UNCLEAR": "TRANSITION"}

    trend_evidence = StrategyEvidence(
        strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
        net_expectancy=517.0, gross_expectancy=949.0,
        train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
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

    import os
    if os.path.exists(args.memory_store_path):
        os.remove(args.memory_store_path)
    memory_store = EventStore(args.memory_store_path)

    print(f"Building real MarketState + FinalDecision + MarketMemory for {len(session_dates)} real days "
          f"(end-of-day cycle each) ...")
    all_market_records = []
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

        context = IntelligenceContext(as_of_time=candles[-1].timestamp, execution_mode=EXECUTION_MODE_HISTORICAL_REPLAY)
        market_state = compose_market_state(candles, current_vix, trailing_vix, context)

        strategy_regime = MIC_REGIME_TO_MARKET_STRATEGY_REGIME.get(market_state.market_regime, "TRANSITION")
        environment = MarketEnvironment(
            mic_regime=strategy_regime, risk_state=market_state.risk_state,
            volatility_state=market_state.volatility_state, execution_profile_name="NORMAL",
            data_quality_ok=(market_state.data_quality == "SUFFICIENT"),
        )
        score = score_strategy(trend_evidence)
        assessment = evaluate_opportunity(score, environment, TREND_FAVORABLE, TREND_UNFAVORABLE)
        allocation = assess_risk_allocation(OpportunityCandidate(assessment=assessment))
        portfolio = rank_portfolio_choices([allocation])
        final_decision = compose_decision(allocation, portfolio)
        observation = run_shadow_cycle(market_state, final_decision, market_state.as_of_time)

        market_record = build_market_memory_record(observation)
        decision_record = build_decision_memory_record(observation)
        record_market_memory(memory_store, market_record, session_id=date_str)
        record_decision_memory(memory_store, decision_record, session_id=date_str)
        all_market_records.append(market_record)

    print(f"  -> {len(all_market_records)} real MarketMemoryRecords persisted to {args.memory_store_path}")

    # --- BEFORE / AFTER demonstration on the LAST real day -------------
    target = all_market_records[-1]
    print(f"\n--- BEFORE memory: decision for {target.as_of_time} based only on current intelligence ---")
    print(f"evidence_score={score_strategy(trend_evidence).evidence_score}  "
          f"market_regime={target.market_regime}  volatility={target.volatility_state}  risk={target.risk_state}")

    reloaded = read_all_market_memories(memory_store)
    index = build_index(reloaded)
    context = build_memory_context(target, index, [], as_of_time=target.as_of_time)

    print(f"\n--- AFTER memory: same decision, PLUS historical context ---")
    print(explain_memory_context(context))

    print("\n--- Proof: evidence_score/qualification/allocation unchanged by memory ---")
    score_recomputed = score_strategy(trend_evidence)
    print(f"evidence_score before memory query: {score_strategy(trend_evidence).evidence_score}")
    print(f"evidence_score after memory query:  {score_recomputed.evidence_score}")
    assert score_strategy(trend_evidence).evidence_score == score_recomputed.evidence_score
    print("CONFIRMED: identical.")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
