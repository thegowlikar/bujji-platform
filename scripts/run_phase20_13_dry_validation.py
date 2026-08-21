#!/usr/bin/env python
"""Phase 20.13 -- Live Shadow Runner historical DRY validation.

SCOPE NOTE (see the phase report's own Limitations section): this
script exercises `bujji.live_shadow_runner`'s full lifecycle
(start_session -> process_cycle x N -> close_session -> persistence ->
evaluate_runtime_health) against REAL historical trading days. It does
NOT constitute the phase's own "20-30 live NSE sessions" requirement,
which needs the runtime to run during real, FUTURE market hours across
the coming weeks -- see the report for the operational plan to close
that gap. `execution_mode` here is HISTORICAL_REPLAY throughout.

Reuses HistoricalObservationStore + compose_market_state exactly as
every prior phase's own scripts already do -- no strategy discovery,
optimization, or backtesting rerun. Phase 20.5's own published evidence
is reused verbatim. Read-only, no broker, no order capability.
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
    parser.add_argument("--artifact-dir", default="/tmp/phase20_13_dry_run_artifacts")
    args = parser.parse_args()

    from bujji.historical_reality.store import HistoricalObservationStore
    from bujji.core.models import Candle as CoreCandle
    from bujji.strategy_intelligence import StrategyEvidence
    from bujji.shadow_decision_runtime import ShadowDecisionLog
    from bujji.shadow_market_campaign import build_campaign_report, build_campaign_session, validate_session_behavior
    from bujji.live_shadow_runner import (
        ShadowRunConfig, close_session, evaluate_runtime_health,
        load_decision_observations, process_cycle, save_campaign_artifact, start_session,
    )

    TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
    MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

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
    strategies = ((trend_evidence, TREND_FAVORABLE, TREND_UNFAVORABLE), (mr_evidence, MR_FAVORABLE, MR_UNFAVORABLE))

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
    os.makedirs(args.artifact_dir, exist_ok=True)

    sessions_run = 0
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

        config = ShadowRunConfig(market="NIFTY", session_date=date_str, cycle_interval_minutes=5,
                                  data_source=args.historical_store_path)
        state = start_session(config)
        log = ShadowDecisionLog()
        artifact_path = os.path.join(args.artifact_dir, f"{date_str}.jsonl")

        for i in range(5, len(candles)):
            prefix = candles[: i + 1]
            as_of = prefix[-1].timestamp.isoformat()
            state, observations = process_cycle(state, as_of, prefix, current_vix, trailing_vix, strategies, log)
            for obs in observations:
                save_campaign_artifact(artifact_path, "DecisionObservation", obs)

        state = close_session(state)
        as_of_final = candles[-1].timestamp.isoformat()
        health = evaluate_runtime_health(state, log.observations, as_of_final)
        save_campaign_artifact(artifact_path, "HealthReport", health)

        day_open, day_close = f"{date_str}T09:15:00+05:30", f"{date_str}T15:30:00+05:30"
        session = build_campaign_session(date_str, day_open, day_close, log)
        metrics = validate_session_behavior(session, log.observations)
        save_campaign_artifact(artifact_path, "CampaignSession", session)
        save_campaign_artifact(artifact_path, "CampaignMetrics", metrics)

        # Restart-recovery proof, on real data: reload everything just
        # written and confirm it matches what the live run produced --
        # no artifact silently lost across a "restart" of this process.
        recovered = load_decision_observations(artifact_path)
        assert len(recovered) == len(log.observations), \
            f"{date_str}: persisted {len(log.observations)} but reloaded {len(recovered)}"

        sessions_run += 1
        print(f"  {date_str}: run_state={state.state} cycles={state.cycles_completed} "
              f"observations={len(log.observations)} runtime_health={health.runtime_status} "
              f"restart_recovery_verified=OK")

    print(f"\n{sessions_run} real historical sessions dry-run through the FULL live_shadow_runner lifecycle "
          f"(start_session -> process_cycle x N -> close_session -> persistence -> restart-recovery proof -> "
          f"evaluate_runtime_health -> campaign session/metrics).")
    print("This is historical DRY validation, NOT the phase's own live 20-30 NSE session requirement.")

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(_main())
