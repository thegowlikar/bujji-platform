"""Market Participant Positioning Intelligence vocabulary — BUJJI
Engineering Series 86 (MPPI v1).

Follows this project's established convention (plain string constants,
not enum.Enum).

---------------------------------------------------------------------
Deliverable 1 investigation finding (see docs/MARKET_PARTICIPANT_POSITIONING_INTELLIGENCE.md
for the full writeup) that shapes this taxonomy:
---------------------------------------------------------------------
Open interest is genuinely available per contract (Series 73C,
`OptionObservation.open_interest`/`.change_in_open_interest`), but ONLY
at end-of-day (Bhavcopy) granularity — no intraday OI history exists
anywhere in this codebase. bid/ask are always None from Bhavcopy
(disclosed in Series 73C's own taxonomy). This means:
  - Lens A (Put/Call OI Ratio) and Lens B (OI Concentration) need only
    ONE point-in-time chain snapshot (a cross-sectional read across
    strikes) — genuinely computable today.
  - Lens C (OI Migration) and Lens D (Expansion/Contraction) need TWO
    chain snapshots at different times to compare — computable at
    DAILY resolution only (comparing two Bhavcopy-derived snapshots),
    never intraday. When only one snapshot is supplied, these lenses
    honestly report UNKNOWN rather than fabricate a migration reading.
  - Lens E (Writer Dominance) uses `change_in_open_interest` (a
    single-snapshot field — the Bhavcopy day's own OI delta), so it
    needs only one snapshot, same as Lens A/B.

---------------------------------------------------------------------
Deliverable — why PositioningBias is derived cautiously:
---------------------------------------------------------------------
Raw aggregate OI alone cannot distinguish "a participant WROTE this
option" from "a participant BOUGHT this option" — that distinction
requires concurrent price-OI correlation this module does not compute.
This module reports a POSITIONING lean using the same disclosed,
non-statistically-calibrated convention the project's own legacy
`bujji/intelligence/structure_brain.py` already uses for wall detection
(heavy call OI above spot = resistance-leaning; heavy put OI below
spot = support-leaning) — but, mirroring that module's own explicit
caution ("PCR is reported as evidence only... not enough real history
to calibrate a PCR-based signal honestly"), Lens A and Lens C's
directional read is capped at LOW/MODERATE confidence at most, never
HIGH, since this project has never validated the buyer-vs-writer
interpretation against real outcomes.
"""
from __future__ import annotations

MSI_PARTICIPANT_POSITIONING_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# PositioningBias — the overall/composite read. Mirrors MDI's
# NEUTRAL/MIXED/UNKNOWN three-way distinction exactly (see
# bujji.msi_market_direction.taxonomy's own docstring for the same
# reasoning, restated here for this package's independence):
#   NEUTRAL = lenses genuinely agree there is no directional bias.
#   MIXED   = lenses genuinely disagree.
#   UNKNOWN = insufficient evidence for ANY lens to form an opinion.
# ---------------------------------------------------------------------------
BULLISH_POSITIONING = "BULLISH_POSITIONING"
BEARISH_POSITIONING = "BEARISH_POSITIONING"
NEUTRAL_POSITIONING = "NEUTRAL_POSITIONING"
MIXED_POSITIONING = "MIXED_POSITIONING"
UNKNOWN_POSITIONING = "UNKNOWN_POSITIONING"

ALL_POSITIONING_BIASES = (
    BULLISH_POSITIONING, BEARISH_POSITIONING, NEUTRAL_POSITIONING,
    MIXED_POSITIONING, UNKNOWN_POSITIONING,
)

_BIAS_RANK = {BEARISH_POSITIONING: -1, NEUTRAL_POSITIONING: 0, BULLISH_POSITIONING: 1}


def bias_rank(bias: str) -> int:
    """Signed rank for a per-lens bullish/bearish/neutral bias. Never
    call on MIXED_POSITIONING/UNKNOWN_POSITIONING — not band values."""
    return _BIAS_RANK[bias]


def bias_from_rank(rank: int) -> str:
    if rank > 0:
        return BULLISH_POSITIONING
    if rank < 0:
        return BEARISH_POSITIONING
    return NEUTRAL_POSITIONING


# ---------------------------------------------------------------------------
# PositioningStrength — DISTINCT from confidence: confidence is "how
# sure are we this bias reading is correct"; strength is "how much
# participation/conviction is behind whatever bias exists" (driven
# primarily by Lens D, expansion/contraction, which has no directional
# valence of its own).
# ---------------------------------------------------------------------------
STRENGTH_UNKNOWN = "UNKNOWN"
STRENGTH_WEAK = "WEAK"
STRENGTH_MODERATE = "MODERATE"
STRENGTH_STRONG = "STRONG"

ALL_POSITIONING_STRENGTHS = (STRENGTH_UNKNOWN, STRENGTH_WEAK, STRENGTH_MODERATE, STRENGTH_STRONG)

# ---------------------------------------------------------------------------
# WriterDominance — Deliverable 3, Lens E's own value set (exactly the
# values the spec lists).
# ---------------------------------------------------------------------------
CALL_WRITERS_DOMINANT = "CALL_WRITERS_DOMINANT"
PUT_WRITERS_DOMINANT = "PUT_WRITERS_DOMINANT"
WRITER_BALANCED = "BALANCED"
WRITER_UNKNOWN = "UNKNOWN"

ALL_WRITER_DOMINANCE_STATES = (CALL_WRITERS_DOMINANT, PUT_WRITERS_DOMINANT, WRITER_BALANCED, WRITER_UNKNOWN)

# ---------------------------------------------------------------------------
# LensName — Deliverable 3's five initial lenses.
# ---------------------------------------------------------------------------
LENS_PUT_CALL_OI_RATIO = "PUT_CALL_OI_RATIO"
LENS_OI_CONCENTRATION = "OI_CONCENTRATION"
LENS_OI_MIGRATION = "OI_MIGRATION"
LENS_OI_EXPANSION_CONTRACTION = "OI_EXPANSION_CONTRACTION"
LENS_WRITER_DOMINANCE = "WRITER_DOMINANCE"

ALL_LENS_NAMES = (
    LENS_PUT_CALL_OI_RATIO, LENS_OI_CONCENTRATION, LENS_OI_MIGRATION,
    LENS_OI_EXPANSION_CONTRACTION, LENS_WRITER_DOMINANCE,
)

# ---------------------------------------------------------------------------
# ConfidenceLevel — established NONE/LOW/MODERATE/HIGH convention.
# ---------------------------------------------------------------------------
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"

ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)
_CONFIDENCE_RANK = {level: i for i, level in enumerate(ALL_CONFIDENCE_LEVELS)}


def confidence_rank(level: str) -> int:
    return _CONFIDENCE_RANK[level]


def confidence_at_rank(rank: int) -> str:
    rank = max(0, min(rank, len(ALL_CONFIDENCE_LEVELS) - 1))
    return ALL_CONFIDENCE_LEVELS[rank]
