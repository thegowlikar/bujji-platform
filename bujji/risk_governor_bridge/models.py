"""Phase 20.17 -- pure data contracts. No IO, no broker, no execution,
no order/position-creation vocabulary anywhere in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

STATUS_ADMITTED = "ADMITTED"          # cleared every real, evaluated stage -- still not an order.
STATUS_BLOCKED = "BLOCKED"            # a real governor stage rejected the notional probe.
STATUS_NOT_EVALUATED = "NOT_EVALUATED"  # Cycle 1's own decision was never worth a governor review.
ALL_FINAL_STATUSES = (STATUS_ADMITTED, STATUS_BLOCKED, STATUS_NOT_EVALUATED)

# Deliberately trivial, disclosed, FIXED probe -- never a real proposed
# position size. Exists only to exercise the real governor's own
# admit/reject logic and compute its real, currently-available risk
# CEILING (`maximum_quantity`) -- the informative output is the
# ceiling itself, never this probe.
NOTIONAL_PROBE_MARGIN = 1.0
NOTIONAL_PROBE_MAX_LOSS = 1.0
NOTIONAL_PROBE_DESIRED_QUANTITY = 1
PROBE_DISCLOSURE = (
    f"Notional probe used: margin=₹{NOTIONAL_PROBE_MARGIN}, max_loss=₹{NOTIONAL_PROBE_MAX_LOSS}, "
    f"desired_quantity={NOTIONAL_PROBE_DESIRED_QUANTITY} unit -- a deliberately trivial, fixed value used "
    f"ONLY to exercise the real risk governor's own admit/reject logic and compute its real, currently-"
    f"available risk ceiling. This is NEVER a proposed order size, NEVER an actual position."
)


@dataclass(frozen=True)
class RiskGovernorAssessment:
    """The ONLY output of `bujji.risk_governor_bridge`. Carries a real
    ceiling (`maximum_quantity`) computed by the real risk governor
    pipeline (D.1-D.3) -- never a recommendation to trade, never an
    order, never a position. D.4 (existing-position lifecycle) and
    D.5 (adaptive strategy experience) are NOT evaluated -- Cycle 1
    has zero real open positions and zero real risk-memory entries;
    constructing fake ones for either stage would be fabrication, so
    both are honestly skipped rather than guessed (see `bridge.py`'s
    own docstring)."""

    # KNOWN CURRENT LIMIT (see bridge.py's own docstring): with zero real
    # open positions, D.2's real aggregator marks a flat book RISK_INVALID
    # (no real MarginSnapshot exists for an empty book, and a real
    # MarginSnapshot itself requires a real broker margin query against at
    # least one real leg). STATUS_ADMITTED is therefore not reachable
    # today -- this bridge reports that real D.2 block honestly rather
    # than fabricating a MarginSnapshot to force an artificial pass.

    strategy_name: str
    final_status: str                   # ALL_FINAL_STATUSES
    blocking_stage: Optional[str]        # None unless final_status == STATUS_BLOCKED.

    capital_status: Optional[str] = None
    capital_allowed: Optional[bool] = None
    capital_explanation: Optional[str] = None

    portfolio_status: Optional[str] = None
    portfolio_explanation: Optional[str] = None

    budget_status: Optional[str] = None
    budget_allowed: Optional[bool] = None
    budget_explanation: Optional[str] = None
    real_risk_ceiling_units: Optional[int] = None   # PositionSizeRecommendation.maximum_quantity, the real hard ceiling.

    probe_disclosure: str = PROBE_DISCLOSURE
    skipped_stages: Tuple[str, ...] = ("D.4_POSITION_LIFECYCLE", "D.5_ADAPTIVE_EXPERIENCE")
    skip_reason: str = "Cycle 1 has zero real open positions and zero real risk-memory entries -- neither stage can be honestly evaluated without fabricating one."

    def __post_init__(self) -> None:
        if self.final_status not in ALL_FINAL_STATUSES:
            raise ValueError(f"final_status={self.final_status!r} not in {ALL_FINAL_STATUSES}")

    def to_dict(self) -> dict:
        return {
            "strategy_name": self.strategy_name, "final_status": self.final_status,
            "blocking_stage": self.blocking_stage,
            "capital_status": self.capital_status, "capital_allowed": self.capital_allowed,
            "capital_explanation": self.capital_explanation,
            "portfolio_status": self.portfolio_status, "portfolio_explanation": self.portfolio_explanation,
            "budget_status": self.budget_status, "budget_allowed": self.budget_allowed,
            "budget_explanation": self.budget_explanation, "real_risk_ceiling_units": self.real_risk_ceiling_units,
            "probe_disclosure": self.probe_disclosure, "skipped_stages": list(self.skipped_stages),
            "skip_reason": self.skip_reason,
        }
