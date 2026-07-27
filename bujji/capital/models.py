"""Capital Management Engine — data models.

Pure, frozen dataclasses. No broker calls, no math beyond simple derived
properties — the engine (engine.py) owns the actual sizing algorithm; these
are its inputs/outputs, structured so every field the institutional Capital
Health Report needs is present and named exactly once.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class CapitalStatus(str, Enum):
    SAFE = "SAFE"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class CapitalSnapshot:
    """What the broker reports about the account right now.

    Every field is Optional and defaults to None — a broker adapter that
    cannot verify a particular figure must leave it None, never fabricate
    a plausible-looking number. `is_complete` is what the engine actually
    gates on.
    """

    account_equity: Optional[float] = None
    available_funds: Optional[float] = None
    available_margin: Optional[float] = None
    cash_balance: Optional[float] = None
    collateral: Optional[float] = None
    used_margin: Optional[float] = None
    available_exposure: Optional[float] = None
    peak_margin: Optional[float] = None
    as_of: Optional[datetime] = None

    @property
    def is_complete(self) -> bool:
        """The two figures the sizing algorithm actually needs.

        Collateral/cash_balance/used_margin/peak_margin/available_exposure
        are reported in the health report when the broker supplies them, but
        the ONLY hard requirement to size a trade at all is knowing
        available_margin. account_equity is required too, purely for the
        health report's transparency — a report claiming "SAFE" without
        being able to show equity is not trustworthy.
        """
        return self.available_margin is not None and self.account_equity is not None


@dataclass(frozen=True)
class MarginRequirement:
    """Broker-quoted (or, if unavailable, explicitly-flagged-as-unverified)
    margin required for ONE lot of the exact CE+PE straddle about to be
    sold."""

    margin_per_lot: Optional[float]
    verified: bool             # True only if a real broker margin-calculator
                                # response backs this number.
    source: str                # e.g. "fyers_margin_calculator" | "unverified"
    as_of: Optional[datetime] = None


@dataclass(frozen=True)
class SizingDecision:
    """The engine's final answer: how many lots, and why."""

    status: CapitalStatus
    approved_lots: int
    quantity: int                       # approved_lots * lot_size
    configured_max_lots: int
    maximum_safe_lots: int
    lot_size: int
    safety_buffer: float
    usable_margin: Optional[float]
    margin_required_per_lot: Optional[float]
    capital_utilization: Optional[float]   # fraction of available_margin used
    remaining_margin: Optional[float]
    reason: str
    snapshot: CapitalSnapshot
    margin: MarginRequirement
    timestamp: datetime

    @property
    def approved(self) -> bool:
        return self.status in (CapitalStatus.SAFE, CapitalStatus.WARNING) and self.approved_lots > 0

    def render(self) -> str:
        """The exact institutional Capital Health Report format."""
        def fmt(v: Optional[float]) -> str:
            return "NOT VERIFIED" if v is None else f"₹{v:,.2f}"

        lines = [
            "==========================",
            "CAPITAL HEALTH",
            "==========================",
            f"Account Equity        : {fmt(self.snapshot.account_equity)}",
            f"Available Funds       : {fmt(self.snapshot.available_funds)}",
            f"Available Margin      : {fmt(self.snapshot.available_margin)}",
            f"Collateral            : {fmt(self.snapshot.collateral)}",
            f"Used Margin           : {fmt(self.snapshot.used_margin)}",
            f"Required Margin (1 lot): {fmt(self.margin.margin_per_lot)}"
            f" ({'BROKER-VERIFIED' if self.margin.verified else 'UNVERIFIED'} — {self.margin.source})",
            f"Safety Buffer         : {self.safety_buffer:.0%}",
            f"Maximum Safe Lots     : {self.maximum_safe_lots}",
            f"Configured Max Lots   : {self.configured_max_lots}",
            f"Approved Lots         : {self.approved_lots}",
            f"Capital Utilization   : {'NOT VERIFIED' if self.capital_utilization is None else f'{self.capital_utilization:.1%}'}",
            f"Remaining Margin      : {fmt(self.remaining_margin)}",
            f"Status                : {self.status.value}",
            f"Reason                : {self.reason}",
            "==========================",
        ]
        return "\n".join(lines)

    def to_log(self) -> dict[str, Any]:
        return {
            "audit": "capital_health",
            "status": self.status.value,
            "approved_lots": self.approved_lots,
            "quantity": self.quantity,
            "configured_max_lots": self.configured_max_lots,
            "maximum_safe_lots": self.maximum_safe_lots,
            "lot_size": self.lot_size,
            "safety_buffer": self.safety_buffer,
            "usable_margin": self.usable_margin,
            "margin_required_per_lot": self.margin_required_per_lot,
            "margin_verified": self.margin.verified,
            "margin_source": self.margin.source,
            "capital_utilization": self.capital_utilization,
            "remaining_margin": self.remaining_margin,
            "reason": self.reason,
            "account_equity": self.snapshot.account_equity,
            "available_margin": self.snapshot.available_margin,
        }

    def to_dashboard(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "status": self.status.value,
            "reason": self.reason,
            "approved_lots": self.approved_lots,
            "configured_max_lots": self.configured_max_lots,
            "maximum_safe_lots": self.maximum_safe_lots,
            "safety_buffer": self.safety_buffer,
            "capital_utilization": self.capital_utilization,
            "remaining_margin": self.remaining_margin,
            "snapshot": asdict(self.snapshot),
            "margin": asdict(self.margin),
        }
