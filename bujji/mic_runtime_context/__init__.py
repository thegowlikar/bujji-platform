"""Phase 20.24 -- Market Understanding Runtime Integration.

Wires the completed `MarketUnderstandingContext` (Phase 20.23) and
MIC's own regime output into Bujji's runtime decision flow, by
activating a REAL, already-existing, already-tested integration point
that has been dormant for the entire engagement.

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_24_MARKET_UNDERSTANDING_RUNTIME_INTEGRATION_REPORT.md
for the full audit):

- `bujji.strategy_intelligence.MarketContext` + `scoring.
  _apply_mic_context()` (Phase 20.5) -- A) reusable directly, and the
  ENTIRE reason this phase does not need a new confidence-adjustment
  mechanism. `score_strategy(evidence, context: Optional[MarketContext])`
  already implements exactly the "MIC regime demotes confidence by one
  band when unfavorable or never-validated, never promotes" rule --
  fully tested since Phase 20.5. CRITICAL FINDING: confirmed by
  direct inspection of every real call site
  (`bujji/live_shadow_runner/runner.py:88`), `score_strategy()` is
  ALWAYS called as `score_strategy(evidence)` -- `context` is NEVER
  supplied in the live pipeline. This dormant, already-built
  integration point is the actual target of this phase.
- `bujji.mic_context_bridge.MarketUnderstandingContext` (Phase 20.23)
  -- A) reusable directly, this phase's other real input. Never
  recomputed, never modified.
- `bujji.decision_context.DecisionContext` (Phase 19.4, MSI lineage),
  `bujji.intelligence.context.IntelligenceContext` (Phase 19.2.2,
  MSI lineage), `bujji.memory_context.MemoryDecisionContext` (Phase
  20.22) -- C) wrong domain / already-disclosed-distinct-name
  precedent. None are extended or renamed; `RuntimeIntelligenceContext`
  is a new, distinctly-named class avoiding all three.
- `bujji.mic_v0` (core) -- read, NOT modified. `MarketState`'s own
  schema (`market_regime`/`volatility_state`/`risk_state`) is
  unchanged; this phase never edits `bujji/mic_v0/*`.
- `bujji.live_shadow_runner.runner.process_cycle()` -- the ONE
  runtime file this phase edits (NOT "MIC core" -- the orchestration
  entrypoint, already extended by Phase 20.14's own precedent). The
  edit is minimal and additive: build the real `MarketContext` from
  data already in scope in the existing per-strategy loop
  (`market_state`'s mapped regime, and the `favorable`/`unfavorable`
  tuples already unpacked from `strategies`) and pass it to
  `score_strategy(evidence, context=...)` -- one line changed, no
  function signature change, no new parameter, 100% behavior-
  preserving for every existing caller/test EXCEPT that confidence can
  now genuinely be demoted by MIC regime context, exactly as Phase
  20.5 always intended.

This package NEVER modifies `evidence_score`, NEVER promotes
confidence, NEVER creates or ranks an opportunity, NEVER calls the
Risk Governor or Execution Intelligence, and NEVER imports a broker
module. The richer `MarketUnderstandingContext` factors
(volatility/premium/liquidity/structure/greeks) are carried through
for EXPLAINABILITY ONLY -- no strategy-scoring consumer for them
exists anywhere in the codebase, and this phase does not invent one.

--------------------------------------------------------------------
PHASE 20.25 ADDITION -- Full Market Intelligence Runtime Wiring
--------------------------------------------------------------------

STEP 1 AUDIT SUMMARY (see docs/PHASE_20_25_FULL_MARKET_INTELLIGENCE_RUNTIME_WIRING_REPORT.md):

Read every real brain's `analyze()` signature directly (not assumed).
Of the 6 brains, only `RegimeBrain` was already safely callable from
`live_shadow_runner`'s existing data (spot candles, VIX) -- already
wired in Phase 20.24. The other 5 require real option-chain/quote
data `live_shadow_runner` does not fetch today:

- `VolatilityBrain.analyze()` requires real ATM CE/PE premiums + strike + t_years.
- `LiquidityBrain.analyze()` requires real top-of-book CE/PE bid/ask.
- `StructureBrain.analyze()` requires real per-strike CE/PE open interest.
- `GreeksBrain.analyze()` requires solved IVs (from VolatilityBrain) + strike/t_years.
- `PremiumBrain.analyze()` requires a real POSITION entry point --
  structurally unavailable in Cycle 1's shadow runtime (no positions
  exist, by design boundary) -- excluded from this wiring entirely,
  not merely "not yet fetched."

`brain_assembly.assemble_market_understanding_from_option_data()`
calls the 4 real, unmodified, currently-unused brains
(`VolatilityBrain`/`LiquidityBrain`/`StructureBrain`/`GreeksBrain`)
directly -- zero fill/IV/Greeks/liquidity math reimplemented. `regime`
and `premium` are deliberately left `None` in the composed
`MarketUnderstandingContext` this function returns -- see its own
docstring for why neither can be honestly populated here.

`bujji.live_shadow_runner.runner.process_cycle()` gained ONE new
OPTIONAL parameter, `option_market_data: Optional[OptionMarketDataForCycle]
= None`, defaulting to `None` -- 100% behavior-preserving for every
existing caller (confirmed by re-running the full pre-existing
`live_shadow_runner` test suite unmodified). Actually FETCHING real
option-chain/quote data from the broker in the live entrypoint remains
a disclosed, separate future task -- not fabricated here.
"""
from .adapter import build_mic_runtime_context
from .brain_assembly import assemble_market_understanding_from_option_data
from .explain import explain_runtime_intelligence_context
from .models import OptionMarketDataForCycle, RuntimeIntelligenceContext

__all__ = [
    "build_mic_runtime_context", "explain_runtime_intelligence_context", "RuntimeIntelligenceContext",
    "assemble_market_understanding_from_option_data", "OptionMarketDataForCycle",
]
