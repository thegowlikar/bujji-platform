"""bujji.mic_v0.risk_classifier — Phase 20.1.

Classifies `risk_state` (NORMAL/ELEVATED/EXTREME) from the real,
current India VIX level. Reuses `bujji.intelligence.event_brain`'s own
two threshold CONSTANTS verbatim (not its function -- that function's
`EventReading` is entangled with expiry-proximity/straddle-specific
inputs Cycle 1 does not have) so the LOW/ELEVATED boundary is not
re-derived or guessed a second time.

`event_brain`'s own 3-way VixRegime (LOW/MODERATE/ELEVATED) has no tier
distinguishing "elevated" from "acute crisis" -- charter's risk_state
needs exactly that third tier, so ONE new threshold is added here
(VIX_EXTREME_THRESHOLD), disclosed as a first-pass default in the same
style as every other brain's calibration note in this codebase.

Mapping (disclosed, not implicit):
  VIX <= 13.0                 -> NORMAL   (event_brain's own LOW boundary)
  13.0 < VIX < 20.0           -> NORMAL   (event_brain's own MODERATE band --
                                            "building concern" is not the
                                            same claim as "elevated risk")
  20.0 <= VIX < 30.0          -> ELEVATED (event_brain's own ELEVATED boundary)
  VIX >= 30.0                 -> EXTREME  (new; historically associated with
                                            acute Indian-market stress, e.g.
                                            2020-03 -- NOT statistically
                                            calibrated against BUJJI's own
                                            accumulated history yet)

Event-calendar evidence (RBI/Budget/elections) is explicitly NEVER used
here -- see package docstring and models.EventContext.
"""
from __future__ import annotations

from typing import List, Tuple

from bujji.intelligence.event_brain import VIX_ELEVATED_THRESHOLD

from .models import RISK_ELEVATED, RISK_EXTREME, RISK_NORMAL

VIX_EXTREME_THRESHOLD = 30.0  # disclosed first pass -- see module docstring.


def classify_risk_state(current_vix: float) -> Tuple[str, List[str]]:
    evidence = [f"current_vix={current_vix:.2f}"]

    if current_vix >= VIX_EXTREME_THRESHOLD:
        evidence.append(f"vix >= {VIX_EXTREME_THRESHOLD} -> EXTREME")
        return RISK_EXTREME, evidence

    if current_vix >= VIX_ELEVATED_THRESHOLD:
        evidence.append(f"vix >= {VIX_ELEVATED_THRESHOLD} (event_brain threshold) -> ELEVATED")
        return RISK_ELEVATED, evidence

    evidence.append(
        f"vix < {VIX_ELEVATED_THRESHOLD} (event_brain threshold, covers LOW and MODERATE bands) -> NORMAL"
    )
    return RISK_NORMAL, evidence
