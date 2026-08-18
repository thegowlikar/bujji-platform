"""Live Intelligence Data Bridge -- Shadow Runtime, Phase 19.13.

Task 1, verbatim flow:

    MarketDataAdapter -> MarketRealitySnapshot -> MarketIntelligenceSnapshot
    -> DecisionIntelligenceSnapshot -> MarketPhenomenaAssessment
    -> MarketStateGraph -> CycleArtifact

Every step already exists and is REUSED, not reimplemented:
`MarketDataAdapter.build_snapshot()`/`fetch_spot_candles()`
(`bujji.market_perception`, unmodified) -> `translate_market_snapshot_to_reality_snapshot()`
(`reality_translator.py`, Phase 19.10.2) -> `build_intelligence_heartbeat_cycle()`
(`intelligence_pipeline_adapter.py`, Phase 19.10.1, itself already
composing MarketIntelligenceSnapshot/DecisionContext/DecisionIntelligenceSnapshot/
MarketPhenomenaAssessment/MarketStateNode/MarketEnvironmentAssessment) ->
`build_daily_intelligence_artifact()` (`daily_intelligence_artifact.py`,
Phase 19.13, this phase's own new full-payload storage).

This module's own, genuinely new contribution is: (a) the completeness
gate (task 5) sitting BEFORE composition, and (b) bundling the whole
flow into one callable a daily runtime's `intelligence_fn` can invoke
without re-deriving any of the above wiring itself -- `ShadowSessionRunner`
already has an equivalent step internally
(`_run_intelligence_pipeline_step`), but it is entangled with that
runner's own watchlist/quote-observation machinery, which
`DailySessionRuntime` (Phase 19.11) has no use for. This module is the
same real composition, without that unrelated machinery.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bujji.intelligence.context import IntelligenceContext
from bujji.market_perception.intelligence_adapter import fetch_spot_candles
from bujji.market_perception.market_data_adapter import MarketDataAdapter
from bujji.market_perception.models import OptionChainConfig

from .completeness_gate import evaluate_completeness_gate
from .cycle_artifact import build_cycle_artifact, record_cycle_artifact, record_cycle_failure
from .daily_intelligence_artifact import build_daily_intelligence_artifact, record_daily_intelligence_artifact
from .intelligence_pipeline_adapter import (
    IntelligenceHeartbeatCycle,
    IntelligencePipelineAdapterError,
    build_intelligence_heartbeat_cycle,
)
from .reality_translator import RealityTranslationError, translate_market_snapshot_to_reality_snapshot
from .replay_equivalence import LiveReplayEquivalenceReport, validate_live_replay_equivalence


@dataclass(frozen=True)
class LiveIntelligenceCycleResult:
    """The real, honest outcome of one live cycle -- never raised as an
    exception (same "never raises" discipline every Phase 19.x runtime
    component already follows). `succeeded=False` with a `gate_reasons`
    non-empty tuple means the completeness gate refused composition;
    `succeeded=False` with `error` set means an unexpected failure
    downstream; `succeeded=True` means a real cycle was composed and
    (if a store was supplied) persisted.

    Phase 19.14.1, additive: `replay_equivalence`/`replay_check_error`
    are only ever populated when the caller opts in via
    `include_replay_equivalence=True`. `replay_equivalence` (when set)
    is the real, unmodified `LiveReplayEquivalenceReport` from Phase
    19.13's own canonical `replay_equivalence.validate_live_replay_equivalence()`
    -- never a second, re-derived comparison. `replay_check_error` is set
    only if that comparison itself could not run (distinct from
    `replay_equivalence.equivalent is False`, a real mismatch finding,
    not a check failure)."""

    succeeded: bool
    cycle_id: str
    as_of_time: str
    gate_passed: bool
    gate_reasons: tuple
    intelligence_fingerprint: Optional[str]
    error: Optional[str]
    replay_equivalence: Optional[LiveReplayEquivalenceReport] = None
    replay_check_error: Optional[str] = None


async def run_live_intelligence_cycle(
    *, broker, clock, underlying: str, session_id: str, cycle_id: str, session_date: str,
    execution_mode: str, chain_config: Optional[OptionChainConfig] = None,
    previous_market_intelligence_snapshot=None, previous_phenomena=None, previous_state_node=None,
    cycle_artifact_store=None, daily_artifact_store=None,
    runtime_health_status: str = "RUNNING",
    include_replay_equivalence: bool = False,
) -> LiveIntelligenceCycleResult:
    """`broker`: any object implementing `bujji.market_perception`'s own
    broker protocol (`get_spot`/`get_vix`/`get_option_chain`/
    `get_recent_candles`, all read-only) -- never a broker method
    beyond what `MarketDataAdapter` already calls, and NEVER
    `place_order`/`modify_order`/`cancel_order`/position/margin/funds
    (this module contains no reference to any of those, structurally).
    `cycle_artifact_store`/`daily_artifact_store`: optional `EventStore`
    instances (Phase 15B) -- when omitted, the cycle still composes and
    the result is still returned, just not persisted (matches
    `ShadowSessionRunner`'s own established convention of optional
    persistence paths).

    `include_replay_equivalence` (Phase 19.14.1, default False -- every
    pre-existing caller/test keeps behaving byte-for-byte identically):
    when True, immediately after a successful LIVE composition, this
    function ALSO re-runs `build_intelligence_heartbeat_cycle()` in
    `HISTORICAL_REPLAY` mode against the SAME `reality_snapshot`/
    `spot_candles`/`as_of_time` (never a second fetch, never
    reconstructed data) and compares the two via Phase 19.13's own
    canonical `validate_live_replay_equivalence()` -- reused unmodified,
    never a second equivalence mechanism."""
    as_of_time = clock()
    adapter = MarketDataAdapter(broker, clock, underlying=underlying, chain_config=chain_config or OptionChainConfig())

    try:
        market_snapshot = await adapter.build_snapshot()
        spot_candles = await fetch_spot_candles(broker, underlying)
        reality_snapshot = translate_market_snapshot_to_reality_snapshot(market_snapshot)
    except RealityTranslationError as exc:
        return _failure_result(cycle_id, as_of_time, error=f"reality_translation_failed: {exc}")
    except Exception as exc:  # noqa: BLE001 -- broker/adapter IO, must never raise into the caller.
        return _failure_result(cycle_id, as_of_time, error=f"market_data_fetch_failed: {type(exc).__name__}: {exc}")

    gate = evaluate_completeness_gate(reality_snapshot)
    if not gate.passed:
        if cycle_artifact_store is not None:
            record_cycle_failure(
                cycle_artifact_store, session_id=session_id, cycle_id=cycle_id, execution_mode=execution_mode,
                as_of_time=as_of_time.isoformat(), error=f"completeness_gate_failed: {gate.reasons}", recorded_at=as_of_time,
            )
        return LiveIntelligenceCycleResult(
            succeeded=False, cycle_id=cycle_id, as_of_time=as_of_time.isoformat(),
            gate_passed=False, gate_reasons=gate.reasons, intelligence_fingerprint=None, error=None,
        )

    context = IntelligenceContext(
        as_of_time=as_of_time, execution_mode=execution_mode,
        reality_snapshot_reference=reality_snapshot.fingerprint(),
    )
    try:
        cycle: IntelligenceHeartbeatCycle = build_intelligence_heartbeat_cycle(
            reality_snapshot=reality_snapshot, spot_candles=spot_candles, context=context,
            previous_market_intelligence_snapshot=previous_market_intelligence_snapshot,
            previous_phenomena=previous_phenomena, previous_state_node=previous_state_node,
        )
    except IntelligencePipelineAdapterError as exc:
        if cycle_artifact_store is not None:
            record_cycle_failure(
                cycle_artifact_store, session_id=session_id, cycle_id=cycle_id, execution_mode=execution_mode,
                as_of_time=as_of_time.isoformat(), error=f"{type(exc).__name__}: {exc}", recorded_at=as_of_time,
            )
        return _failure_result(cycle_id, as_of_time, error=f"intelligence_pipeline_failed: {exc}")

    replay_equivalence: Optional[LiveReplayEquivalenceReport] = None
    replay_check_error: Optional[str] = None
    if include_replay_equivalence:
        try:
            replay_equivalence = validate_live_replay_equivalence(
                reality_snapshot=reality_snapshot, spot_candles=spot_candles, as_of_time=as_of_time,
                previous_market_intelligence_snapshot=previous_market_intelligence_snapshot,
                previous_phenomena=previous_phenomena, previous_state_node=previous_state_node,
            )
        except Exception as exc:  # noqa: BLE001 -- an additive check; must never break the already-succeeded LIVE cycle.
            replay_check_error = f"{type(exc).__name__}: {exc}"

    if cycle_artifact_store is not None:
        artifact = build_cycle_artifact(
            cycle=cycle, cycle_id=cycle_id, session_id=session_id,
            execution_mode=execution_mode, runtime_health_status=runtime_health_status,
        )
        record_cycle_artifact(cycle_artifact_store, artifact, recorded_at=as_of_time)

    if daily_artifact_store is not None:
        daily_artifact = build_daily_intelligence_artifact(
            cycle=cycle, reality_snapshot=reality_snapshot, cycle_id=cycle_id, session_id=session_id,
            session_date=session_date, execution_mode=execution_mode, runtime_health_status=runtime_health_status,
            completeness_gate_passed=True,
            replay_equivalence=replay_equivalence.to_dict() if replay_equivalence is not None else None,
        )
        record_daily_intelligence_artifact(daily_artifact_store, daily_artifact, recorded_at=as_of_time)

    return LiveIntelligenceCycleResult(
        succeeded=True, cycle_id=cycle_id, as_of_time=as_of_time.isoformat(),
        gate_passed=True, gate_reasons=(), intelligence_fingerprint=cycle.market_intelligence_snapshot.intelligence_snapshot_id,
        error=None, replay_equivalence=replay_equivalence, replay_check_error=replay_check_error,
    )


def _failure_result(cycle_id: str, as_of_time: datetime, *, error: str) -> LiveIntelligenceCycleResult:
    return LiveIntelligenceCycleResult(
        succeeded=False, cycle_id=cycle_id, as_of_time=as_of_time.isoformat(),
        gate_passed=False, gate_reasons=(), intelligence_fingerprint=None, error=error,
    )
