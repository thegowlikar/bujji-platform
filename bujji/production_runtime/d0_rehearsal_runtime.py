"""D0: fail-closed wiring rehearsal — BUJJI Options OS v3, Numeric Risk
Governor Gate D0.

Purpose: prove the Governor's `assess()` is actually reachable from a
runtime-shaped call site and its verdict is what a caller would see --
using real Gate A/B/C-scaffold objects, not hand-assembled fixtures.

Hard constraint: this module MUST have no successful dispatch path,
structurally, not merely by current data availability. That guarantee
is NOT a wrapped/checked call -- it is the literal ABSENCE of any
import of `bujji.runtime_execution`, `bujji.broker`, `bujji.broker_adapter`,
or `bujji.execution` anywhere in this file. There is no function here
that could invoke a broker or place/queue/dispatch an order, because no
such capability is ever imported into this module's namespace.
`tests/test_d0_rehearsal_runtime.py::test_no_dispatch_capable_imports`
verifies this automatically -- it is not left as a documentation claim.

This module does NOT modify, import from as a caller, or wire into
`bujji/production_runtime/composition_root.py`, `runtime.py`, or
`startup.py` -- it is entirely additive and stands alone. Promoting
this rehearsal into the real runtime (true Gate D) is a separate,
future, explicitly-approved step.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment
from bujji.trading_brain.risk_governor.engine import RiskVerdict, assess
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment
from bujji.trading_brain.risk_governor.position_group_fold import PositionGroupState

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class D0RehearsalInput:
    group_state: PositionGroupState
    defined_risk: DefinedRiskAssessment
    portfolio_limits: PortfolioLimitAssessment
    capital_check: CapitalCheckAssessment


@dataclass(frozen=True)
class D0RehearsalResult:
    """Terminal. Nothing in this module consumes `verdict` any further
    -- there is no next step, no dispatch call, no order construction.
    A caller receiving this back has reached the end of what D0 can
    ever do, by construction."""

    verdict: RiskVerdict
    rehearsal_note: str = (
        "D0 rehearsal only -- this verdict was never acted on. No dispatch "
        "call exists in this module's import graph or call chain."
    )


def run_d0_rehearsal(rehearsal_input: D0RehearsalInput, clock: Clock) -> D0RehearsalResult:
    """The entire rehearsal: call the real Governor assess() with real
    Gate A/B/C-scaffold objects, and stop. Nothing downstream of the
    returned RiskVerdict exists in this module -- there is no dispatch
    function to call even if this returned decision=='ALLOW'."""
    verdict = assess(
        rehearsal_input.group_state,
        rehearsal_input.defined_risk,
        rehearsal_input.portfolio_limits,
        rehearsal_input.capital_check,
        clock=clock,
    )
    return D0RehearsalResult(verdict=verdict)
