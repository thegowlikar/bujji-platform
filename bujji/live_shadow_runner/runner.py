"""Phase 20.13 -- the per-cycle orchestrator. Composes ONLY
already-existing, already-tested functions from every prior Cycle 1
layer (Phase 20.1, 20.5-20.10, 20.11, 20.12) -- no scoring, ranking,
qualification, allocation, conflict, decision, or observation logic is
reimplemented here. Never raises on a single bad cycle: a missing-data
or intelligence failure is recorded as an honest, degraded state, not
a crash (this phase's own "no crash, honest degradation" requirement).
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from bujji.capital_intelligence import assess_risk_allocation
from bujji.core.models import Candle
from bujji.decision_orchestration import compose_decision
from bujji.intelligence.context import EXECUTION_MODE_HISTORICAL_REPLAY, IntelligenceContext
from bujji.mic_runtime_context import (
    OptionMarketDataForCycle, assemble_market_understanding_from_option_data, build_mic_runtime_context,
)
from bujji.mic_v0.engine import compose_market_state
from bujji.opportunity_intelligence import MarketEnvironment, evaluate_opportunity
from bujji.opportunity_portfolio import rank_portfolio_choices
from bujji.opportunity_ranking import OpportunityCandidate
from bujji.shadow_decision_runtime import DecisionObservation, ShadowDecisionLog, run_shadow_cycle
from bujji.shadow_market_campaign import collect_observation
from bujji.strategy_intelligence import StrategyEvidence, score_strategy

from .models import ShadowRunConfig, ShadowRunState, SHADOW_RUN_CLOSED, SHADOW_RUN_OPEN, SHADOW_RUN_RUNNING

# MIC v0's own three-way session regime, mapped onto the strategy
# environment's intraday-style vocabulary -- the SAME disclosed
# approximation Phase 20.11/20.12 already carry (Phase 20.1C's own
# validated intraday classifier is not re-run here).
_MIC_REGIME_TO_STRATEGY_REGIME = {"TREND": "TREND_UP", "RANGE": "RANGE", "UNCLEAR": "TRANSITION"}

_MIN_CANDLES = 6   # RegimeBrain's own minimum -- mirrors mic_v0_validation.validation.MIN_CANDLES_PER_DAY.


def start_session(config: ShadowRunConfig) -> ShadowRunState:
    if not config.enabled:
        return ShadowRunState(
            session_date=config.session_date, state=SHADOW_RUN_CLOSED,
            cycles_completed=0, last_cycle_timestamp=None, errors=("disabled_by_config",),
        )
    return ShadowRunState(
        session_date=config.session_date, state=SHADOW_RUN_OPEN,
        cycles_completed=0, last_cycle_timestamp=None, errors=(),
    )


def process_cycle(
    state: ShadowRunState,
    timestamp: str,
    candles: Sequence[Candle],
    current_vix: float,
    trailing_vix: Sequence[float],
    strategies: Sequence[Tuple[StrategyEvidence, Tuple[str, ...], Tuple[str, ...]]],
    campaign_log: ShadowDecisionLog,
    execution_mode: str = EXECUTION_MODE_HISTORICAL_REPLAY,
    option_market_data: Optional[OptionMarketDataForCycle] = None,
) -> Tuple[ShadowRunState, Tuple[DecisionObservation, ...]]:
    """One full shadow cycle for every strategy in `strategies`:
    MIC v0 -> Strategy Intelligence -> Opportunity Intelligence ->
    Ranking -> Capital Intelligence -> Portfolio Intelligence ->
    Decision Orchestration -> Shadow Decision Runtime -> Campaign
    Collector. `candles`/`current_vix`/`trailing_vix` are the caller's
    own real data for this cycle -- fetched via `ShadowRunConfig.
    data_source`, never fabricated here. On any failure (insufficient
    real data, an exception in the chain), the cycle is recorded as a
    real error on the returned state and zero observations are
    produced -- never a crash, never a fabricated observation.

    `option_market_data` (Phase 20.25) -- OPTIONAL, defaults to `None`.
    When the caller has real option-chain/quote data for this cycle
    (spot/strike/t_years/premiums/quotes/OI), supplying it activates
    `VolatilityBrain`/`LiquidityBrain`/`StructureBrain`/`GreeksBrain`
    (real, unmodified) to build a richer `MarketUnderstandingContext`
    for explainability. `None` (today's actual live-runtime state,
    since `live_shadow_runner` does not yet fetch this data) preserves
    Phase 20.24's own exact behavior -- unchanged."""
    if state.state not in (SHADOW_RUN_OPEN, SHADOW_RUN_RUNNING):
        raise ValueError(f"cannot process a cycle while state={state.state!r}")

    errors = list(state.errors)
    produced: List[DecisionObservation] = []
    try:
        if len(candles) < _MIN_CANDLES:
            raise ValueError(f"insufficient real candles for this cycle ({len(candles)} < {_MIN_CANDLES})")

        context = IntelligenceContext(as_of_time=datetime.fromisoformat(timestamp), execution_mode=execution_mode)
        market_state = compose_market_state(list(candles), current_vix, list(trailing_vix), context)
        strategy_regime = _MIC_REGIME_TO_STRATEGY_REGIME.get(market_state.market_regime, "TRANSITION")
        environment = MarketEnvironment(
            mic_regime=strategy_regime, risk_state=market_state.risk_state,
            volatility_state=market_state.volatility_state, execution_profile_name="NORMAL",
            data_quality_ok=(market_state.data_quality == "SUFFICIENT"),
        )

        # Phase 20.25 -- built ONCE per cycle (not per-strategy): option-chain
        # data describes the market this cycle, not any one strategy.
        market_understanding = None
        if option_market_data is not None:
            market_understanding = assemble_market_understanding_from_option_data(
                candles, option_market_data, context=context,
            )

        for evidence, favorable, unfavorable in strategies:
            # Phase 20.24 -- activates score_strategy()'s own real, dormant
            # `context` parameter (Phase 20.5's `_apply_mic_context`), never
            # called with one anywhere in the live pipeline before this line.
            runtime_context = build_mic_runtime_context(
                strategy_regime, favorable, unfavorable, market_understanding=market_understanding,
            )
            score = score_strategy(evidence, context=runtime_context.mic_market_context)
            assessment = evaluate_opportunity(score, environment, favorable, unfavorable)
            allocation = assess_risk_allocation(OpportunityCandidate(assessment=assessment))
            portfolio = rank_portfolio_choices([allocation])
            final_decision = compose_decision(allocation, portfolio)
            observation = run_shadow_cycle(market_state, final_decision, market_state.as_of_time)
            collect_observation(campaign_log, observation)
            produced.append(observation)

        new_state = ShadowRunState(
            session_date=state.session_date, state=SHADOW_RUN_RUNNING,
            cycles_completed=state.cycles_completed + 1, last_cycle_timestamp=timestamp, errors=tuple(errors),
        )
    except Exception as exc:  # noqa: BLE001 -- one bad cycle must never crash the session.
        errors.append(f"cycle_error@{timestamp}: {type(exc).__name__}: {exc}")
        new_state = ShadowRunState(
            session_date=state.session_date, state=SHADOW_RUN_RUNNING,
            cycles_completed=state.cycles_completed, last_cycle_timestamp=state.last_cycle_timestamp,
            errors=tuple(errors),
        )

    return new_state, tuple(produced)


def close_session(state: ShadowRunState) -> ShadowRunState:
    final = SHADOW_RUN_CLOSED
    return ShadowRunState(
        session_date=state.session_date, state=final,
        cycles_completed=state.cycles_completed, last_cycle_timestamp=state.last_cycle_timestamp,
        errors=state.errors,
    )
