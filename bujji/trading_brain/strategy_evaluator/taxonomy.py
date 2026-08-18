"""Strategy Candidate Evaluator vocabulary — BUJJI Options OS v3,
Trading Brain Intelligence Upgrade, Phase 1.

Ranks the strategies `strategy_selector.engine.select()` already found
ELIGIBLE -- this module never re-gates and never overrides that
verdict. A strategy `strategy_selector` rejected never reaches here;
it is carried through unscored, with its own original rejection
reason, for full transparency (see models.RankedCandidates.
rejected_ineligible).

Plain string constants (house convention, matches every other
taxonomy in this codebase). Every score is a named, disclosed
category with a stated reason grounded in a real, already-existing
evidence object -- never a numeric weight, a probability, or a
machine-learned value. When the evidence a dimension needs was not
supplied by the caller, that dimension scores UNKNOWN rather than
guessing -- the same fail-closed discipline used throughout MSI.
"""
from __future__ import annotations

STRATEGY_EVALUATOR_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Per-dimension score level -- one closed vocabulary shared by every
# dimension, so a caller never has to learn a second scale.
# ---------------------------------------------------------------------------
SCORE_HIGH = "HIGH"
SCORE_MEDIUM = "MEDIUM"
SCORE_LOW = "LOW"
SCORE_UNKNOWN = "UNKNOWN"

ALL_SCORE_LEVELS = (SCORE_HIGH, SCORE_MEDIUM, SCORE_LOW, SCORE_UNKNOWN)

_SCORE_RANK = {SCORE_UNKNOWN: 0, SCORE_LOW: 1, SCORE_MEDIUM: 2, SCORE_HIGH: 3}


def score_rank(level: str) -> int:
    return _SCORE_RANK[level]


# ---------------------------------------------------------------------------
# Dimensions scored -- fixed set, Phase 1 scope. Each maps to exactly
# one real, already-existing evidence source (see engine.py for the
# traced justification of each mapping):
#   STRUCTURE_FIT     <- MSSI location/structural_balance + PSI compression
#   VOLATILITY_FIT     <- VSB iv_state/expansion_state
#   RISK_EFFICIENCY     <- registry.StrategyDefinition.risk_profile, a
#                          real, already-declared field, chosen over a
#                          fresh id->MSI-family mapping because no such
#                          mapping has been verified for the Trading
#                          Brain v3 strategy_id namespace (see the
#                          Phase 1 audit note in engine.py) -- risk_profile
#                          is exactly what msi_entry_bridge's own
#                          reviewed formula-coverage table tracks
#                          (DEFINED_RISK families have a bounded max-loss
#                          formula; UNDEFINED_RISK families are
#                          permanently vetoed there), so this proxies
#                          real formula coverage without inventing a
#                          cross-lineage mapping this session never
#                          traced.
#   LIQUIDITY_FIT       <- LiquidityReading.tightness
# Capital/margin efficiency is deliberately NOT a Phase 1 dimension:
# Gate C's margin certification has never produced a real result (see
# project memory), so no honest score exists for it yet -- adding one
# would fabricate precision this codebase does not have.
# ---------------------------------------------------------------------------
DIMENSION_STRUCTURE_FIT = "STRUCTURE_FIT"
DIMENSION_VOLATILITY_FIT = "VOLATILITY_FIT"
DIMENSION_RISK_EFFICIENCY = "RISK_EFFICIENCY"
DIMENSION_LIQUIDITY_FIT = "LIQUIDITY_FIT"

ALL_DIMENSIONS = (
    DIMENSION_STRUCTURE_FIT, DIMENSION_VOLATILITY_FIT,
    DIMENSION_RISK_EFFICIENCY, DIMENSION_LIQUIDITY_FIT,
)

DIMENSION_DESCRIPTIONS = {
    DIMENSION_STRUCTURE_FIT: "Does today's real market structure (MSSI location, PSI compression) fit this strategy's directional_bias?",
    DIMENSION_VOLATILITY_FIT: "Does today's real volatility state (VSB iv_state/expansion_state) favor this strategy's income_or_debit posture?",
    DIMENSION_RISK_EFFICIENCY: "Is this strategy's risk shape one the numeric risk stack can honestly bound today (registry.risk_profile)?",
    DIMENSION_LIQUIDITY_FIT: "Is the real chain liquidity (LiquidityReading.tightness) tight enough for this strategy to be efficiently entered/exited?",
}

# ---------------------------------------------------------------------------
# Ranking status -- overall verdict, mirrors strategy_selector.taxonomy's
# own SELECTION_STATUS_* three-way shape deliberately.
# ---------------------------------------------------------------------------
RANKING_STATUS_RANKED = "RANKED"
RANKING_STATUS_NO_ELIGIBLE_CANDIDATES = "NO_ELIGIBLE_CANDIDATES"
RANKING_STATUS_UNKNOWN = "UNKNOWN"

ALL_RANKING_STATUSES = (
    RANKING_STATUS_RANKED, RANKING_STATUS_NO_ELIGIBLE_CANDIDATES, RANKING_STATUS_UNKNOWN,
)

RANKING_STATUS_DESCRIPTIONS = {
    RANKING_STATUS_RANKED: "At least one eligible candidate was scored and ranked.",
    RANKING_STATUS_NO_ELIGIBLE_CANDIDATES: "strategy_selector found zero eligible strategies; there is nothing to rank.",
    RANKING_STATUS_UNKNOWN: "No StrategyDecision was supplied at all; nothing could be evaluated.",
}
