"""Market Thesis models — frozen, immutable composed record.

Answers "what is the market telling us, and which strategy families
does that support?" It does NOT choose a trade, does NOT build an
order, and does NOT replace strategy_selector or
msi_strategy_selection_foundation -- `preferred_strategy_families`
here is a direct reshaping of SSF's own real per-family verdicts,
never a new suitability computation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class MarketThesisAssessment:
    assessment_id: str
    timestamp: str

    market_regime: str                 # msi_trade_thesis.taxonomy.ALL_THESIS_TYPES pass-through, or UNKNOWN.
    directional_bias: str              # Real MDI overall_direction pass-through (via msi_trade_thesis).
    volatility_environment: str        # Real msi_trade_thesis volatility_expectation pass-through.
    premium_environment: str           # NEW: taxonomy.ALL_PREMIUM_ENVIRONMENTS -- see taxonomy.py.
    expected_move_environment: str     # Real VSB expected_move_state pass-through.
    positioning_environment: str       # Real MPPI positioning_bias pass-through, exposed as its own field
                                        # (msi_trade_thesis consumes MPPI but folds it into supporting/
                                        # conflicting only -- never exposes it directly).
    liquidity_environment: str         # Real LiquidityReading.tightness pass-through, exposed as its own
                                        # field (SSF has liquidity evidence per-family only; msi_trade_thesis
                                        # does not consume liquidity at all).

    preferred_strategy_families: Tuple[str, ...]        # SSF families where suitability == SUITABLE.
    rejected_strategy_families: Tuple[str, ...]         # SSF families where suitability == UNSUITABLE.
    insufficient_evidence_families: Tuple[str, ...]     # SSF families where suitability == INSUFFICIENT_EVIDENCE.
                                                          # Deliberately a THIRD, separate bucket -- never folded
                                                          # into rejected, since "we don't know" and "we know it
                                                          # doesn't fit" are different facts.

    confidence: str                    # Real msi_trade_thesis conviction pass-through (NONE/LOW/MODERATE/HIGH).
    reasons: Tuple[str, ...]
    supporting_assessment_ids: Tuple[str, ...]
    provenance: str
    schema_version: str
