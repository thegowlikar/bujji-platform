"""bujji.mic_v0.volatility_classifier — Phase 20.1.

Classifies `volatility_state` (LOW/NORMAL/HIGH) from real India VIX
history via trailing-window percentile — NOT the same thing as
`bujji.intelligence.volatility_brain.Richness` (IV_RICH/IV_CHEAP/
IV_FAIR), which is an OPTIONS-implied-vs-realized measure requiring a
real option premium to solve IV from. Cycle 1 has no historical
options data (Phase 20.0 finding) so `volatility_brain` cannot be
reused here — this is a genuinely different, new, index-level measure,
built because the existing one is the wrong tool for this data domain,
not because the existing one was overlooked.

VIX has 8+ years of real daily history in `HistoricalObservationStore`
(confirmed, Phase 20.0) — a trailing-window percentile is honestly
computable and independently checkable, unlike anything options-based
in this system today.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from .models import VOLATILITY_HIGH, VOLATILITY_LOW, VOLATILITY_NORMAL

MIN_WINDOW = 60  # ~3 trading months of daily VIX closes -- below this, refuse to guess.

# Tercile split -- a documented first pass, not a statistically
# calibrated conclusion (same disclosed-default discipline as every
# `*_brain.py` threshold in this codebase). Revisit once Phase 20.1B's
# validation results exist.
LOW_PERCENTILE = 1.0 / 3.0
HIGH_PERCENTILE = 2.0 / 3.0


def classify_volatility_state(
    current_vix: float, trailing_vix_history: List[float],
) -> Tuple[Optional[str], List[str]]:
    """`trailing_vix_history`: real historical VIX closes STRICTLY
    BEFORE the current observation, oldest first, caller-supplied
    (never fetched here — this module has no store access, matching
    every other brain in this codebase). Returns (state_or_None,
    evidence_lines). None when there isn't enough history to place a
    percentile honestly -- never a guessed classification."""
    n = len(trailing_vix_history)
    if n < MIN_WINDOW:
        return None, [f"insufficient_history: {n} sample(s), need >= {MIN_WINDOW}"]

    sorted_history = sorted(trailing_vix_history)
    rank = sum(1 for v in sorted_history if v <= current_vix)
    percentile = rank / n

    evidence = [
        f"current_vix={current_vix:.2f}",
        f"trailing_window_n={n}",
        f"percentile={percentile:.3f}",
    ]

    if percentile <= LOW_PERCENTILE:
        evidence.append(f"percentile <= {LOW_PERCENTILE:.3f} -> LOW")
        return VOLATILITY_LOW, evidence
    if percentile >= HIGH_PERCENTILE:
        evidence.append(f"percentile >= {HIGH_PERCENTILE:.3f} -> HIGH")
        return VOLATILITY_HIGH, evidence
    evidence.append(f"percentile between {LOW_PERCENTILE:.3f} and {HIGH_PERCENTILE:.3f} -> NORMAL")
    return VOLATILITY_NORMAL, evidence
