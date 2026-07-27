"""Static configuration for Market Participant Positioning Intelligence.
Every threshold is fixed, disclosed configuration — never fit/tuned
against outcomes, mirroring bujji.msi_market_direction.config's exact
posture and bujji/intelligence/structure_brain.py's own disclosed,
non-statistically-calibrated first-pass thresholds."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MSI_PARTICIPANT_POSITIONING_VERSION

DEFAULT_PROVENANCE = "msi_participant_positioning.engine.assess_participant_positioning"

# Minimum number of (strike, option_type) contracts required in a chain
# snapshot before ANY lens will attempt a read (below this, honestly
# UNKNOWN — insufficient evidence, not fabricated).
MIN_CONTRACTS_FOR_ANY_LENS = 2

# Lens A — Put/Call OI Ratio. Disclosed, first-pass, NOT statistically
# calibrated (mirrors structure_brain.py's own explicit caution).
# PCR > this -> lean BULLISH (heavy put-side OI read as put-writing for
# income, a bullish bet); PCR < this -> lean BEARISH (heavy call-side
# OI read as call-writing, a bearish/capped bet). Capped at MODERATE
# confidence at most — see taxonomy.py module docstring.
PCR_BULLISH_MIN_RATIO = 1.15
PCR_BEARISH_MAX_RATIO = 0.85

# Lens B — OI Concentration / wall proximity. Mirrors
# structure_brain.py's NEAR_WALL_THRESHOLD_PCT exactly (same disclosed
# first-pass sizing, "roughly one NIFTY strike-width").
NEAR_WALL_THRESHOLD_PCT = 0.25

# Lens C — OI Migration. Minimum fractional change in the PCR between
# two chain snapshots before migration is considered directionally
# meaningful (below this, NEUTRAL — a real but small move, not noise
# suppression via fabrication).
MIGRATION_MEANINGFUL_PCR_DELTA = 0.05

# Lens D — OI Expansion/Contraction. Minimum fractional change in total
# OI (both sides combined) between two snapshots to call it EXPANDING
# or CONTRACTING rather than STABLE.
EXPANSION_MEANINGFUL_TOTAL_OI_DELTA_RATIO = 0.02

# Lens E — Writer Dominance. Minimum ratio of |change_in_oi| between
# the two sides before one side is called "dominant" rather than
# BALANCED.
WRITER_DOMINANCE_MIN_RATIO = 1.20
