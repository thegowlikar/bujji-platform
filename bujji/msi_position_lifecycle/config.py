"""Position Lifecycle Intelligence config — Series 96.

Deliverable 1 reuse: WATCH-level exposure thresholds below are
deliberately set BELOW Series 91's own hard `MAX_ABS_PORTFOLIO_DELTA`/
`MAX_ABS_PORTFOLIO_VEGA` REJECT thresholds -- reusing the same real
`portfolio_delta_after`/`portfolio_vega_after` figures Series 91 already
computes, just at an earlier, advisory tripwire ("watch this") rather
than a hard admission gate. Never re-derives Series 91's own Greeks
aggregation; only compares against a stricter, disclosed threshold.
"""
from __future__ import annotations

from bujji.msi_portfolio_construction import config as prc_config

# --- Reused directly (Deliverable 1) --------------------------------------
HARD_MAX_ABS_PORTFOLIO_DELTA = prc_config.MAX_ABS_PORTFOLIO_DELTA
HARD_MAX_ABS_PORTFOLIO_VEGA = prc_config.MAX_ABS_PORTFOLIO_VEGA

# --- NEW, advisory watch-level thresholds (structural, never tuned) ------
# Set at 60% of Series 91's own hard reject thresholds -- a disclosed,
# round, structural fraction, not fit to any replay outcome.
WATCH_ABS_PORTFOLIO_DELTA = HARD_MAX_ABS_PORTFOLIO_DELTA * 0.6
WATCH_ABS_PORTFOLIO_VEGA = HARD_MAX_ABS_PORTFOLIO_VEGA * 0.6

# --- DTE policy (reused concept, not the exact constant, from Series 90's
# own DTE window; this one governs "close to avoid pin risk", not
# "which expiry to enter") -------------------------------------------------
NEAR_EXPIRY_DTE_THRESHOLD = 1  # DTE <= this -> profit-harvest / close-before-expiry consideration.

# --- Construction types that PREFER an early, before-expiry close rather
# than holding into the final session (credit-collecting / debit-spread
# shapes that have already captured most of their available theta/edge by
# the final day) -- declarative, standard desk convention. ----------------
EARLY_HARVEST_PREFERRED_CONSTRUCTION_TYPES = (
    "VERTICAL_DEBIT_SPREAD", "VERTICAL_CREDIT_SPREAD", "IRON_CONDOR_SHAPE", "IRON_FLY_SHAPE",
    "BUTTERFLY_SHAPE", "SHORT_STRANGLE", "SHORT_STRADDLE", "COVERED_SHAPE",
)
