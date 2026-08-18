"""Phase 20.24 -- human-readable rendering. No new logic; reads fields
already computed by `adapter.py`.
"""
from __future__ import annotations

from .models import RuntimeIntelligenceContext


def explain_runtime_intelligence_context(context: RuntimeIntelligenceContext) -> str:
    lines = [
        f"MIC regime (confidence adjustment): {context.mic_market_context.mic_regime}",
        f"Favorable regimes: {context.mic_market_context.favorable_regimes}",
        f"Unfavorable regimes: {context.mic_market_context.unfavorable_regimes}",
    ]
    if context.market_understanding is not None:
        lines.append(f"Market understanding state: {context.market_understanding.market_state}")
        if context.market_understanding.uncertainties:
            lines.append("Uncertainties: " + "; ".join(context.market_understanding.uncertainties))
        if context.market_understanding.conflicts:
            lines.append("Conflicts: " + "; ".join(context.market_understanding.conflicts))
    else:
        lines.append("Market understanding: NOT_AVAILABLE this cycle.")
    if context.regime_consistency_note is not None:
        lines.append(context.regime_consistency_note)
    lines.append(context.explanation)
    return "\n".join(lines)
