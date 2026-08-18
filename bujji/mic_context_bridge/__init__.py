"""Phase 20.23 -- Market Understanding Context Bridge.

Connects existing, already-real intelligence organs
(`bujji.intelligence.regime_brain`/`volatility_brain`/`premium_brain`/
`liquidity_brain`/`structure_brain`/`greeks_brain`) into a single
`MarketUnderstandingContext` -- WITHOUT creating new intelligence
engines and WITHOUT modifying `bujji.mic_v0` (core).

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_23_MARKET_UNDERSTANDING_CONTEXT_BRIDGE_REPORT.md
for the full audit):

- `bujji.market_observation` (MOC), `bujji.options_observation`,
  `bujji.premium_behaviour`, `bujji.intelligence.{liquidity_brain,
  volatility_brain,structure_brain,greeks_brain}`, `bujji.market_
  microstructure` -- ALL A) reusable directly. Confirmed real,
  built, several live-data-verified against real FYERS quotes/option
  chain responses (documented directly in each brain's own module
  docstring). None of these are duplicated by this phase.
- `bujji.mic_v0.engine` -- read, NOT modified. Confirmed by direct
  inspection: MIC v0 currently imports only `RegimeBrain` and one
  constant (`event_brain.VIX_ELEVATED_THRESHOLD`) from the entire
  `bujji.intelligence.*` roster -- `liquidity_brain`/`structure_brain`/
  `greeks_brain`/`volatility_brain`/`premium_brain` are real, built,
  and completely unconsumed. THIS is the actual gap Phase 20.23
  closes -- not missing intelligence, missing composition.
- `bujji.market_microstructure` -- A) reusable directly for its real
  `MinuteObservation` OHLC+texture data, but D) missing capability
  for a classification enum: no existing code anywhere classifies
  microstructure into an EXPANDING/CONTRACTING-style state (confirmed
  by direct inspection of `models.py`/`microstructure_aggregator.py`
  -- no such enum exists). This phase does NOT invent one; microstructure
  is honestly left out of `MarketUnderstandingContext`'s composed
  factors rather than fabricating a classification that doesn't exist
  anywhere in the codebase.

This package NEVER creates a trading signal, opportunity, or
confidence value; NEVER modifies `FinalDecision`/`evidence_score`/
ranking/allocation; NEVER calls the Risk Governor or Execution
Intelligence; and NEVER imports a broker module. It answers exactly
one question: "what is happening in the market?" -- using only
already-real, already-computed brain outputs, never recomputing any
of them.
"""
from .adapter import build_market_understanding_context
from .explain import explain_market_understanding_context
from .models import MarketUnderstandingContext

__all__ = [
    "build_market_understanding_context", "explain_market_understanding_context", "MarketUnderstandingContext",
]
