"""Phase 20.17.1 -- pure data contracts. No IO, no broker, no
execution, no order/position-creation vocabulary anywhere in this
module. `RiskContextRequest`/`RiskContextAssessment` are REQUEST/
ASSESSMENT objects -- never a position, never an order, never an
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple

# -- RiskContextAssessment.status -----------------------------------------
# A pre-trade opportunity being reviewed for capital admission has cleared
# D.1 (capital safety) and has no live portfolio/margin context to check
# against yet -- the correct, honest outcome given Phase 20.17's own
# discovered structural gap (D.2 requires a real MarginSnapshot, which
# requires a real position to query). NOT a rejection of the opportunity.
STATUS_NOT_READY_FOR_CAPITAL_APPROVAL = "NOT_READY_FOR_CAPITAL_APPROVAL"
# D.1 itself (a real, already-computed governor decision) found a genuine
# capital restriction -- e.g. insufficient available capital, breached
# drawdown/margin threshold. A real finding, not a missing-context gap.
STATUS_RESTRICTED = "RESTRICTED"
# No compatible pre-trade interface / no capital_snapshot was available to
# evaluate against at all -- never fabricated, always disclosed.
STATUS_UNAVAILABLE_RISK_CONTEXT = "UNAVAILABLE_RISK_CONTEXT"
# Cycle 1's own Decision Brain already found this not worth reviewing
# (NO_OPPORTUNITY / BLOCKED / INSUFFICIENT_INTELLIGENCE) -- the Risk
# Governor cannot rescue a decision Cycle 1's own evidence chain already
# rejected (mirrors Phase 20.10's own Rule 3, and Phase 20.17's own
# identical precedent).
STATUS_NOT_EVALUATED = "NOT_EVALUATED"
# Reserved for when a live portfolio/margin context genuinely becomes
# available (future phase) -- not reachable with today's real data, but
# a legitimate outcome this adapter's contract must be able to express.
STATUS_READY_FOR_REVIEW = "READY_FOR_REVIEW"

ALL_STATUSES = (
    STATUS_READY_FOR_REVIEW, STATUS_NOT_READY_FOR_CAPITAL_APPROVAL,
    STATUS_RESTRICTED, STATUS_UNAVAILABLE_RISK_CONTEXT, STATUS_NOT_EVALUATED,
)


@dataclass(frozen=True)
class RiskContextRequest:
    """Cycle 1's own opportunity, translated into the shape a
    pre-trade-compatible risk review needs. A REQUEST, not a position
    or an order -- nothing here is minted into Gate A, nothing here is
    submitted to a broker.

    `opportunity_id` is `strategy_name` itself -- Cycle 1 runs at most
    one opportunity per strategy per cycle and has no separate
    opportunity-identity scheme; reusing `strategy_name` is an honest
    surrogate, not an invented ID.

    `expected_exposure`/`expected_loss_boundary` are always `None`:
    Cycle 1 never computes a real dollar position size (no position
    sizing for execution, by explicit design boundary) -- these fields
    exist so a future phase that DOES compute a real proposed size can
    populate them without a contract change; this phase never
    fabricates a value for either.
    """

    strategy_name: str
    opportunity_id: str
    allocation_class: Optional[str]
    decision_state: str
    expected_exposure: Optional[float]
    expected_loss_boundary: Optional[float]
    confidence_level: Optional[str]
    market_regime: Optional[str]
    timestamp: datetime


@dataclass(frozen=True)
class RiskContextAssessment:
    """The ONLY output of `bujji.risk_context_adapter`. Never an
    approval to trade, never a position, never an order -- `status`
    communicates readiness for further review, not permission."""

    status: str
    risk_context_valid: bool
    governor_response: Optional[str]
    blockers: Tuple[str, ...]
    explanation: str

    def __post_init__(self) -> None:
        if self.status not in ALL_STATUSES:
            raise ValueError(f"status={self.status!r} not in {ALL_STATUSES}")

    def to_dict(self) -> dict:
        return {
            "status": self.status, "risk_context_valid": self.risk_context_valid,
            "governor_response": self.governor_response, "blockers": list(self.blockers),
            "explanation": self.explanation,
        }
