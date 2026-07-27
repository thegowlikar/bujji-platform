"""Decision Pipeline Qualification engine — BUJJI Options OS v3,
Engineering Series 38, Sprint 1.

This module runs the complete, frozen Trading Brain end-to-end
(Evidence Interpreter -> Market State Builder -> Strategy Selector ->
Risk Brain -> Capital Brain -> Execution Planner) and validates it.
It adds no new decision logic of its own: every stage call here is a
direct, unmodified call into that stage's own frozen `engine.py`. This
module never connects to a broker, a live feed, an execution engine,
or an order manager -- it only replays already-defined,
already-tested pipeline stages and checks their combined behavior.

Determinism note: every stage function used here accepts an injectable
`clock`, defaulting to the real wall clock, exactly like every other
Trading Brain module. For a qualification run's own "identical
replay" guarantee to hold, the SAME fixed clock must be supplied
across every replay of the same `PipelineInput` -- this module's own
`clock` parameter defaults to the real clock (matching convention),
but callers validating replay stability must supply a fixed one, as
this package's own tests do.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..capital_brain import engine as capital_brain_engine
from ..evidence_interpreter import engine as evidence_interpreter_engine
from ..evidence_interpreter.models import EvidenceInterpretation
from ..execution_planner import engine as execution_planner_engine
from ..execution_planner.models import ExecutionPlan
from ..market_state import engine as market_state_engine
from ..market_state.models import MarketStateAssessment
from ..risk_brain import engine as risk_brain_engine
from ..risk_brain.models import RiskAssessment
from ..strategy_selector import engine as strategy_selector_engine
from ..strategy_selector.models import StrategyDecision
from . import models

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


CONFIDENCE_ORDER = ("UNKNOWN", "VERY_LOW", "LOW", "MODERATE", "HIGH", "VERY_HIGH")


class StageFailure(Exception):
    """Raised internally when a stage rejects the input. Caught by
    run_qualification() -- never propagated to a caller."""

    def __init__(self, stage: str, error: Exception) -> None:
        self.stage = stage
        self.error = error
        super().__init__(f"{stage} failed: {error}")


def _run_stages(
    pipeline_input: models.PipelineInput, clock: Clock
) -> Tuple[Dict[str, object], Tuple[str, ...], Optional[str]]:
    """Run all six frozen stages in order, stopping at the first
    exception. Returns (results_by_stage, completed_stage_names,
    failed_stage_or_None). Never mutates `pipeline_input`.
    """
    results: Dict[str, object] = {}
    completed: List[str] = []

    try:
        ei = evidence_interpreter_engine.interpret(
            market_context=pipeline_input.market_context,
            market_opinion=pipeline_input.market_opinion,
            context_stability=pipeline_input.context_stability,
            calibration=pipeline_input.calibration,
            governance=pipeline_input.governance,
            lifecycle=pipeline_input.lifecycle,
            contract=pipeline_input.contract,
            source=pipeline_input.source,
            reasoning_summary=pipeline_input.reasoning_summary,
            context_id=pipeline_input.context_id,
            clock=clock,
        )
    except Exception as exc:  # noqa: BLE001 - deliberately broad; reported, not swallowed
        raise StageFailure(models.STAGE_EVIDENCE_INTERPRETER, exc) from exc
    results[models.STAGE_EVIDENCE_INTERPRETER] = ei
    completed.append(models.STAGE_EVIDENCE_INTERPRETER)

    try:
        msb = market_state_engine.assess(ei, clock=clock)
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(models.STAGE_MARKET_STATE_BUILDER, exc) from exc
    results[models.STAGE_MARKET_STATE_BUILDER] = msb
    completed.append(models.STAGE_MARKET_STATE_BUILDER)

    try:
        ss = strategy_selector_engine.select(msb, clock=clock)
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(models.STAGE_STRATEGY_SELECTOR, exc) from exc
    results[models.STAGE_STRATEGY_SELECTOR] = ss
    completed.append(models.STAGE_STRATEGY_SELECTOR)

    try:
        rb = risk_brain_engine.assess(ss, msb, clock=clock)
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(models.STAGE_RISK_BRAIN, exc) from exc
    results[models.STAGE_RISK_BRAIN] = rb
    completed.append(models.STAGE_RISK_BRAIN)

    try:
        cb = capital_brain_engine.authorize(rb, clock=clock)
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(models.STAGE_CAPITAL_BRAIN, exc) from exc
    results[models.STAGE_CAPITAL_BRAIN] = cb
    completed.append(models.STAGE_CAPITAL_BRAIN)

    try:
        ep = execution_planner_engine.plan(cb, ss, clock=clock)
    except Exception as exc:  # noqa: BLE001
        raise StageFailure(models.STAGE_EXECUTION_PLANNER, exc) from exc
    results[models.STAGE_EXECUTION_PLANNER] = ep
    completed.append(models.STAGE_EXECUTION_PLANNER)

    return results, tuple(completed), None


def _stage_id(results: Dict[str, object], stage: str) -> Optional[str]:
    obj = results.get(stage)
    if obj is None:
        return None
    id_attr = {
        models.STAGE_EVIDENCE_INTERPRETER: "interpretation_id",
        models.STAGE_MARKET_STATE_BUILDER: "assessment_id",
        models.STAGE_STRATEGY_SELECTOR: "decision_id",
        models.STAGE_RISK_BRAIN: "assessment_id",
        models.STAGE_CAPITAL_BRAIN: "decision_id",
        models.STAGE_EXECUTION_PLANNER: "plan_id",
    }[stage]
    return getattr(obj, id_attr)


def compute_fingerprint(results: Dict[str, object], completed_stages: Tuple[str, ...]) -> str:
    """One deterministic fingerprint over the entire completed chain.

    Changing any upstream decision changes at least one downstream id
    (every id in this pipeline is itself content-derived via
    hashlib.md5 over its own stage's inputs -- never uuid4, never
    wall-clock alone), so this fingerprint changes too.
    """
    if not completed_stages:
        return "FP-" + hashlib.md5(b"EMPTY").hexdigest()[:16]
    ids = [_stage_id(results, stage) or "NONE" for stage in completed_stages]
    return "FP-" + hashlib.md5("|".join(ids).encode()).hexdigest()[:16]


def _check_provenance(results: Dict[str, object]) -> List[str]:
    """Verify every stage's own *_id back-reference actually points at
    the upstream object that produced it. Only checks pairs where both
    sides are present.
    """
    warnings: List[str] = []

    ei = results.get(models.STAGE_EVIDENCE_INTERPRETER)
    msb = results.get(models.STAGE_MARKET_STATE_BUILDER)
    ss = results.get(models.STAGE_STRATEGY_SELECTOR)
    rb = results.get(models.STAGE_RISK_BRAIN)
    cb = results.get(models.STAGE_CAPITAL_BRAIN)
    ep = results.get(models.STAGE_EXECUTION_PLANNER)

    if ei and msb and msb.interpretation_id != ei.interpretation_id:
        warnings.append("MISSING_PROVENANCE:market_state_builder->evidence_interpreter")
    if msb and ss and ss.market_state_assessment_id != msb.assessment_id:
        warnings.append("MISSING_PROVENANCE:strategy_selector->market_state_builder")
    if ss and rb and rb.strategy_decision_id != ss.decision_id:
        warnings.append("MISSING_PROVENANCE:risk_brain->strategy_selector")
    if msb and rb and rb.market_state_assessment_id != msb.assessment_id:
        warnings.append("MISSING_PROVENANCE:risk_brain->market_state_builder")
    if rb and cb and cb.risk_assessment_id != rb.assessment_id:
        warnings.append("MISSING_PROVENANCE:capital_brain->risk_brain")
    if cb and ep and ep.capital_decision_id != cb.decision_id:
        warnings.append("MISSING_PROVENANCE:execution_planner->capital_brain")
    if ss and ep and ep.strategy_decision_id != ss.decision_id:
        warnings.append("MISSING_PROVENANCE:execution_planner->strategy_selector")

    return warnings


def _check_confidence_monotonic(results: Dict[str, object]) -> List[str]:
    """Verify confidence never increases along each real dependency
    edge (not a flat six-stage line -- Strategy Selector and Risk
    Brain both derive independently from Market State Builder, so
    each is checked against its own true upstream, not against each
    other).
    """
    warnings: List[str] = []

    def idx(level: str) -> int:
        return CONFIDENCE_ORDER.index(level)

    ei = results.get(models.STAGE_EVIDENCE_INTERPRETER)
    msb = results.get(models.STAGE_MARKET_STATE_BUILDER)
    ss = results.get(models.STAGE_STRATEGY_SELECTOR)
    rb = results.get(models.STAGE_RISK_BRAIN)
    cb = results.get(models.STAGE_CAPITAL_BRAIN)
    ep = results.get(models.STAGE_EXECUTION_PLANNER)

    edges = [
        ("evidence_interpreter->market_state_builder", ei and ei.ontology_snapshot.confidence, msb and msb.confidence),
        ("market_state_builder->strategy_selector", msb and msb.confidence, ss and ss.selection_confidence),
        ("market_state_builder->risk_brain", msb and msb.confidence, rb and rb.confidence),
        ("risk_brain->capital_brain", rb and rb.confidence, cb and cb.confidence),
        ("capital_brain->execution_planner", cb and cb.confidence, ep and ep.confidence),
    ]
    for label, upstream, downstream in edges:
        if upstream is None or downstream is None:
            continue
        if idx(downstream) > idx(upstream):
            warnings.append(f"CONFIDENCE_INCREASE:{label}")

    return warnings


def _check_propagation(results: Dict[str, object]) -> List[str]:
    """Verify UNKNOWN / NO_STRATEGY / DENY propagate downstream
    honestly rather than being silently dropped."""
    warnings: List[str] = []

    ss = results.get(models.STAGE_STRATEGY_SELECTOR)
    rb = results.get(models.STAGE_RISK_BRAIN)
    cb = results.get(models.STAGE_CAPITAL_BRAIN)
    ep = results.get(models.STAGE_EXECUTION_PLANNER)

    if ss and ss.selected_strategy is None and rb and rb.blocking_reason != "NO_STRATEGY":
        warnings.append("UNEXPECTED_PROPAGATION:no_strategy_not_reflected_in_risk_brain")

    if rb and rb.approval == "DENY" and cb and cb.allocation_status != "DENIED":
        warnings.append("UNEXPECTED_PROPAGATION:deny_not_reflected_in_capital_brain")

    if cb and cb.allocation_status == "DENIED" and ep and ep.status != "NOT_PLANNED":
        warnings.append("UNEXPECTED_PROPAGATION:denied_not_reflected_in_execution_planner")

    return warnings


def _build_trace(
    pipeline_status: str,
    completed_stages: Tuple[str, ...],
    failed_stage: Optional[str],
    deterministic: bool,
    warnings: Tuple[str, ...],
) -> str:
    lines = [f"Completed stages: {', '.join(completed_stages) if completed_stages else 'none'}."]
    if failed_stage:
        lines.append(f"Failed at: {failed_stage}.")
    lines.append(f"Deterministic: {deterministic}.")
    if warnings:
        lines.append("Warnings: " + "; ".join(warnings) + ".")
    else:
        lines.append("No warnings.")
    lines.append(f"Pipeline Status: {pipeline_status}.")
    return " ".join(lines)


def run_qualification(
    pipeline_input: models.PipelineInput,
    clock: Clock = _real_clock,
    replay_count: int = 10,
) -> models.DecisionPipelineQualification:
    """Run the complete frozen Trading Brain end-to-end `replay_count`
    times against the same `pipeline_input` and the same `clock`, and
    validate the result.

    Never places an order, never touches a broker, execution engine,
    or order manager, and never modifies any of the six frozen stage
    modules it calls -- it only invokes their own, already-tested
    `engine.py` entry points and inspects their outputs.
    """
    timestamp = clock().isoformat()

    runs: List[Tuple[Dict[str, object], Tuple[str, ...], Optional[str], Optional[Exception]]] = []
    for _ in range(max(replay_count, 1)):
        try:
            results, completed_stages, _ = _run_stages(pipeline_input, clock)
            runs.append((results, completed_stages, None, None))
        except StageFailure as failure:
            runs.append(({}, (), failure.stage, failure.error))

    first_results, first_completed, first_failed_stage, first_error = runs[0]
    deterministic = True
    for results, completed_stages, failed_stage, error in runs[1:]:
        if completed_stages != first_completed or failed_stage != first_failed_stage:
            deterministic = False
            break
        if not all(results.get(s) == first_results.get(s) for s in models.ALL_STAGE_NAMES):
            deterministic = False
            break

    first_fingerprint = compute_fingerprint(first_results, first_completed)
    for results, completed_stages, _fs, _err in runs[1:]:
        if compute_fingerprint(results, completed_stages) != first_fingerprint:
            deterministic = False
            break

    warnings: List[str] = []
    if first_failed_stage is None:
        warnings.extend(_check_provenance(first_results))
        warnings.extend(_check_confidence_monotonic(first_results))
        warnings.extend(_check_propagation(first_results))

    if not deterministic:
        warnings.append("NON_DETERMINISTIC_REPLAY")

    if first_failed_stage is not None:
        pipeline_status = models.PIPELINE_STATUS_FAILED
    elif not deterministic:
        pipeline_status = models.PIPELINE_STATUS_FAILED
    elif warnings:
        pipeline_status = models.PIPELINE_STATUS_COMPLETE_WITH_WARNINGS
    else:
        pipeline_status = models.PIPELINE_STATUS_COMPLETE

    trace = _build_trace(
        pipeline_status, first_completed, first_failed_stage, deterministic, tuple(warnings)
    )

    seed = "|".join([first_fingerprint, pipeline_status, timestamp])
    qualification_id = "DPQ-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return models.DecisionPipelineQualification(
        qualification_id=qualification_id,
        pipeline_status=pipeline_status,
        completed_stages=first_completed,
        failed_stage=first_failed_stage,
        decision_fingerprint=first_fingerprint,
        deterministic=deterministic,
        warnings=tuple(warnings),
        validation_trace=trace,
        timestamp=timestamp,
        version=models.QUALIFICATION_VERSION,
    )
