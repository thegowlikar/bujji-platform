"""Replay Qualification models — BUJJI Options OS v3, Engineering
Series 46, Sprint 1.

These are qualification utilities only -- no production logic, no new
trading decisions. `ReplayScenario` bundles every external input the
full deterministic pipeline (Series 32-45) needs to run end to end;
`ReplayRunResult` and `QualificationReport` are the frozen, immutable
records this package produces. Every field is a plain type (str,
float, bool, int, tuple, dict of those) so both remain trivially
JSON-serializable without a dedicated serialization module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from ..trading_brain.nifty_contract_builder.models import (
    NiftyOptionChainSnapshot,
    NiftySpotSnapshot,
)
from ..trading_brain.order_construction.models import ExecutionPolicy, TradingConfiguration
from ..trading_brain.position_sizing.config import PositionSizingConfig
from ..trading_brain.position_sizing.models import CapitalPolicy, LotSpecification

# The 12 pipeline stages this package drives, in true dependency
# order. Note this differs slightly from the specification's own
# illustrative diagram ordering (which interleaves the NIFTY Contract
# Builder / Position Sizing / Order Construction / Runtime Execution
# Orchestrator after the Broker Adapter) -- those four modules do not
# actually depend on the Broker Adapter's output (see their own
# Series 42-45 architecture docs: NIFTY Contract Builder consumes
# StrategyDecision + CapitalDecision directly). This package calls
# every stage in its true dependency order, not the diagram's
# illustrative one.
STAGE_EVIDENCE_INTERPRETER = "evidence_interpreter"
STAGE_MARKET_STATE_BUILDER = "market_state_builder"
STAGE_STRATEGY_SELECTOR = "strategy_selector"
STAGE_RISK_BRAIN = "risk_brain"
STAGE_CAPITAL_BRAIN = "capital_brain"
STAGE_EXECUTION_PLANNER = "execution_planner"
STAGE_EXECUTION_ENGINE = "execution_engine"
STAGE_BROKER_ADAPTER = "broker_adapter"
STAGE_NIFTY_CONTRACT_BUILDER = "nifty_contract_builder"
STAGE_POSITION_SIZING = "position_sizing"
STAGE_ORDER_CONSTRUCTION = "order_construction"
STAGE_RUNTIME_EXECUTION = "runtime_execution"

ALL_STAGE_NAMES = (
    STAGE_EVIDENCE_INTERPRETER,
    STAGE_MARKET_STATE_BUILDER,
    STAGE_STRATEGY_SELECTOR,
    STAGE_RISK_BRAIN,
    STAGE_CAPITAL_BRAIN,
    STAGE_EXECUTION_PLANNER,
    STAGE_EXECUTION_ENGINE,
    STAGE_BROKER_ADAPTER,
    STAGE_NIFTY_CONTRACT_BUILDER,
    STAGE_POSITION_SIZING,
    STAGE_ORDER_CONSTRUCTION,
    STAGE_RUNTIME_EXECUTION,
)


@dataclass(frozen=True)
class ReplayScenario:
    """One replayable bundle covering every external input the full
    pipeline needs -- historical market data only, paper execution
    only, never a live broker.
    """

    scenario_id: str
    description: str
    market_context: Optional[str] = None
    market_opinion: Optional[str] = None
    context_stability: Optional[str] = None
    calibration: Optional[str] = None
    governance: Optional[str] = None
    lifecycle: Optional[str] = None
    contract: Optional[str] = None
    spot_snapshot: Optional[NiftySpotSnapshot] = None
    option_chain: Optional[NiftyOptionChainSnapshot] = None
    capital_policy: Optional[CapitalPolicy] = None
    lot_spec: Optional[LotSpecification] = None
    sizing_config: PositionSizingConfig = field(default_factory=PositionSizingConfig)
    execution_policy: Optional[ExecutionPolicy] = None
    trading_config: Optional[TradingConfiguration] = None
    broker: str = "FYERS"
    dispatch_should_fail: bool = False


@dataclass(frozen=True)
class ReplayRunResult:
    run_id: str
    scenario_id: str
    completed_stages: Tuple[str, ...]
    failed_stage: Optional[str]
    artifact_ids: Dict[str, Optional[str]]
    fingerprint: str
    deterministic: bool
    chain_valid: bool
    warnings: Tuple[str, ...]
    elapsed_seconds: float
    stage_timings: Dict[str, float]
    artifact_counts: Dict[str, int]
    timestamp: str
    version: str


@dataclass(frozen=True)
class QualificationReport:
    report_id: str
    total_scenarios: int
    passed_scenarios: int
    failed_scenarios: int
    deterministic_scenarios: int
    chain_valid_scenarios: int
    invariant_results: Dict[str, bool]
    scenario_run_ids: Tuple[str, ...]
    timestamp: str
    version: str


def run_result_to_dict(r: ReplayRunResult) -> dict:
    return {
        "run_id": r.run_id,
        "scenario_id": r.scenario_id,
        "completed_stages": list(r.completed_stages),
        "failed_stage": r.failed_stage,
        "artifact_ids": dict(r.artifact_ids),
        "fingerprint": r.fingerprint,
        "deterministic": r.deterministic,
        "chain_valid": r.chain_valid,
        "warnings": list(r.warnings),
        "elapsed_seconds": r.elapsed_seconds,
        "stage_timings": dict(r.stage_timings),
        "artifact_counts": dict(r.artifact_counts),
        "timestamp": r.timestamp,
        "version": r.version,
    }


def run_result_from_dict(d: dict) -> ReplayRunResult:
    return ReplayRunResult(
        run_id=d["run_id"],
        scenario_id=d["scenario_id"],
        completed_stages=tuple(d["completed_stages"]),
        failed_stage=d.get("failed_stage"),
        artifact_ids=dict(d["artifact_ids"]),
        fingerprint=d["fingerprint"],
        deterministic=d["deterministic"],
        chain_valid=d["chain_valid"],
        warnings=tuple(d["warnings"]),
        elapsed_seconds=d["elapsed_seconds"],
        stage_timings=dict(d["stage_timings"]),
        artifact_counts=dict(d["artifact_counts"]),
        timestamp=d["timestamp"],
        version=d["version"],
    )


def qualification_report_to_dict(r: QualificationReport) -> dict:
    return {
        "report_id": r.report_id,
        "total_scenarios": r.total_scenarios,
        "passed_scenarios": r.passed_scenarios,
        "failed_scenarios": r.failed_scenarios,
        "deterministic_scenarios": r.deterministic_scenarios,
        "chain_valid_scenarios": r.chain_valid_scenarios,
        "invariant_results": dict(r.invariant_results),
        "scenario_run_ids": list(r.scenario_run_ids),
        "timestamp": r.timestamp,
        "version": r.version,
    }


def qualification_report_from_dict(d: dict) -> QualificationReport:
    return QualificationReport(
        report_id=d["report_id"],
        total_scenarios=d["total_scenarios"],
        passed_scenarios=d["passed_scenarios"],
        failed_scenarios=d["failed_scenarios"],
        deterministic_scenarios=d["deterministic_scenarios"],
        chain_valid_scenarios=d["chain_valid_scenarios"],
        invariant_results=dict(d["invariant_results"]),
        scenario_run_ids=tuple(d["scenario_run_ids"]),
        timestamp=d["timestamp"],
        version=d["version"],
    )
