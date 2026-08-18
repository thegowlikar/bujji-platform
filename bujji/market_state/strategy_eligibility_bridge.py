"""Strategy Eligibility Bridge -- Shadow Trading Brain, Phase 6B.

Thin wrapper around the EXISTING, unmodified
msi_strategy_eligibility.engine.determine_eligibility() -- confirmed by
Phase 6A's investigation to already accept the real
MarketOpportunityAssessment/ConsensusAssessment objects directly (both
already live via Phase 5B), with no adapter required.

StrategyEligibilityAssessment is a COMPATIBILITY FILTER, never a
selector: eligible_strategy_families is a SET (often several families
at once), never narrowed to one "winning" family here or in the
wrapped engine. This bridge adds no interpretation beyond None-safety
at the call site -- determine_eligibility()'s own opportunity/consensus
parameters are required, non-Optional types with no internal null
guard (unlike derive_trade_thesis's vsb/consensus), so this bridge
supplies that safety rather than letting a None reach the engine.
"""
from __future__ import annotations

from typing import Optional

from bujji.msi_consensus.models import ConsensusAssessment
from bujji.msi_decision_synthesis.models import MarketOpportunityAssessment
from bujji.msi_strategy_eligibility.engine import determine_eligibility
from bujji.msi_strategy_eligibility.models import StrategyEligibilityAssessment


def build_strategy_eligibility(
    opportunity: Optional[MarketOpportunityAssessment],
    consensus: Optional[ConsensusAssessment],
    timestamp: str,
) -> Optional[StrategyEligibilityAssessment]:
    """None (never fabricated) when either input is honestly absent --
    determine_eligibility() itself requires both as real objects."""
    if opportunity is None or consensus is None:
        return None
    return determine_eligibility(opportunity, consensus, timestamp=timestamp)
