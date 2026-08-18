"""Market Regime Memory -- Phase 11 Upgrade 1. Pure state/models, no IO,
no wall-clock reads, no strategy/decision field.

Tracks HOW Bujji arrived at its current regime reading -- duration,
transitions, and the empirical (never fabricated) transition context of
regimes it has actually observed this session. This is memory, not
prediction: nothing here forecasts what the market will do next, it
only discloses what has already, verifiably, happened this session.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Dict, Optional, Tuple

STABILITY_INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
STABILITY_FRAGILE = "FRAGILE"
STABILITY_WEAKENING = "WEAKENING"
STABILITY_STABLE = "STABLE"

ALL_STABILITY_STATES = (
    STABILITY_INSUFFICIENT_HISTORY, STABILITY_FRAGILE, STABILITY_WEAKENING, STABILITY_STABLE,
)

CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"


@dataclass(frozen=True)
class RegimeMemorySnapshot:
    """One cycle's regime-memory read -- what this module actually
    exposes downstream. Never a strategy/action field."""

    current_regime: Optional[str]
    previous_regime: Optional[str]
    duration_cycles: int
    stability: str
    transition_probability_context: Dict[str, float]
    confidence: str
    total_transitions_observed: int

    def to_dict(self) -> dict:
        return {
            "current_regime": self.current_regime,
            "previous_regime": self.previous_regime,
            "duration_cycles": self.duration_cycles,
            "stability": self.stability,
            "transition_probability_context": dict(self.transition_probability_context),
            "confidence": self.confidence,
            "total_transitions_observed": self.total_transitions_observed,
        }


@dataclass(frozen=True)
class RegimeMemoryState:
    """Threaded cross-cycle state -- mirrors ObservationMemory's own
    frozen, replace()-based growth pattern (market_state_builder.market_state).
    `transition_counts[from_regime][to_regime]` accumulates for the
    whole session; never reset except by constructing a fresh instance
    (a new session)."""

    current_regime: Optional[str] = None
    previous_regime: Optional[str] = None
    duration_cycles: int = 0
    total_transitions: int = 0
    transition_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    recent_episode_durations: Tuple[int, ...] = ()

    def advance(self, new_regime: Optional[str]) -> "RegimeMemoryState":
        """Returns a NEW state. `new_regime=None` (this cycle produced
        no real regime reading at all) leaves state entirely unchanged
        -- a transient data gap must never look like a transition."""
        if new_regime is None:
            return self

        if self.current_regime is None:
            return replace(self, current_regime=new_regime, previous_regime=None, duration_cycles=1)

        if new_regime == self.current_regime:
            return replace(self, duration_cycles=self.duration_cycles + 1)

        new_counts = {k: dict(v) for k, v in self.transition_counts.items()}
        new_counts.setdefault(self.current_regime, {})
        new_counts[self.current_regime][new_regime] = new_counts[self.current_regime].get(new_regime, 0) + 1

        new_recent_durations = (self.recent_episode_durations + (self.duration_cycles,))[-5:]

        return replace(
            self, previous_regime=self.current_regime, current_regime=new_regime, duration_cycles=1,
            total_transitions=self.total_transitions + 1, transition_counts=new_counts,
            recent_episode_durations=new_recent_durations,
        )
