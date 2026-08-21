"""Phase 20.18 -- translates Cycle 1's own Decision Brain + Risk
Context Adapter outputs into execution-simulation inputs. Pure
translation and a small, disclosed, illustrative configuration --
zero broker/order/capital logic.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bujji.decision_orchestration import BLOCKED, FinalDecision, NO_OPPORTUNITY
from bujji.risk_context_adapter import RiskContextAssessment, STATUS_NOT_EVALUATED

from .models import ExecutionIntent, ExecutionPlan

_REJECTED_DECISION_STATES = (NO_OPPORTUNITY, BLOCKED)

EXPECTED_BEHAVIOR_SIMULATE = (
    "Cycle 1's own Decision Brain and Risk Context Adapter both permit further exploration -- "
    "a paper-only execution simulation will run to estimate fill quality under injected market conditions."
)

# Illustrative, disclosed simulation configuration -- same "illustrative
# defaults, not calibrated" precedent as CapitalSafetyThresholds() etc.
# elsewhere in this codebase. Never fitted to real market data by this phase.
ILLUSTRATIVE_EXPECTED_LATENCY_MS = 150.0
ILLUSTRATIVE_EXPECTED_SLIPPAGE_RANGE = (0.0, 0.50)
EXECUTION_STYLE_SIMULATED_MARKET = "SIMULATED_MARKET"
ENTRY_ASSUMPTION = (
    "Immediate simulated entry at the injected MarketSnapshot's last observed price -- "
    "no real order routing, no broker interaction, no real market impact."
)


def build_execution_intent(
    final_decision: FinalDecision, risk_assessment: Optional[RiskContextAssessment], *, timestamp: datetime,
) -> Optional[ExecutionIntent]:
    """Returns `None` (NO_EXECUTION_INTENT) whenever Cycle 1's own
    Decision Brain rejected the opportunity (`NO_OPPORTUNITY`/
    `BLOCKED`) or the Risk Context Adapter never evaluated it at all
    (`STATUS_NOT_EVALUATED`) -- a rejected decision is never converted
    into an execution intent."""
    if final_decision.decision_state in _REJECTED_DECISION_STATES:
        return None
    if risk_assessment is not None and risk_assessment.status == STATUS_NOT_EVALUATED:
        return None

    market_regime = None
    confidence = None
    if final_decision.allocation is not None:
        assessment = final_decision.allocation.candidate.assessment
        market_regime = assessment.environment.mic_regime
        confidence = assessment.strategy_score.confidence

    return ExecutionIntent(
        decision_id=final_decision.strategy_name or "UNKNOWN",
        strategy_name=final_decision.strategy_name or "UNKNOWN",
        direction=None, timeframe=None,
        market_regime=market_regime, confidence=confidence,
        expected_behavior=EXPECTED_BEHAVIOR_SIMULATE, timestamp=timestamp,
    )


def build_execution_plan(intent: ExecutionIntent, risk_assessment: Optional[RiskContextAssessment]) -> ExecutionPlan:
    """`simulation_required` is the one field carrying real
    information -- see `simulator.py`'s own gate, which reads this
    same field rather than re-deriving it from `risk_assessment`."""
    from .simulator import simulation_permitted

    return ExecutionPlan(
        intent_id=intent.decision_id, entry_assumption=ENTRY_ASSUMPTION,
        execution_style=EXECUTION_STYLE_SIMULATED_MARKET,
        expected_latency=ILLUSTRATIVE_EXPECTED_LATENCY_MS,
        expected_slippage_range=ILLUSTRATIVE_EXPECTED_SLIPPAGE_RANGE,
        simulation_required=simulation_permitted(risk_assessment),
    )
