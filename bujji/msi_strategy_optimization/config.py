"""bujji.msi_strategy_optimization.config — Series 108.

Every constant is structural and disclosed, never tuned against any
return series (same discipline as every other MSI package's config.py).
Where a constant reflects one documented industry convention rather than
a universal rule, that is stated explicitly here and in
docs/STRATEGY_OPTIMIZATION.md's institutional-literature section
(Deliverable 1).
"""
from __future__ import annotations

DEFAULT_PROVENANCE = "bujji.msi_strategy_optimization.engine"
SCHEMA_VERSION = "1.0.0"

# --- Expiry bucketing (Deliverable 5) --------------------------------------
# DTE thresholds bucketing real available chain expiries. Structural,
# matches Series 90's own DTE-window convention; not re-derived.
NEXT_WEEKLY_MIN_DTE = 8
MONTHLY_MIN_DTE = 15
FAR_MONTHLY_MIN_DTE = 35

# --- Rolling intelligence (Deliverable 6) -----------------------------------
# "Tested" strike convention: a short option's delta magnitude crossing
# this threshold is a widely-cited (not universal -- see doc) signal that
# the strike itself, not just the underlying position, now needs
# attention. 0.35-0.40 is commonly cited across professional premium-
# selling literature (see Deliverable 1); 0.35 is the conservative end of
# that documented range, chosen as the structural default here.
TESTED_STRIKE_DELTA_THRESHOLD = 0.35

# "21 DTE" mechanical management point -- a well-known, STYLE-SPECIFIC
# convention (tastytrade's own published research; not universal across
# all professional desks -- see doc). Used here as a structural default
# for roll_expiry's DTE trigger, disclosed as style-specific, not
# presented as a universal law.
ROLL_EXPIRY_DTE_THRESHOLD = 21

# --- Expiry optimiser dominance ranking (Deliverable 5) --------------------
# Real NIFTY chains carry far-dated (multi-year) expiry entries that are
# not practically tradable for premium-harvesting purposes -- confirmed
# on real corpus data (a chain row with DTE > 1000). Ranking candidates
# by a raw theta/gamma ratio is monotonic in DTE (both shrink, but gamma
# shrinks faster), so an unbounded ratio always picks the most distant
# real expiry present, which is not a genuine "dominant" answer for a
# MAXIMIZE_THETA objective. Dominance ranking is therefore restricted to
# real candidates within this practical DTE cap (all candidates are still
# reported; only the DOMINANCE comparison excludes far-dated entries,
# disclosed here rather than silently dropped).
EXPIRY_DOMINANCE_MAX_DTE = 45

# --- Strike optimiser (Deliverable 4) ---------------------------------------
# Skew classification: a >= this many percentage points of solved-IV
# difference between equal-delta CE/PE strikes is reported as skew,
# below it as NEUTRAL. A real, structural rounding threshold -- not a
# trading trigger by itself.
SKEW_IV_DIFFERENCE_THRESHOLD = 0.02
