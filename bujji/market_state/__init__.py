"""Market State -- Shadow Campaign v2 Phase 3C.

Final intelligence aggregation layer: combines
intelligence.runner.run_intelligence()'s dict output with
market_state_builder.MarketStateAssessment into one immutable
MarketState. Answers only "what is happening in the market" -- no
strategy, trade, entry, exit, position, order, or risk field exists
anywhere in this package, and no scoring/prediction logic
(bullish_score, buy_probability, etc.) is implemented here.

NOT wired into ShadowSessionRunner yet -- standalone only, per this
phase's own instruction.
"""
