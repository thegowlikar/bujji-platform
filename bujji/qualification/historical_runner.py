"""Historical Qualification Runner — BUJJI Options OS v3, Engineering
Series 58.

Replays a corpus of `ReplayScenario`s (Series 46,
`bujji.qualification.replay_models`) through the *entire* integrated
runtime -- Production Composition Root (Series 54), Health Aggregator
(Series 55), Circuit Breaker (Series 56), Rate Limiter (Series 57),
and the Shadow Runtime (Series 54's `run_shadow()`) -- recording one
`QualificationRecord` per session. It never dispatches a live order,
never authenticates against a live broker, never modifies a replay
input, and never alters runtime state beyond what `run_shadow()`
itself already does (dispatch into `PaperBroker`, exactly as Series 54
documents).

This module builds nothing new on top of the pipeline itself -- every
stage call it triggers is a call into an already-frozen prior series,
reached exclusively through `guarded_run_shadow()` (Series 57), which
itself only ever reaches Series 54's `run_shadow()` when the Circuit
Breaker and Rate Limiter both agree admission is safe.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, List, Optional, Sequence

from ..production_runtime.circuit_breaker import CIRCUIT_CLOSED, RuntimeCircuitBreaker
from ..production_runtime.composition_root import CompositionRoot
from ..production_runtime.health import RuntimeHealthAggregator
from ..production_runtime.rate_limiter import RateLimiterConfig, RuntimeRateLimiter
from ..production_runtime.rate_limiter import guarded_run_shadow as rate_guarded_run_shadow
from ..production_runtime.runtime import PipelineInput
from ..production_runtime.startup import StartupReport
from .recorder import (
    RUNTIME_OUTCOME_COMPLETED,
    RUNTIME_OUTCOME_FAILED,
    RUNTIME_OUTCOME_REJECTED_CIRCUIT,
    RUNTIME_OUTCOME_REJECTED_RATE_LIMIT,
    QualificationRecord,
    QualificationRecorder,
    RuntimeOutcome,
)
from .replay_models import ReplayScenario

_FAILED_EXECUTION_STATES = ("FAILED_VALIDATION", "ABORTED")

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


def _pipeline_input_from_scenario(scenario: ReplayScenario) -> PipelineInput:
    return PipelineInput(
        market_context=scenario.market_context,
        market_opinion=scenario.market_opinion,
        context_stability=scenario.context_stability,
        calibration=scenario.calibration,
        governance=scenario.governance,
        lifecycle=scenario.lifecycle,
        contract=scenario.contract,
    )


def _classify_outcome(circuit_decision, rate_decision, shadow_result) -> RuntimeOutcome:
    if circuit_decision.state != CIRCUIT_CLOSED:
        return RuntimeOutcome(
            status=RUNTIME_OUTCOME_REJECTED_CIRCUIT,
            reason=circuit_decision.reason,
            shadow_result=None,
        )
    if not rate_decision.permitted:
        return RuntimeOutcome(
            status=RUNTIME_OUTCOME_REJECTED_RATE_LIMIT,
            reason=rate_decision.reason,
            shadow_result=None,
        )
    if shadow_result is None:
        # Defensive only -- unreachable given the two checks above,
        # since `rate_guarded_run_shadow` only returns None when
        # either gate above already refused. Never fabricated as
        # COMPLETED.
        return RuntimeOutcome(
            status=RUNTIME_OUTCOME_FAILED,
            reason="Admission was permitted but no ShadowResult was produced.",
            shadow_result=None,
        )
    if shadow_result.execution_session.execution_state in _FAILED_EXECUTION_STATES:
        return RuntimeOutcome(
            status=RUNTIME_OUTCOME_FAILED,
            reason=f"execution_state={shadow_result.execution_session.execution_state}",
            shadow_result=shadow_result,
        )
    return RuntimeOutcome(
        status=RUNTIME_OUTCOME_COMPLETED,
        reason=shadow_result.trace,
        shadow_result=shadow_result,
    )


@dataclass(frozen=True)
class HistoricalQualificationRunner:
    """Owns no runtime state itself. `root` and `startup_report` are
    constructed once, by the caller, via Series 54's `startup()` --
    this runner never calls `build_composition_root()` or `startup()`
    on its own, so it can never silently reconstruct a different
    runtime mid-corpus.
    """

    root: CompositionRoot
    startup_report: StartupReport
    health_aggregator: RuntimeHealthAggregator
    circuit_breaker: RuntimeCircuitBreaker
    rate_limiter: RuntimeRateLimiter
    logger: logging.Logger

    def run_corpus(
        self,
        scenarios: Sequence[ReplayScenario],
        timestamps: Sequence[datetime],
        recorder: Optional[QualificationRecorder] = None,
        published_states: Optional[Sequence[object]] = None,
    ) -> QualificationRecorder:
        """Replay every scenario, in order, recording one
        `QualificationRecord` each. `timestamps[i]` is the sole,
        injected, deterministic clock reading used for session `i`'s
        health snapshot, circuit decision, rate-limit decision, and
        Shadow Runtime run -- the system clock is never read.

        `published_states`, if supplied, is Engineering Series 70 Phase
        2 (Context Stability Observatory, recording-only): the full
        `bujji.mic_replay.publication_replay.PublishedState` each
        `scenarios[i]` was originally flattened from, attached verbatim
        onto `QualificationRecord.published_state` -- never derived,
        never recomputed. Optional and positionally zipped with
        `scenarios`; when omitted (the default, and every call site
        before this sprint), every record's `published_state` is
        `None`, exactly as before.

        Never mutates `scenarios` or any element of it. Never raises
        on an individual scenario's rejection or failure -- those are
        recorded outcomes, not runner errors.
        """
        if len(scenarios) != len(timestamps):
            raise ValueError(
                f"scenarios ({len(scenarios)}) and timestamps ({len(timestamps)}) must be the same length"
            )
        if published_states is not None and len(published_states) != len(scenarios):
            raise ValueError(
                f"published_states ({len(published_states)}) must be the same length as scenarios ({len(scenarios)})"
            )

        recorder = recorder if recorder is not None else QualificationRecorder()
        previous_admission_timestamp: Optional[datetime] = None

        for index, (scenario, ts) in enumerate(zip(scenarios, timestamps)):
            published_state = published_states[index] if published_states is not None else None
            clock_for_session: Clock = lambda _ts=ts: _ts

            pipeline_input = _pipeline_input_from_scenario(scenario)

            health_snapshot = _snapshot_with_clock(
                self.health_aggregator, self.root, self.startup_report, clock_for_session
            )

            circuit_decision = _evaluate_with_clock(
                self.circuit_breaker,
                health_snapshot,
                clock_for_session,
                runtime_mode=self.root.config.mode,
                qualification_mode=self.root.config.replay_status,
            )

            rate_decision, shadow_result = rate_guarded_run_shadow(
                circuit_decision,
                _limiter_with_clock(self.rate_limiter, clock_for_session),
                previous_admission_timestamp,
                self.root,
                pipeline_input,
                scenario.spot_snapshot,
                scenario.option_chain,
                clock=clock_for_session,
            )

            if rate_decision.permitted:
                previous_admission_timestamp = ts

            outcome = _classify_outcome(circuit_decision, rate_decision, shadow_result)

            record = QualificationRecord(
                replay_identifier=scenario.scenario_id,
                timestamp=ts.isoformat(),
                strategy_decision=getattr(shadow_result, "strategy_decision", None),
                risk_decision=getattr(shadow_result, "risk_assessment", None),
                capital_decision=getattr(shadow_result, "capital_decision", None),
                execution_plan=getattr(shadow_result, "execution_plan", None),
                health_snapshot=health_snapshot,
                circuit_decision=circuit_decision,
                rate_limit_decision=rate_decision,
                runtime_outcome=outcome,
                qualification_fingerprint=self.root.config.qualification_fingerprint,
                # Series 69 Phase 2: recorded verbatim, exactly
                # mirroring the `strategy_decision` extraction two
                # lines above -- `shadow_result.market_state_assessment`
                # already exists (production_runtime/runtime.py
                # ShadowResult), it was simply never copied onto
                # QualificationRecord before. `scenario` is the same
                # already-in-scope `ReplayScenario` this session was
                # built from -- no new computation, no lookahead.
                market_state_assessment=getattr(shadow_result, "market_state_assessment", None),
                scenario=scenario,
                # Series 69 Phase 2b: same pattern, one more field --
                # shadow_result.evidence_interpretation already exists
                # (production_runtime/runtime.py ShadowResult), simply
                # never copied onto QualificationRecord before.
                evidence_interpretation=getattr(shadow_result, "evidence_interpretation", None),
                # Series 70 Phase 2: see run_corpus()'s own docstring above.
                published_state=published_state,
            )
            recorder.record(record)

            self.logger.info(
                "QualificationRecord replay_identifier=%s outcome=%s health=%s circuit=%s rate=%s",
                record.replay_identifier,
                outcome.status,
                health_snapshot.overall,
                circuit_decision.state,
                rate_decision.state,
            )

        return recorder


def _snapshot_with_clock(aggregator: RuntimeHealthAggregator, root, startup_report, clock: Clock):
    from dataclasses import replace

    return replace(aggregator, clock=clock).snapshot_from_composition_root(root, startup_report=startup_report)


def _evaluate_with_clock(
    breaker: RuntimeCircuitBreaker,
    health_snapshot,
    clock: Clock,
    runtime_mode: Optional[str] = None,
    qualification_mode: Optional[str] = None,
):
    from dataclasses import replace

    return replace(breaker, clock=clock).evaluate(
        health_snapshot, runtime_mode=runtime_mode, qualification_mode=qualification_mode
    )


def _limiter_with_clock(limiter: RuntimeRateLimiter, clock: Clock) -> RuntimeRateLimiter:
    from dataclasses import replace

    return replace(limiter, clock=clock)
