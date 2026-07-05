"""Decision Trace — every decision explains itself.

A :class:`DecisionTrace` is a structured, self-describing record of *how* a
decision was reached: the inputs observed, each named check with its result and
detail, and the final conclusion. Both the Signal Engine and the Trade Manager
attach a trace to their output, so every entry, hold, and exit is auditable
without re-deriving the logic.

The trace is a passive data object — it never influences the decision, it only
documents it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .models import CheckOutcome


@dataclass(frozen=True)
class DecisionTrace:
    """Structured explanation of a single decision."""

    source: str                 # e.g. "signal_engine" | "trade_manager".
    timestamp: datetime
    conclusion: str             # e.g. "HOLD", "EXIT", "ENTER", "NO_TRADE".
    reason: str
    inputs: dict[str, Any] = field(default_factory=dict)
    checks: tuple[CheckOutcome, ...] = ()

    @property
    def failed_checks(self) -> tuple[CheckOutcome, ...]:
        return tuple(c for c in self.checks if not c.passed)

    def to_log(self) -> dict[str, Any]:
        """Flat dict suitable for structured JSON logging."""
        payload: dict[str, Any] = {
            "source": self.source,
            "conclusion": self.conclusion,
            "reason": self.reason,
        }
        payload.update({f"in.{k}": v for k, v in self.inputs.items()})
        payload.update({f"chk.{c.name}": c.result.value for c in self.checks})
        return payload

    def render(self) -> str:
        """Multi-line human-readable explanation for logs/dashboard."""
        lines = [f"[{self.source}] {self.conclusion} @ "
                 f"{self.timestamp.strftime('%H:%M:%S')} — {self.reason}"]
        if self.inputs:
            kv = ", ".join(f"{k}={v}" for k, v in self.inputs.items())
            lines.append(f"  inputs: {kv}")
        for c in self.checks:
            mark = "PASS" if c.passed else "FAIL"
            lines.append(f"  [{mark}] {c.name}: {c.detail}")
        return "\n".join(lines)
