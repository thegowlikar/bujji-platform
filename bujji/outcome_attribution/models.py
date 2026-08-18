"""Outcome Attribution -- Phase 15J. Pure models, no IO, no broker, no
execution. Post-trade ANALYTICAL intelligence only -- explains an
outcome, never influences one.

FORENSIC FINDING (Step 1) driving this design: `PositionLifecycle.
realized_pnl` (Phase 15G) is defaulted `None` and NO code path in this
codebase ever sets it to anything else -- confirmed by direct source
inspection. `PositionClosed`'s own event payload (Phase 15G) captures
only `closed_at`/`exit_reason`, never an exit price or premium. This
means the $ OUTCOME of a position (profit/loss/magnitude) is currently
`MISSING` data, not merely sparse -- there is no PaperBroker linkage
(Phase 15B's own disclosed limitation, still true) to derive it from.

This is handled honestly, not worked around: `outcome_direction`
degrades to `UNKNOWN` whenever `realized_pnl` is `None` (i.e. always,
today) -- NEVER inferred from thesis/management narrative alone. Per
Step 5's explicit instruction, causal ATTRIBUTION (what happened, and
why) is deliberately kept independent of $ CLASSIFICATION (profit vs
loss) -- this engine can and does produce a real, evidence-grounded
causal narrative even while `outcome_direction` itself stays `UNKNOWN`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

SCHEMA_VERSION = "1.0.0"

# --- Attribution taxonomy (Step 2) -- the smallest useful set; no
# dimension exists here without a real, already-available evidence
# source feeding it (see engine.py's per-dimension extraction). ------
DIM_SELECTION = "SELECTION"
DIM_ENTRY_TIMING = "ENTRY_TIMING"
DIM_REGIME = "REGIME"
DIM_DIRECTION = "DIRECTION"
DIM_VOLATILITY = "VOLATILITY"
DIM_PREMIUM_BEHAVIOUR = "PREMIUM_BEHAVIOUR"
DIM_GREEKS_EXPOSURE = "GREEKS_EXPOSURE"
DIM_LIQUIDITY = "LIQUIDITY"
DIM_MANAGEMENT = "MANAGEMENT"
DIM_EXIT_TIMING = "EXIT_TIMING"
DIM_STRUCTURAL_GEOMETRY = "STRUCTURAL_GEOMETRY"
DIM_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ALL_DIMENSIONS = (
    DIM_SELECTION, DIM_ENTRY_TIMING, DIM_REGIME, DIM_DIRECTION, DIM_VOLATILITY,
    DIM_PREMIUM_BEHAVIOUR, DIM_GREEKS_EXPOSURE, DIM_LIQUIDITY, DIM_MANAGEMENT,
    DIM_EXIT_TIMING, DIM_STRUCTURAL_GEOMETRY, DIM_INSUFFICIENT_EVIDENCE,
)

# --- Causal role -------------------------------------------------------------
ROLE_PRIMARY_CAUSE = "PRIMARY_CAUSE"
ROLE_CONTRIBUTING_FACTOR = "CONTRIBUTING_FACTOR"
ROLE_PROTECTIVE_FACTOR = "PROTECTIVE_FACTOR"
ROLE_UNKNOWN = "UNKNOWN"
ALL_ROLES = (ROLE_PRIMARY_CAUSE, ROLE_CONTRIBUTING_FACTOR, ROLE_PROTECTIVE_FACTOR, ROLE_UNKNOWN)

# --- Evidence strength / impact direction ------------------------------------
STRENGTH_STRONG = "STRONG"
STRENGTH_MODERATE = "MODERATE"
STRENGTH_WEAK = "WEAK"
STRENGTH_UNKNOWN = "UNKNOWN"

IMPACT_POSITIVE = "POSITIVE"    # evidence that the position's thesis/management held up well on this dimension.
IMPACT_NEGATIVE = "NEGATIVE"    # evidence that this dimension worked against the position.
IMPACT_NEUTRAL = "NEUTRAL"      # evidence resolved, but genuinely showed no material effect either way.
IMPACT_UNKNOWN = "UNKNOWN"      # NEVER used as a synonym for NEUTRAL -- always means "we don't know," not "no effect."

# --- Outcome direction (Step 5 -- kept separate from causal attribution) ----
OUTCOME_PROFIT = "PROFIT"
OUTCOME_LOSS = "LOSS"
OUTCOME_BREAKEVEN = "BREAKEVEN"
OUTCOME_UNKNOWN = "UNKNOWN"

# --- Readiness (Step 6) -------------------------------------------------------
READY = "READY"
NOT_READY = "NOT_READY"


@dataclass(frozen=True)
class AttributionEvidence:
    dimension: str            # one of ALL_DIMENSIONS.
    role: str                 # one of ALL_ROLES.
    source: str                # e.g. "thesis_evaluations[3].checks.direction", "management_assessments[-1].evidence.exposure".
    strength: str               # one of STRENGTH_*.
    observed_value: Optional[str]
    expected_value: Optional[str]
    impact_direction: str        # one of IMPACT_*.
    confidence: str               # NONE/LOW/MODERATE/HIGH -- same vocabulary as Position Intelligence/Management.
    explanation: str

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension, "role": self.role, "source": self.source,
            "strength": self.strength, "observed_value": self.observed_value,
            "expected_value": self.expected_value, "impact_direction": self.impact_direction,
            "confidence": self.confidence, "explanation": self.explanation,
        }


@dataclass(frozen=True)
class PositionOutcomeAttribution:
    position_id: str
    evaluation_timestamp: str
    readiness: str                    # READY / NOT_READY.
    outcome_direction: str             # OUTCOME_* -- OUTCOME_UNKNOWN whenever realized_pnl is None (today: always).
    realized_pnl: Optional[float]       # copied verbatim from PositionLifecycle -- never recomputed here.
    primary_cause: Optional[str]        # one dimension, or None if genuinely undetermined.
    contributing_factors: Tuple[str, ...]
    protective_factors: Tuple[str, ...]
    evidence: Tuple[AttributionEvidence, ...]
    narrative: str
    schema_version: str = SCHEMA_VERSION
    # Phase 5 (Nervous System Integration) additive fields: Maximum
    # Favorable/Adverse Excursion, computed by engine.py's
    # compute_mfe_mae() from an OPTIONAL per-cycle unrealized-P&L
    # history. HONEST DISCLOSURE, confirmed by direct trace of
    # PositionLifecycle.thesis_evaluations/management_assessments (Phase
    # 15F/15I): NEITHER currently carries a per-cycle valuation number
    # anywhere -- this is a genuinely deeper, pre-existing gap than "MFE/
    # MAE were never computed," it is "the underlying per-cycle valuation
    # capture these two fields would need does not exist yet." These
    # fields therefore read None on every real position TODAY, not
    # because this function is broken, but because no real caller can
    # supply a real history yet. They exist now so the moment a per-cycle
    # valuation capture point is added (a distinct, disclosed, NOT-done-
    # here piece of future work), this dataclass does not need touching
    # again.
    mfe: Optional[float] = None
    mae: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id, "evaluation_timestamp": self.evaluation_timestamp,
            "readiness": self.readiness, "outcome_direction": self.outcome_direction,
            "realized_pnl": self.realized_pnl, "primary_cause": self.primary_cause,
            "contributing_factors": list(self.contributing_factors),
            "protective_factors": list(self.protective_factors),
            "evidence": [e.to_dict() for e in self.evidence],
            "narrative": self.narrative, "schema_version": self.schema_version,
            "mfe": self.mfe, "mae": self.mae,
        }
