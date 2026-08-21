"""Market Regime Memory engine -- pure functions, no state, no IO.
Phase 11 Upgrade 1.

`evaluate(state)` is a pure function of an already-advanced
RegimeMemoryState -- it never mutates state, never reads a clock, never
touches a broker. Stability and transition-probability-context are both
disclosed, sample-size-aware reads of what this session has ACTUALLY
observed -- never a forecast, never fabricated on zero evidence.
"""
from __future__ import annotations

from typing import Dict

from .models import (
    CONFIDENCE_HIGH, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_NONE,
    STABILITY_FRAGILE, STABILITY_INSUFFICIENT_HISTORY, STABILITY_STABLE, STABILITY_WEAKENING,
    RegimeMemorySnapshot, RegimeMemoryState,
)

FRAGILE_DURATION_THRESHOLD = 3


def _stability(state: RegimeMemoryState) -> str:
    if state.total_transitions == 0:
        return STABILITY_INSUFFICIENT_HISTORY
    if state.duration_cycles < FRAGILE_DURATION_THRESHOLD:
        return STABILITY_FRAGILE
    if not state.recent_episode_durations:
        return STABILITY_INSUFFICIENT_HISTORY
    avg_recent = sum(state.recent_episode_durations) / len(state.recent_episode_durations)
    return STABILITY_STABLE if state.duration_cycles >= avg_recent else STABILITY_WEAKENING


def _transition_probability_context(state: RegimeMemoryState) -> Dict[str, float]:
    """Empirical distribution of what `previous_regime` has actually
    transitioned to, historically, THIS SESSION. Empty dict (never a
    guessed/uniform distribution) when there's no history for it yet."""
    if state.previous_regime is None:
        return {}
    outcomes = state.transition_counts.get(state.previous_regime)
    if not outcomes:
        return {}
    total = sum(outcomes.values())
    return {
        f"from_{state.previous_regime}_to_{to_regime}": round(count / total, 4)
        for to_regime, count in sorted(outcomes.items())
    }


def _confidence(state: RegimeMemoryState) -> str:
    """Confidence in the transition_probability_context specifically --
    a sample-size read, not a market-condition read."""
    if state.previous_regime is None:
        return CONFIDENCE_NONE
    outcomes = state.transition_counts.get(state.previous_regime)
    sample_size = sum(outcomes.values()) if outcomes else 0
    if sample_size == 0:
        return CONFIDENCE_NONE
    if sample_size < 3:
        return CONFIDENCE_LOW
    if sample_size < 8:
        return CONFIDENCE_MODERATE
    return CONFIDENCE_HIGH


def evaluate(state: RegimeMemoryState) -> RegimeMemorySnapshot:
    return RegimeMemorySnapshot(
        current_regime=state.current_regime,
        previous_regime=state.previous_regime,
        duration_cycles=state.duration_cycles,
        stability=_stability(state),
        transition_probability_context=_transition_probability_context(state),
        confidence=_confidence(state),
        total_transitions_observed=state.total_transitions,
    )
