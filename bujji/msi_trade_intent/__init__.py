"""Trade Intent Intelligence (TII) — BUJJI Engineering Series 83.

Lives at `bujji/msi_trade_intent/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` -- same isolation
discipline as every prior series in this arc. Like Series 82, TII is a
deliberate, one-directional, DOWNSTREAM consumer of two specific real
model types: `bujji.msi_strategy_eligibility.models.
StrategyEligibilityAssessment` (Series 82) and
`bujji.msi_decision_synthesis.models.MarketOpportunityAssessment`
(Series 77) -- see `engine.py`'s module docstring for the full
"Architecture boundary" reasoning, `taxonomy.py`'s module docstring
for Check 1's full "Strategy Selection does not exist" finding and its
disclosed placeholder-selection resolution, and
`docs/MSI_TRADE_INTENT.md` for the complete write-up. TII never
imports `bujji.msi_price_structure`, `bujji.msi_market_structure`, or
`bujji.msi_consensus` directly.

TII converts ONE placeholder-selected eligible strategy family (82's
output, narrowed to one by a disclosed, non-scoring tie-break -- see
`engine._placeholder_select_one_eligible_family`) into a full
`TradeIntentAssessment` describing exposure/bias/risk-profile/
invalidation -- it never selects strikes, expiry, size, or issues an
order, and it performs no scoring, optimization, or P&L prediction of
any kind.

Trade Intent describes what a trade should EXPRESS. It never
constructs or executes the trade.

Note: there is no real Series 80 (Volatility Structure) brain yet, and
no real "Strategy Selection" stage/`StrategySelectionAssessment` type
either (see Check 1). Every end-to-end demonstration involving
"volatility" or "strategy selection" uses a disclosed mock/placeholder,
exactly as Series 81/82 established. See `docs/MSI_TRADE_INTENT.md`
"Known limitations".
"""
from __future__ import annotations
