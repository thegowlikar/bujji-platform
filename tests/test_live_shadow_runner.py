"""Phase 20.13 -- Live Shadow Campaign Runner & Operational Session
Controller tests."""
from __future__ import annotations

import os
from datetime import datetime

import pytest

from bujji.core.models import Candle
from bujji.strategy_intelligence import StrategyEvidence
from bujji.shadow_decision_runtime import ShadowDecisionLog
from bujji.live_shadow_runner import (
    HealthReport, ShadowRunConfig, ShadowRunState,
    SHADOW_RUN_CLOSED, SHADOW_RUN_OPEN, SHADOW_RUN_RUNNING,
    close_session, evaluate_runtime_health, load_campaign_artifacts,
    load_decision_observations, process_cycle, save_campaign_artifact, start_session,
)

TREND_FAVORABLE, TREND_UNFAVORABLE = ("TREND_UP", "TREND_DOWN"), ("RANGE",)
MR_FAVORABLE, MR_UNFAVORABLE = ("RANGE",), ("TREND_UP", "TREND_DOWN")

TREND_EVIDENCE = StrategyEvidence(
    strategy_name="FAMILY_A_TREND_FOLLOWING", sample_size=11278, win_rate=0.665, profit_factor=2.30,
    net_expectancy=517.0, gross_expectancy=949.0,
    train_expectancy=512.0, validation_expectancy=481.0, out_of_sample_expectancy=573.0,
)
MR_EVIDENCE = StrategyEvidence(
    strategy_name="FAMILY_B_MEAN_REVERSION", sample_size=150, win_rate=0.0, profit_factor=0.0,
    net_expectancy=-1664.0, gross_expectancy=-1218.0,
    train_expectancy=-1375.0, validation_expectancy=-1806.0, out_of_sample_expectancy=-2260.0,
)
STRATEGIES = ((TREND_EVIDENCE, TREND_FAVORABLE, TREND_UNFAVORABLE), (MR_EVIDENCE, MR_FAVORABLE, MR_UNFAVORABLE))


def _config(enabled=True):
    return ShadowRunConfig(
        market="NIFTY", session_date="2026-08-17", cycle_interval_minutes=5,
        data_source="test-fixture", enabled=enabled,
    )


def _candles(n=10, start_price=100.0, ts_prefix="2026-08-17T09:", start_minute=15):
    out = []
    price = start_price
    for i in range(n):
        minute = start_minute + i * 5
        hour, minute = 9 + minute // 60, minute % 60
        ts = f"2026-08-17T{hour:02d}:{minute:02d}:00+05:30"
        price += 0.5
        out.append(Candle(timestamp=datetime.fromisoformat(ts),
                           open=price, high=price + 1, low=price - 1, close=price, volume=1000.0))
    return out


def _vix():
    return 15.0, [14.0] * 30


# --------------------------------------------------------------------- #
# 1. Session lifecycle
# --------------------------------------------------------------------- #
def test_session_lifecycle_open_running_closed():
    config = _config()
    state = start_session(config)
    assert state.state == SHADOW_RUN_OPEN

    log = ShadowDecisionLog()
    candles = _candles(10)
    current_vix, trailing_vix = _vix()
    state, observations = process_cycle(state, candles[-1].timestamp.isoformat(), candles, current_vix,
                                         trailing_vix, STRATEGIES, log)
    assert state.state == SHADOW_RUN_RUNNING
    assert len(observations) == 2

    state = close_session(state)
    assert state.state == SHADOW_RUN_CLOSED
    assert state.cycles_completed == 1


# --------------------------------------------------------------------- #
# 2. Multiple cycles
# --------------------------------------------------------------------- #
def test_multiple_cycles_recorded():
    state = start_session(_config())
    log = ShadowDecisionLog()
    current_vix, trailing_vix = _vix()
    for i in range(3):
        candles = _candles(10 + i)
        state, obs = process_cycle(state, candles[-1].timestamp.isoformat(), candles, current_vix,
                                    trailing_vix, STRATEGIES, log)
    assert state.cycles_completed == 3
    assert len(log.observations) == 6  # 2 strategies x 3 cycles


# --------------------------------------------------------------------- #
# 3. Persistence
# --------------------------------------------------------------------- #
def test_artifacts_survive_restart(tmp_path):
    path = tmp_path / "campaign.jsonl"
    state = start_session(_config())
    log = ShadowDecisionLog()
    current_vix, trailing_vix = _vix()
    candles = _candles(10)
    state, observations = process_cycle(state, candles[-1].timestamp.isoformat(), candles, current_vix,
                                         trailing_vix, STRATEGIES, log)
    for obs in observations:
        save_campaign_artifact(path, "DecisionObservation", obs)

    reloaded = load_decision_observations(path)
    assert len(reloaded) == len(observations)
    assert {o.observation_id for o in reloaded} == {o.observation_id for o in observations}
    assert {o.decision_state for o in reloaded} == {o.decision_state for o in observations}


# --------------------------------------------------------------------- #
# 4. Health calculation
# --------------------------------------------------------------------- #
def test_health_calculation_states():
    state = start_session(_config())
    log = ShadowDecisionLog()
    current_vix, trailing_vix = _vix()
    candles = _candles(10)
    as_of = candles[-1].timestamp.isoformat()
    state, observations = process_cycle(state, as_of, candles, current_vix, trailing_vix, STRATEGIES, log)

    healthy_report = evaluate_runtime_health(state, log.observations, as_of)
    assert healthy_report.runtime_status == "HEALTHY"

    # Evaluated long after the last real observation -> feed goes stale -> DEGRADED.
    stale_report = evaluate_runtime_health(state, log.observations, "2026-08-17T14:00:00+05:30")
    assert stale_report.runtime_status == "DEGRADED"

    empty_state = start_session(_config())
    empty_report = evaluate_runtime_health(empty_state, (), as_of)
    assert empty_report.runtime_status == "DEGRADED"


# --------------------------------------------------------------------- #
# 5. Missing data handling
# --------------------------------------------------------------------- #
def test_missing_data_does_not_crash():
    state = start_session(_config())
    log = ShadowDecisionLog()
    current_vix, trailing_vix = _vix()
    too_few_candles = _candles(3)  # below MIC v0's own minimum of 6.
    new_state, observations = process_cycle(state, too_few_candles[-1].timestamp.isoformat(), too_few_candles,
                                              current_vix, trailing_vix, STRATEGIES, log)
    assert observations == ()
    assert new_state.cycles_completed == state.cycles_completed  # no fabricated cycle counted.
    assert any("insufficient real candles" in e for e in new_state.errors)
    assert new_state.state == SHADOW_RUN_RUNNING  # degraded, not crashed.


# --------------------------------------------------------------------- #
# 6. No execution leakage
# --------------------------------------------------------------------- #
def test_no_trading_capability_in_package():
    import bujji.live_shadow_runner as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("place_order", "modify_order", "cancel_order", "PaperBroker", "FyersBroker",
                 "entry_price", "exit_price", "order_id", "quantity", "position_size")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden term {term!r}"


def test_no_broker_module_imports():
    import bujji.live_shadow_runner as pkg
    pkg_dir = os.path.dirname(pkg.__file__)
    forbidden = ("import bujji.broker", "from bujji.broker", "import bujji.capital.", "from bujji.capital.")
    for fname in os.listdir(pkg_dir):
        if not fname.endswith(".py"):
            continue
        with open(os.path.join(pkg_dir, fname)) as f:
            source = f.read()
        for term in forbidden:
            assert term not in source, f"{fname} contains forbidden import {term!r}"


# --------------------------------------------------------------------- #
# 7. Restart recovery
# --------------------------------------------------------------------- #
def test_restart_recovery_resumes_without_losing_artifacts(tmp_path):
    path = tmp_path / "campaign.jsonl"
    state = start_session(_config())
    log = ShadowDecisionLog()
    current_vix, trailing_vix = _vix()

    # "Session 1" -- runs 2 cycles, persists each observation, then "crashes".
    for i in range(2):
        candles = _candles(10 + i)
        state, observations = process_cycle(state, candles[-1].timestamp.isoformat(), candles, current_vix,
                                             trailing_vix, STRATEGIES, log)
        for obs in observations:
            save_campaign_artifact(path, "DecisionObservation", obs)
    cycles_before_restart = state.cycles_completed
    observations_before_restart = len(load_decision_observations(path))

    # "Restart" -- fresh process state, but reload what was already persisted.
    recovered_observations = load_decision_observations(path)
    assert len(recovered_observations) == observations_before_restart

    resumed_log = ShadowDecisionLog()
    for obs in recovered_observations:
        resumed_log.record(obs)
    resumed_state = ShadowRunState(
        session_date=state.session_date, state=SHADOW_RUN_RUNNING,
        cycles_completed=cycles_before_restart, last_cycle_timestamp=state.last_cycle_timestamp, errors=(),
    )

    # Continue the same session post-restart -- no artifact from before the restart was lost.
    candles = _candles(12)
    resumed_state, new_observations = process_cycle(resumed_state, candles[-1].timestamp.isoformat(), candles,
                                                      current_vix, trailing_vix, STRATEGIES, resumed_log)
    for obs in new_observations:
        save_campaign_artifact(path, "DecisionObservation", obs)

    assert resumed_state.cycles_completed == cycles_before_restart + 1
    assert len(resumed_log.observations) == observations_before_restart + len(new_observations)
    assert len(load_decision_observations(path)) == observations_before_restart + len(new_observations)
