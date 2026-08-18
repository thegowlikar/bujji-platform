"""Phase 20.18 -- paper-only execution simulator. NOT a broker.

Pure composition of the already-existing, already-tested
`bujji.broker.simulation.{fill_simulator,slippage,market_snapshot}`
(Gate F.2) -- no fill/slippage/latency math is reimplemented here.
Zero imports from `bujji.broker.base`/`bujji.broker.fyers`/`bujji.
broker.hybrid`/`bujji.broker.guard` or any live-broker module: this
module reaches ONLY the deterministic, injectable, broker-independent
simulation primitives, never anything that could reach a real account.

`simulate_execution()` NEVER fabricates a `MarketSnapshot` internally
-- it is always caller-injected, exactly like every prior phase's own
"never invent the input" discipline.
"""
from __future__ import annotations

import random
from typing import Optional

from bujji.broker.simulation.fill_simulator import (
    FillSimulator, LatencyConfig, LatencyMode, PartialFillConfig, RejectionConfig,
)
from bujji.broker.simulation.market_snapshot import MarketSnapshot
from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode
from bujji.risk_context_adapter import (
    RiskContextAssessment, STATUS_NOT_EVALUATED, STATUS_RESTRICTED, STATUS_UNAVAILABLE_RISK_CONTEXT,
)

from .models import (
    QUALITY_DEGRADED, QUALITY_GOOD, QUALITY_REJECTED,
    STATUS_FILLED, STATUS_PARTIAL, STATUS_REJECTED,
    ExecutionPlan, ExecutionResult,
)

_SIMULATION_BLOCKED_STATUSES = (STATUS_RESTRICTED, STATUS_UNAVAILABLE_RISK_CONTEXT, STATUS_NOT_EVALUATED)

# Illustrative, disclosed defaults -- matches planner.py's own
# "illustrative, not calibrated" values.
_DEFAULT_SLIPPAGE_CONFIG = SlippageConfig(mode=SlippageMode.PERCENTAGE, percentage=0.001)
_DEFAULT_LATENCY_CONFIG = LatencyConfig(mode=LatencyMode.FIXED, fixed_ms=150.0)
_DEFAULT_REJECTION_CONFIG = RejectionConfig()
_DEFAULT_PARTIAL_FILL_CONFIG = PartialFillConfig()

# Illustrative fill-quality thresholds, disclosed here rather than left implicit.
_SLIPPAGE_DEGRADED_THRESHOLD = 5.0    # absolute price-delta units.
_LATENCY_DEGRADED_THRESHOLD_MS = 500.0


def simulation_permitted(risk_assessment: Optional[RiskContextAssessment]) -> bool:
    """`True` only when the real, unmodified Risk Context Adapter
    found no capital restriction and did evaluate the opportunity at
    all -- `NOT_READY_FOR_CAPITAL_APPROVAL`/`READY_FOR_REVIEW` permit
    exploratory simulation; `RESTRICTED`/`UNAVAILABLE_RISK_CONTEXT`/
    `NOT_EVALUATED` do not."""
    if risk_assessment is None:
        return False
    return risk_assessment.status not in _SIMULATION_BLOCKED_STATUSES


def simulate_execution(
    plan: ExecutionPlan, snapshot: MarketSnapshot, *, requested_qty: int = 1, side: str = "BUY",
    seed: int = 0,
) -> Optional[ExecutionResult]:
    """Returns `None` when `plan.simulation_required` is `False` --
    never simulates a fill for a plan the Risk Context Adapter marked
    ineligible. `requested_qty` defaults to 1 (a notional probe unit,
    matching Phase 20.17's own precedent) -- this is NEVER a real
    proposed position size; no caller of this function may treat it
    as one. `seed` makes the simulation fully deterministic and
    reproducible for identical inputs (`random.Random(seed)` is the
    only randomness source, matching `FillSimulator`'s own injectable-
    rng discipline)."""
    if not plan.simulation_required:
        return None

    rng = random.Random(seed)
    fill = FillSimulator.simulate(
        requested_qty=requested_qty, side=side, snapshot=snapshot,
        slippage_config=_DEFAULT_SLIPPAGE_CONFIG, latency_config=_DEFAULT_LATENCY_CONFIG,
        rejection_config=_DEFAULT_REJECTION_CONFIG, partial_fill_config=_DEFAULT_PARTIAL_FILL_CONFIG,
        rng=rng,
    )

    if fill.status == STATUS_REJECTED:
        quality = QUALITY_REJECTED
        notes = f"Simulated rejection: {fill.rejection_reason}."
    else:
        degraded = fill.slippage > _SLIPPAGE_DEGRADED_THRESHOLD or fill.latency_ms > _LATENCY_DEGRADED_THRESHOLD_MS
        quality = QUALITY_DEGRADED if degraded else QUALITY_GOOD
        notes = (
            f"Simulated {fill.status.lower()} fill: slippage={fill.slippage:.4f}, latency={fill.latency_ms:.1f}ms."
            + (" Degraded fill quality." if degraded else "")
        )

    status = STATUS_FILLED if fill.status == "FILLED" else (STATUS_PARTIAL if fill.status == "PARTIAL" else STATUS_REJECTED)

    return ExecutionResult(
        intent_id=plan.intent_id, status=status, simulated_fill_quality=quality,
        slippage_observed=fill.slippage, latency_observed=fill.latency_ms, execution_notes=notes,
    )
