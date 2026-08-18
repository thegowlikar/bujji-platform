"""Phase 20.24 -- the composition adapter. Builds the REAL, already-
existing `bujji.strategy_intelligence.MarketContext` that `score_
strategy()` already knows how to consume (Phase 20.5's own
`_apply_mic_context`, dormant in the live pipeline until this phase's
own runtime wiring) -- and bundles it with the optional, richer Phase
20.23 `MarketUnderstandingContext` for explainability only.

`strategy_regime` MUST be the already-mapped strategy-environment
vocabulary (e.g. `TREND_UP`/`RANGE`/`TRANSITION`, `bujji.
live_shadow_runner.runner`'s own `_MIC_REGIME_TO_STRATEGY_REGIME`
table) -- the SAME vocabulary `favorable_regimes`/`unfavorable_regimes`
are already expressed in, and the SAME value already passed to
`MarketEnvironment.mic_regime` at the opportunity-qualification stage.
This module does not recompute or duplicate that mapping table --
the caller (the real runtime entrypoint) already has the mapped value
in scope and supplies it directly.
"""
from __future__ import annotations

from typing import Optional, Tuple

from bujji.mic_context_bridge import MarketUnderstandingContext
from bujji.strategy_intelligence import MarketContext

from .models import RuntimeIntelligenceContext


def build_mic_runtime_context(
    strategy_regime: str,
    favorable_regimes: Tuple[str, ...],
    unfavorable_regimes: Tuple[str, ...],
    *,
    market_understanding: Optional[MarketUnderstandingContext] = None,
) -> RuntimeIntelligenceContext:
    mic_market_context = MarketContext(
        mic_regime=strategy_regime, favorable_regimes=favorable_regimes, unfavorable_regimes=unfavorable_regimes,
    )

    regime_consistency_note = None
    if market_understanding is not None and market_understanding.market_state != strategy_regime:
        regime_consistency_note = (
            f"MarketUnderstandingContext.market_state={market_understanding.market_state!r} differs from "
            f"the mapped MIC regime {strategy_regime!r} used for confidence adjustment -- explainability "
            f"context only, never reconciled automatically, never overrides the confidence-adjustment regime."
        )

    explanation_parts = [f"MIC regime for confidence adjustment: {strategy_regime!r}."]
    if market_understanding is not None:
        explanation_parts.append(f"Market understanding: {market_understanding.explanation}")
    else:
        explanation_parts.append("No richer market understanding context supplied this cycle.")
    if regime_consistency_note is not None:
        explanation_parts.append(regime_consistency_note)

    return RuntimeIntelligenceContext(
        mic_market_context=mic_market_context, market_understanding=market_understanding,
        regime_consistency_note=regime_consistency_note, explanation=" ".join(explanation_parts),
    )
