"""Decision Pipeline Qualification models — BUJJI Options OS v3,
Engineering Series 38, Sprint 1.

This package validates the frozen Trading Brain (Series 31-37). It
adds no new trading logic, no new decision authority, and modifies no
existing module. `PipelineInput` models one replayable bundle of
published MIC v2-shape intelligence -- the same raw classification
strings the Evidence Interpreter (Series 32) already accepts.
`DecisionPipelineQualification` is the frozen, immutable report this
package produces.

No dedicated `taxonomy.py` exists for this sprint (see the
deliverables list) -- the small number of finite constants needed
live directly here, next to the models that use them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

QUALIFICATION_VERSION = "1.0.0"

STAGE_EVIDENCE_INTERPRETER = "evidence_interpreter"
STAGE_MARKET_STATE_BUILDER = "market_state_builder"
STAGE_STRATEGY_SELECTOR = "strategy_selector"
STAGE_RISK_BRAIN = "risk_brain"
STAGE_CAPITAL_BRAIN = "capital_brain"
STAGE_EXECUTION_PLANNER = "execution_planner"

ALL_STAGE_NAMES = (
    STAGE_EVIDENCE_INTERPRETER,
    STAGE_MARKET_STATE_BUILDER,
    STAGE_STRATEGY_SELECTOR,
    STAGE_RISK_BRAIN,
    STAGE_CAPITAL_BRAIN,
    STAGE_EXECUTION_PLANNER,
)

PIPELINE_STATUS_UNKNOWN = "UNKNOWN"
PIPELINE_STATUS_COMPLETE = "COMPLETE"
PIPELINE_STATUS_COMPLETE_WITH_WARNINGS = "COMPLETE_WITH_WARNINGS"
PIPELINE_STATUS_FAILED = "FAILED"

ALL_PIPELINE_STATUSES = (
    PIPELINE_STATUS_UNKNOWN,
    PIPELINE_STATUS_COMPLETE,
    PIPELINE_STATUS_COMPLETE_WITH_WARNINGS,
    PIPELINE_STATUS_FAILED,
)

PIPELINE_STATUS_DESCRIPTIONS = {
    PIPELINE_STATUS_UNKNOWN: "Pipeline status could not be determined.",
    PIPELINE_STATUS_COMPLETE: "All six stages ran, replay was deterministic, and no warnings were raised.",
    PIPELINE_STATUS_COMPLETE_WITH_WARNINGS: "All six stages ran, but at least one validation warning was raised.",
    PIPELINE_STATUS_FAILED: "A stage raised an exception, or replay was not deterministic.",
}


@dataclass(frozen=True)
class PipelineInput:
    """One replayable bundle of published MIC v2-shape intelligence.

    Field shape mirrors evidence_interpreter/engine.py::interpret()'s
    own parameters exactly -- this package never invents a new input
    contract, it reuses the one the frozen Evidence Interpreter
    already defines.
    """

    market_context: Optional[str] = None
    market_opinion: Optional[str] = None
    context_stability: Optional[str] = None
    calibration: Optional[str] = None
    governance: Optional[str] = None
    lifecycle: Optional[str] = None
    contract: Optional[str] = None
    source: str = "replay"
    reasoning_summary: str = ""
    context_id: Optional[str] = None


@dataclass(frozen=True)
class DecisionPipelineQualification:
    qualification_id: str
    pipeline_status: str
    completed_stages: Tuple[str, ...]
    failed_stage: Optional[str]
    decision_fingerprint: str
    deterministic: bool
    warnings: Tuple[str, ...]
    validation_trace: str
    timestamp: str
    version: str
