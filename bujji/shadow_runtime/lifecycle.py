"""Runtime Lifecycle Model -- Shadow Runtime, Phase 19.10.1.

Explicit session-boundary states `shadow_runtime` never had before
(Phase 19.10.0's own confirmed gap). Pure model -- no IO, no broker,
no clock call. Every transition is deterministic and serializable, so
the same real sequence of calls always produces the same lifecycle
history, LIVE or HISTORICAL_REPLAY.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Tuple


class RuntimeStage(str, Enum):
    INITIALIZING = "INITIALIZING"
    WAITING_FOR_SESSION = "WAITING_FOR_SESSION"
    COLLECTING = "COLLECTING"
    PROCESSING_INTELLIGENCE = "PROCESSING_INTELLIGENCE"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


# Documented, exhaustive legal-transition table -- an illegal transition
# is a real programming error, never silently allowed. FAILED is
# reachable from every non-terminal stage (a real failure can happen at
# any point); COMPLETED and FAILED are both terminal. FINALIZING is ALSO
# reachable from every non-terminal stage before it, not only from
# PROCESSING_INTELLIGENCE: `ShadowSessionRunner.start()`'s own `finally`
# block always builds the session artifact -- "finalize" -- regardless
# of which stage a real failure interrupted (a caught startup error
# never even reaches COLLECTING, for instance), so the lifecycle model
# must allow that same real path, not force a stage sequence the actual
# runner cannot honor. Caught by this phase's own real-caller smoke
# test, not assumed correct from the table alone.
_LEGAL_TRANSITIONS: Dict[RuntimeStage, Tuple[RuntimeStage, ...]] = {
    RuntimeStage.INITIALIZING: (RuntimeStage.WAITING_FOR_SESSION, RuntimeStage.FINALIZING, RuntimeStage.FAILED),
    RuntimeStage.WAITING_FOR_SESSION: (RuntimeStage.COLLECTING, RuntimeStage.FINALIZING, RuntimeStage.FAILED),
    RuntimeStage.COLLECTING: (RuntimeStage.PROCESSING_INTELLIGENCE, RuntimeStage.FINALIZING, RuntimeStage.FAILED),
    RuntimeStage.PROCESSING_INTELLIGENCE: (RuntimeStage.FINALIZING, RuntimeStage.FAILED),
    RuntimeStage.FINALIZING: (RuntimeStage.COMPLETED, RuntimeStage.FAILED),
    RuntimeStage.COMPLETED: (),
    RuntimeStage.FAILED: (),
}

TERMINAL_STAGES = (RuntimeStage.COMPLETED, RuntimeStage.FAILED)


class IllegalLifecycleTransition(RuntimeError):
    """Raised when a transition is attempted that `_LEGAL_TRANSITIONS`
    does not permit -- fails closed rather than silently accepting an
    out-of-order stage change."""


@dataclass(frozen=True)
class LifecycleTransition:
    from_stage: Optional[RuntimeStage]  # None for the very first transition (into INITIALIZING).
    to_stage: RuntimeStage
    at: str  # isoformat, from the caller's own injected clock -- never wall-clock.
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "from_stage": self.from_stage.value if self.from_stage else None,
            "to_stage": self.to_stage.value, "at": self.at, "reason": self.reason,
        }


@dataclass(frozen=True)
class RuntimeLifecycle:
    """Immutable, replay-safe lifecycle history. `advance()` returns a
    NEW `RuntimeLifecycle` (never mutates in place) -- the same
    "no mutable historical facts" discipline this project has followed
    since Phase 18.x."""

    current_stage: RuntimeStage
    transitions: Tuple[LifecycleTransition, ...] = field(default_factory=tuple)

    @staticmethod
    def start(at: str) -> "RuntimeLifecycle":
        transition = LifecycleTransition(from_stage=None, to_stage=RuntimeStage.INITIALIZING, at=at)
        return RuntimeLifecycle(current_stage=RuntimeStage.INITIALIZING, transitions=(transition,))

    def advance(self, to_stage: RuntimeStage, *, at: str, reason: str = "") -> "RuntimeLifecycle":
        legal = _LEGAL_TRANSITIONS[self.current_stage]
        if to_stage not in legal:
            raise IllegalLifecycleTransition(
                f"{self.current_stage.value} -> {to_stage.value} is not a legal transition "
                f"(legal targets: {[s.value for s in legal]})"
            )
        transition = LifecycleTransition(from_stage=self.current_stage, to_stage=to_stage, at=at, reason=reason)
        return RuntimeLifecycle(current_stage=to_stage, transitions=self.transitions + (transition,))

    def is_terminal(self) -> bool:
        return self.current_stage in TERMINAL_STAGES

    def to_dict(self) -> dict:
        return {
            "current_stage": self.current_stage.value,
            "transitions": [t.to_dict() for t in self.transitions],
        }
