"""Phase 20.24/20.25 -- pure data contracts. No IO, no broker, no
execution, no confidence/scoring computation logic anywhere in this
module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from bujji.mic_context_bridge import MarketUnderstandingContext
from bujji.strategy_intelligence import MarketContext


@dataclass(frozen=True)
class OptionMarketDataForCycle:
    """Phase 20.25 -- the REQUIRED real inputs the option-chain-based
    brains (`VolatilityBrain`/`LiquidityBrain`/`StructureBrain`/
    `GreeksBrain`) need, per their own real `analyze()` signatures
    (confirmed by direct inspection, not assumed). Every field is a
    plain, caller-supplied real value -- this dataclass performs no
    calculation and fabricates nothing. A caller with no real option-
    chain data for this cycle simply does not construct one; `bujji.
    live_shadow_runner.runner.process_cycle()`'s own new
    `option_market_data` parameter defaults to `None` for exactly
    this reason.

    `PremiumBrain` deliberately has NO field here: its own real
    `analyze()` signature requires a real `entry_time`/
    `entry_combined_premium` -- i.e. a real or hypothetical POSITION's
    own entry point, which does not exist anywhere in Cycle 1's
    shadow runtime (no positions exist, by design boundary). Wiring
    it would require fabricating an entry that was never real."""

    spot: float
    strike: float
    t_years: float
    ce_premium: float
    pe_premium: float
    ce_bid: float
    ce_ask: float
    pe_bid: float
    pe_ask: float
    strikes: Tuple[Tuple[float, float, float], ...]   # (strike, ce_oi, pe_oi) tuples, for StructureBrain.


@dataclass(frozen=True)
class RuntimeIntelligenceContext:
    """The composed "MIC runtime context input" for one cycle.

    `mic_market_context` is the REAL, already-existing
    `bujji.strategy_intelligence.MarketContext` -- the one object
    `score_strategy()` already knows how to consume to adjust
    confidence by exactly one band (`_apply_mic_context`, Phase
    20.5's own established, tested logic). This is the only field
    consumed by scoring; nothing else on this object reaches
    `evidence_score`/`confidence` through any path.

    `market_understanding` is the richer Phase 20.23
    `MarketUnderstandingContext` (volatility/premium/liquidity/
    structure/greeks/uncertainty/conflicts), carried through for
    EXPLAINABILITY ONLY -- honestly `None` when the caller has not
    supplied one this cycle (e.g. the 6 intelligence brains were not
    run against live data for this cycle). No strategy-scoring
    consumer for these richer factors exists anywhere in the codebase
    today; this phase does not invent one."""

    mic_market_context: MarketContext
    market_understanding: Optional[MarketUnderstandingContext]
    regime_consistency_note: Optional[str]
    explanation: str
