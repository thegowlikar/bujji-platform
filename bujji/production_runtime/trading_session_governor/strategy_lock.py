"""Strategy Lock -- BUJJI Options OS v3, Gate V1.1 Component 3.

Immutable, one-time-only session decision. Once locked, no other
strategy can replace it for the rest of the session -- enforced
structurally: `lock()` raises on a second call, and the locked
`StrategyDecision` is a frozen dataclass, never mutated in place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class StrategyDecision:
    session_id: str
    timestamp: datetime
    trend_regime: str
    volatility_regime: str
    selected_strategy: str
    reasoning: str
    confidence: str
    locked: bool = True


class StrategyAlreadyLockedError(Exception):
    """Raised on any attempt to lock a second strategy in one session."""


class StrategyLock:
    def __init__(self) -> None:
        self._decision: Optional[StrategyDecision] = None

    def is_locked(self) -> bool:
        return self._decision is not None

    def lock(self, decision: StrategyDecision) -> None:
        if self._decision is not None:
            raise StrategyAlreadyLockedError(
                f"session already locked to {self._decision.selected_strategy!r} at "
                f"{self._decision.timestamp.isoformat()} -- cannot lock {decision.selected_strategy!r}"
            )
        self._decision = decision

    @property
    def decision(self) -> Optional[StrategyDecision]:
        return self._decision
