"""Runtime — BUJJI Options OS v3, Engineering Series 54.

Implements the three operating modes by calling each already-frozen
stage's own `engine.py` in true dependency order (the same order
established and proven by the Series 46 replay qualification) --
never reimplementing any stage's own logic. This module is pure
orchestration: it decides *which* stages to call for a given mode, and
threads already-constructed dependencies (from `CompositionRoot`)
between them; it decides nothing about strategy, risk, capital, or
execution.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional, Tuple

from ..authentication import engine as authentication_engine
from ..broker_adapter import engine as broker_adapter_engine
from ..runtime_execution import engine as runtime_execution_engine
from ..runtime_safety import engine as runtime_safety_engine
from ..runtime_safety.models import QualificationPolicy
from ..runtime_session import engine as runtime_session_engine
from ..trading_brain.capital_brain import engine as capital_brain_engine
from ..trading_brain.evidence_interpreter import engine as evidence_interpreter_engine
from ..trading_brain.execution_engine import engine as trading_execution_engine
from ..trading_brain.execution_planner import engine as execution_planner_engine
from ..trading_brain.market_state import engine as market_state_engine
from ..trading_brain.nifty_contract_builder.models import NiftyOptionChainSnapshot, NiftySpotSnapshot
from ..trading_brain.nifty_contract_builder import engine as nifty_contract_builder_engine
from ..trading_brain.order_construction import engine as order_construction_engine
from ..trading_brain.position_sizing import engine as position_sizing_engine
from ..trading_brain.risk_brain import engine as risk_brain_engine
from ..trading_brain.strategy_selector import engine as strategy_selector_engine
from .composition_root import CompositionRoot
from .config import RUNTIME_MODE_PRODUCTION_READY, RUNTIME_MODE_READ_ONLY, RUNTIME_MODE_SHADOW

Clock = Callable[[], datetime]


def _real_clock() -> datetime:
    return datetime.now()


@dataclass(frozen=True)
class PipelineInput:
    """The Evidence Interpreter's own input shape (Series 32), reused
    verbatim -- this module never invents a second input contract.
    """

    market_context: Optional[str] = None
    market_opinion: Optional[str] = None
    context_stability: Optional[str] = None
    calibration: Optional[str] = None
    governance: Optional[str] = None
    lifecycle: Optional[str] = None
    contract: Optional[str] = None


@dataclass(frozen=True)
class ReadOnlyResult:
    mode: str
    evidence_interpretation: object
    market_state_assessment: object
    strategy_decision: object
    risk_assessment: object
    capital_decision: object
    execution_plan: object
    trace: str
    timestamp: str


@dataclass(frozen=True)
class ShadowResult:
    mode: str
    evidence_interpretation: object
    market_state_assessment: object
    strategy_decision: object
    risk_assessment: object
    capital_decision: object
    execution_plan: object
    execution_instruction_set: object
    broker_execution_request: object
    contract_construction_result: object
    position_plan: object
    order_construction_result: object
    execution_session: object
    runtime_authorization: object
    runtime_session: object
    broker_session: object
    order_submitted: bool
    trace: str
    timestamp: str


def _run_decision_pipeline(root: CompositionRoot, pipeline_input: PipelineInput, clock: Clock):
    ei = evidence_interpreter_engine.interpret(
        market_context=pipeline_input.market_context,
        market_opinion=pipeline_input.market_opinion,
        context_stability=pipeline_input.context_stability,
        calibration=pipeline_input.calibration,
        governance=pipeline_input.governance,
        lifecycle=pipeline_input.lifecycle,
        contract=pipeline_input.contract,
        source="production",
        clock=clock,
    )
    msb = market_state_engine.assess(ei, clock=clock)
    ss = strategy_selector_engine.select(msb, clock=clock)
    rb = risk_brain_engine.assess(ss, msb, clock=clock)
    cb = capital_brain_engine.authorize(rb, clock=clock)
    ep = execution_planner_engine.plan(cb, ss, clock=clock)
    return ei, msb, ss, rb, cb, ep


def run_read_only(
    root: CompositionRoot,
    pipeline_input: PipelineInput,
    clock: Clock = _real_clock,
) -> ReadOnlyResult:
    """Mode 1: run the complete decision pipeline through the
    Execution Planner and stop. Never touches Order Construction, the
    Runtime, the Safety Gate, or either integration adapter.
    """
    timestamp = clock().isoformat()
    ei, msb, ss, rb, cb, ep = _run_decision_pipeline(root, pipeline_input, clock)
    trace = (
        f"READ_ONLY: Evidence -> MarketState({msb.market_state}) -> "
        f"Strategy({ss.selected_strategy}) -> Risk({rb.status}) -> "
        f"Capital({cb.capital_intent}) -> ExecutionPlan({ep.status}). Stopped before Order Construction."
    )
    return ReadOnlyResult(
        mode=RUNTIME_MODE_READ_ONLY,
        evidence_interpretation=ei,
        market_state_assessment=msb,
        strategy_decision=ss,
        risk_assessment=rb,
        capital_decision=cb,
        execution_plan=ep,
        trace=trace,
        timestamp=timestamp,
    )


def run_shadow(
    root: CompositionRoot,
    pipeline_input: PipelineInput,
    spot_snapshot: Optional[NiftySpotSnapshot],
    option_chain: Optional[NiftyOptionChainSnapshot],
    existing_session_ids: Tuple[str, ...] = (),
    clock: Clock = _real_clock,
) -> ShadowResult:
    """Mode 2: run the entire pipeline, including the Runtime, the
    Safety Gate, both integration adapters, and a real dispatch --
    terminating at whatever no-op/paper broker `root.broker` was
    constructed as (never a live broker; see
    docs/SHADOW_RUNTIME_GUIDE.md). This is the default mode for
    qualification.
    """
    timestamp = clock().isoformat()
    ei, msb, ss, rb, cb, ep = _run_decision_pipeline(root, pipeline_input, clock)

    eis = trading_execution_engine.orchestrate(ep, clock=clock)
    ber = broker_adapter_engine.translate(eis, broker=root.config.broker_display_name, clock=clock)
    ccr = nifty_contract_builder_engine.build_contracts(ss, cb, spot_snapshot, option_chain, clock=clock)
    pp = position_sizing_engine.size_position(
        cb, ccr.contracts, root.capital_policy, root.lot_spec, root.sizing_config, clock=clock
    )
    ocr = order_construction_engine.construct_orders(
        pp, root.execution_policy, root.trading_config, clock=clock
    )
    exec_session = runtime_execution_engine.build_session(ocr.requests, clock=clock)

    qualification_policy = QualificationPolicy(
        replay_status=root.config.replay_status,
        qualification_fingerprint=root.config.qualification_fingerprint,
        chain_valid=root.config.chain_valid,
        deterministic=root.config.deterministic,
    )
    authorization = runtime_safety_engine.authorize(
        exec_session, qualification_policy, root.runtime_safety_policy, clock=clock
    )

    runtime_session = runtime_session_engine.create_session(
        authorization, root.runtime_session_policy, existing_session_ids=existing_session_ids, clock=clock
    )
    if runtime_session.session_state == "CREATED":
        runtime_session = runtime_session_engine.initialize(runtime_session, clock=clock)
        runtime_session = runtime_session_engine.mark_ready(runtime_session, clock=clock)
        runtime_session = runtime_session_engine.activate(runtime_session, clock=clock)

    broker_session = authentication_engine.create_broker_session(
        runtime_session, root.authentication_policy, clock=clock
    )
    broker_session = authentication_engine.begin_authentication(broker_session, clock=clock)
    broker_session = authentication_engine.complete_authentication(
        broker_session, root.authentication_adapter, root.authentication_policy, clock=clock
    )
    if broker_session.authentication_state == "AUTHENTICATED":
        broker_session = authentication_engine.connect(broker_session, clock=clock)
        broker_session = authentication_engine.mark_ready(broker_session, clock=clock)

    order_submitted = False
    if (
        exec_session.execution_state == "READY"
        and runtime_session.session_state == "ACTIVE"
        and broker_session.session_state == "READY"
        and authorization.decision == "ALLOW"
    ):
        exec_session = runtime_execution_engine.queue_for_dispatch(exec_session, clock=clock)
        exec_session = runtime_execution_engine.dispatch(
            exec_session, root.execution_adapter, clock=clock
        )
        # `order_submitted` reflects only that a call reached the
        # constructed broker object (PaperBroker in Shadow mode,
        # in-memory and simulated) -- never a claim that a real broker
        # order was placed. See Gap 3 in docs/SHADOW_RUNTIME_GUIDE.md.
        order_submitted = exec_session.execution_state == "DISPATCHED"

    trace = (
        f"SHADOW: {len(ocr.requests)} OrderRequest(s) constructed. "
        f"ExecutionSession={exec_session.execution_state}. Authorization={authorization.authorization_state}. "
        f"RuntimeSession={runtime_session.session_state}. BrokerSession={broker_session.authentication_state}/"
        f"{broker_session.session_state}. order_submitted={order_submitted} (never a live broker order)."
    )

    return ShadowResult(
        mode=RUNTIME_MODE_SHADOW,
        evidence_interpretation=ei,
        market_state_assessment=msb,
        strategy_decision=ss,
        risk_assessment=rb,
        capital_decision=cb,
        execution_plan=ep,
        execution_instruction_set=eis,
        broker_execution_request=ber,
        contract_construction_result=ccr,
        position_plan=pp,
        order_construction_result=ocr,
        execution_session=exec_session,
        runtime_authorization=authorization,
        runtime_session=runtime_session,
        broker_session=broker_session,
        order_submitted=order_submitted,
        trace=trace,
        timestamp=timestamp,
    )


def verify_production_ready_construction(root: CompositionRoot) -> bool:
    """Mode 3: verify only that the real production object graph
    constructs successfully -- never connects, never dispatches. See
    startup.py for how this is used during the startup sequence.
    """
    return (
        root.broker is not None
        and root.execution_engine is not None
        and root.authentication_adapter is not None
        and root.execution_adapter is not None
    )
