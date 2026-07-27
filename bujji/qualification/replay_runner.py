"""Replay Runner — BUJJI Options OS v3, Engineering Series 46, Sprint
1.

Runs the complete, frozen, deterministic pipeline (Series 32-45) end
to end against one `ReplayScenario`, using historical/synthetic data
only. No production logic lives here -- every stage call is a direct,
unmodified call into that stage's own frozen `engine.py`. This module
never connects to a broker, never authenticates, and never places a
live order.

`PaperExecutorStub` is the qualification-only stand-in for the
"Paper Executor" box at the bottom of the pipeline diagram. It is
deliberately trivial and deterministic -- it never simulates fills,
never applies randomness, and is not a copy of, or a replacement for,
production's own `bujji.broker.paper.PaperBroker` (which this package
never imports, per its own "qualification utilities only, no
production logic" scope). It exists solely to let the Runtime
Execution Orchestrator's `dispatch()` step reach a terminal state
during replay.
"""
from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from ..broker_adapter import engine as broker_adapter_engine
from ..runtime_execution import engine as runtime_execution_engine
from ..trading_brain.capital_brain import engine as capital_brain_engine
from ..trading_brain.evidence_interpreter import engine as evidence_interpreter_engine
from ..trading_brain.execution_engine import engine as execution_engine_engine
from ..trading_brain.execution_planner import engine as execution_planner_engine
from ..trading_brain.market_state import engine as market_state_engine
from ..trading_brain.nifty_contract_builder import engine as nifty_contract_builder_engine
from ..trading_brain.order_construction import engine as order_construction_engine
from ..trading_brain.position_sizing import engine as position_sizing_engine
from ..trading_brain.risk_brain import engine as risk_brain_engine
from ..trading_brain.strategy_selector import engine as strategy_selector_engine
from .replay_models import ALL_STAGE_NAMES, ReplayRunResult, ReplayScenario

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


class PaperExecutorStub:
    """Deterministic, no-op stand-in satisfying
    `runtime_execution.engine.ExecutionEngineInterface`. Records every
    call it receives; never performs I/O, never uses randomness.
    """

    def __init__(self, should_raise: bool = False) -> None:
        self.should_raise = should_raise
        self.calls: List[str] = []

    def submit_and_confirm(self, order_request) -> None:
        self.calls.append(order_request.client_order_id)
        if self.should_raise:
            raise RuntimeError("PaperExecutorStub configured to raise for negative qualification")
        return None


class StageFailure(Exception):
    def __init__(self, stage: str, error: Exception) -> None:
        self.stage = stage
        self.error = error
        super().__init__(f"{stage} failed: {error}")


_ID_ATTR_BY_STAGE = {
    "evidence_interpreter": "interpretation_id",
    "market_state_builder": "assessment_id",
    "strategy_selector": "decision_id",
    "risk_brain": "assessment_id",
    "capital_brain": "decision_id",
    "execution_planner": "plan_id",
    "execution_engine": "instruction_set_id",
    "broker_adapter": "request_id",
    "nifty_contract_builder": "construction_id",
    "position_sizing": "plan_id",
    "order_construction": "construction_id",
    "runtime_execution": "session_id",
}


def _run_stages(
    scenario: ReplayScenario, clock: Clock, executor: PaperExecutorStub
) -> Tuple[Dict[str, object], Tuple[str, ...], Optional[str], Dict[str, float]]:
    """Run all twelve stages in true dependency order, stopping at the
    first failure (an exception from a genuinely invalid input shape,
    or a FAILED-shaped business result from the four modules that
    never raise -- see the note inside `_run_stages_inner`). Returns
    (artifacts, completed_stage_names, failed_stage_or_None,
    stage_timings), preserving whatever partial progress was made
    before the failure. Never mutates `scenario`.
    """
    artifacts: Dict[str, object] = {}
    completed: List[str] = []
    timings: Dict[str, float] = {}

    try:
        _run_stages_inner(scenario, clock, executor, artifacts, completed, timings)
    except StageFailure as failure:
        return artifacts, tuple(completed), failure.stage, timings

    return artifacts, tuple(completed), None, timings


def _run_stages_inner(
    scenario: ReplayScenario,
    clock: Clock,
    executor: PaperExecutorStub,
    artifacts: Dict[str, object],
    completed: List[str],
    timings: Dict[str, float],
) -> None:

    def _run(stage: str, fn, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            timings[stage] = time.perf_counter() - start
            raise StageFailure(stage, exc) from exc
        timings[stage] = time.perf_counter() - start
        artifacts[stage] = result
        completed.append(stage)
        return result

    ei = _run(
        "evidence_interpreter",
        evidence_interpreter_engine.interpret,
        market_context=scenario.market_context,
        market_opinion=scenario.market_opinion,
        context_stability=scenario.context_stability,
        calibration=scenario.calibration,
        governance=scenario.governance,
        lifecycle=scenario.lifecycle,
        contract=scenario.contract,
        source="replay",
        clock=clock,
    )
    msb = _run("market_state_builder", market_state_engine.assess, ei, clock=clock)
    ss = _run("strategy_selector", strategy_selector_engine.select, msb, clock=clock)
    rb = _run("risk_brain", risk_brain_engine.assess, ss, msb, clock=clock)
    cb = _run("capital_brain", capital_brain_engine.authorize, rb, clock=clock)
    ep = _run("execution_planner", execution_planner_engine.plan, cb, ss, clock=clock)
    eis = _run("execution_engine", execution_engine_engine.orchestrate, ep, clock=clock)
    _run(
        "broker_adapter",
        broker_adapter_engine.translate,
        eis,
        broker=scenario.broker,
        clock=clock,
    )
    ccr = _run(
        "nifty_contract_builder",
        nifty_contract_builder_engine.build_contracts,
        ss,
        cb,
        scenario.spot_snapshot,
        scenario.option_chain,
        clock=clock,
    )
    # These four modules (Series 42-45) never raise for a business-
    # level failure -- they always return a frozen result object with
    # a FAILED-shaped status, by design (see each one's own
    # architecture doc: "never fabricate"). A raised exception in this
    # harness therefore only ever signals a genuinely invalid input
    # shape (e.g. the Evidence Interpreter's own ValueError on an
    # unrecognized classification string), never a normal construction
    # failure. To keep this replay's own atomicity guarantee --
    # "nothing downstream of a failed construction stage is ever
    # called" -- this harness raises StageFailure itself the moment a
    # FAILED-shaped status is observed, rather than waiting for an
    # exception that these modules never throw.
    if ccr.status != "CONSTRUCTED":
        raise StageFailure("nifty_contract_builder", ValueError(ccr.failure_reason))

    pp = _run(
        "position_sizing",
        position_sizing_engine.size_position,
        cb,
        ccr.contracts,
        scenario.capital_policy,
        scenario.lot_spec,
        scenario.sizing_config,
        clock=clock,
    )
    if pp.validation != "PASSED":
        raise StageFailure("position_sizing", ValueError(pp.failure_reason))

    ocr = _run(
        "order_construction",
        order_construction_engine.construct_orders,
        pp,
        scenario.execution_policy,
        scenario.trading_config,
        clock=clock,
    )
    if ocr.status != "CONSTRUCTED":
        raise StageFailure("order_construction", ValueError(ocr.failure_reason))

    session = _run(
        "runtime_execution",
        runtime_execution_engine.build_session,
        ocr.requests,
        clock=clock,
    )
    if session.execution_state not in ("READY", "DISPATCH_PENDING", "DISPATCHED"):
        raise StageFailure("runtime_execution", ValueError(session.failure_reason))

    queued = runtime_execution_engine.queue_for_dispatch(session, clock=clock)
    dispatched = runtime_execution_engine.dispatch(queued, executor, clock=clock)
    artifacts["runtime_execution"] = dispatched


def _artifact_id(artifacts: Dict[str, object], stage: str) -> Optional[str]:
    obj = artifacts.get(stage)
    if obj is None:
        return None
    return getattr(obj, _ID_ATTR_BY_STAGE[stage], None)


def compute_fingerprint(artifacts: Dict[str, object], completed_stages: Tuple[str, ...]) -> str:
    """One deterministic fingerprint over the entire completed chain.
    Every id in this pipeline is itself content-derived
    (`hashlib.md5`, never `uuid4`), so changing any upstream artifact
    changes at least one downstream id, and therefore this fingerprint.
    """
    if not completed_stages:
        return "RFP-" + hashlib.md5(b"EMPTY").hexdigest()[:16]
    ids = [_artifact_id(artifacts, stage) or "NONE" for stage in completed_stages]
    return "RFP-" + hashlib.md5("|".join(ids).encode()).hexdigest()[:16]


def _check_chain_consistency(artifacts: Dict[str, object]) -> List[str]:
    """Deliverable 5/6: verify the artifact chain and pipeline
    consistency -- strategy matches market state, contracts match
    strategy, quantities match capital intent, orders match contracts,
    execution session matches orders. Only meaningful once every stage
    has produced a real artifact; returns a list of violation strings
    (empty means fully consistent).
    """
    violations: List[str] = []
    ei = artifacts.get("evidence_interpreter")
    msb = artifacts.get("market_state_builder")
    ss = artifacts.get("strategy_selector")
    rb = artifacts.get("risk_brain")
    cb = artifacts.get("capital_brain")
    ep = artifacts.get("execution_planner")
    eis = artifacts.get("execution_engine")
    ber = artifacts.get("broker_adapter")
    ccr = artifacts.get("nifty_contract_builder")
    pp = artifacts.get("position_sizing")
    ocr = artifacts.get("order_construction")
    session = artifacts.get("runtime_execution")

    if ei and msb and msb.interpretation_id != ei.interpretation_id:
        violations.append("market_state_builder does not trace back to evidence_interpreter")
    if msb and ss and ss.market_state_assessment_id != msb.assessment_id:
        violations.append("strategy_selector does not trace back to market_state_builder")
    if ss and rb and rb.strategy_decision_id != ss.decision_id:
        violations.append("risk_brain does not trace back to strategy_selector")
    if rb and cb and cb.risk_assessment_id != rb.assessment_id:
        violations.append("capital_brain does not trace back to risk_brain")
    if cb and ep and ep.capital_decision_id != cb.decision_id:
        violations.append("execution_planner does not trace back to capital_brain")
    if ep and eis and eis.plan_id != ep.plan_id:
        violations.append("execution_engine does not trace back to execution_planner")
    if eis and ber and ber.instruction_set_id != eis.instruction_set_id:
        violations.append("broker_adapter does not trace back to execution_engine")
    if ss and ccr and ccr.strategy_id != ss.selected_strategy:
        violations.append("nifty_contract_builder's strategy does not match strategy_selector's decision")
    if cb and pp and (pp.capital_decision_id != cb.decision_id or pp.capital_intent != cb.capital_intent):
        violations.append("position_sizing's capital intent does not match capital_brain's decision")
    if ccr and pp and pp.contracts != ccr.contracts:
        violations.append("position_sizing's contracts do not match nifty_contract_builder's output")
    if pp and ocr and ocr.position_plan_id != pp.plan_id:
        violations.append("order_construction does not trace back to position_sizing")
    if ocr and session and session.order_requests != ocr.requests:
        violations.append("runtime_execution's orders do not match order_construction's requests")

    return violations


def run_replay(
    scenario: ReplayScenario,
    clock: Clock = _real_clock,
    replay_count: int = 3,
) -> ReplayRunResult:
    """Run the complete pipeline `replay_count` times against the same
    scenario and clock, and validate determinism, chain consistency,
    and artifact counts.

    Never connects to a broker, never authenticates, never places a
    live order -- only calls the frozen pipeline's own stage engines
    and a deterministic in-memory paper executor stub.
    """
    timestamp = clock().isoformat()
    overall_start = time.perf_counter()

    runs = []
    for _ in range(max(replay_count, 1)):
        executor = PaperExecutorStub(should_raise=scenario.dispatch_should_fail)
        artifacts, completed, failed_stage, timings = _run_stages(scenario, clock, executor)
        runs.append((artifacts, completed, failed_stage, timings))

    first_artifacts, first_completed, first_failed_stage, first_timings = runs[0]

    deterministic = True
    for artifacts, completed, failed_stage, _timings in runs[1:]:
        if completed != first_completed or failed_stage != first_failed_stage:
            deterministic = False
            break
        if not all(artifacts.get(s) == first_artifacts.get(s) for s in ALL_STAGE_NAMES):
            deterministic = False
            break

    fingerprint = compute_fingerprint(first_artifacts, first_completed)
    for artifacts, completed, _fs, _t in runs[1:]:
        if compute_fingerprint(artifacts, completed) != fingerprint:
            deterministic = False
            break

    warnings: List[str] = []
    chain_valid = False
    if first_failed_stage is None and set(first_completed) == set(ALL_STAGE_NAMES):
        violations = _check_chain_consistency(first_artifacts)
        chain_valid = not violations
        warnings.extend(violations)
    if not deterministic:
        warnings.append("NON_DETERMINISTIC_REPLAY")

    artifact_ids = {stage: _artifact_id(first_artifacts, stage) for stage in ALL_STAGE_NAMES}

    contracts = first_artifacts.get("nifty_contract_builder")
    orders = first_artifacts.get("order_construction")
    artifact_counts = {
        "completed_stages": len(first_completed),
        "total_stages": len(ALL_STAGE_NAMES),
        "contracts": len(contracts.contracts) if contracts else 0,
        "order_requests": len(orders.requests) if orders else 0,
    }

    elapsed = time.perf_counter() - overall_start

    seed = "|".join([scenario.scenario_id, fingerprint, timestamp])
    run_id = "RUN-" + hashlib.md5(seed.encode()).hexdigest()[:16]

    return ReplayRunResult(
        run_id=run_id,
        scenario_id=scenario.scenario_id,
        completed_stages=first_completed,
        failed_stage=first_failed_stage,
        artifact_ids=artifact_ids,
        fingerprint=fingerprint,
        deterministic=deterministic,
        chain_valid=chain_valid,
        warnings=tuple(warnings),
        elapsed_seconds=elapsed,
        stage_timings=first_timings,
        artifact_counts=artifact_counts,
        timestamp=timestamp,
        version="1.0.0",
    )
