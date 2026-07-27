"""Strategy Eligibility Intelligence (SEI) — BUJJI Engineering Series 82.

Lives at `bujji/msi_strategy_eligibility/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` -- same isolation
discipline as every prior series in this arc (73A/73B/73C/74/75/76/
77/78/79/81). UNLIKE those packages' mutual peer isolation, SEI is a
deliberate, one-directional, DOWNSTREAM consumer of two specific real
model types: `bujji.msi_decision_synthesis.models.
MarketOpportunityAssessment` (Series 77) and `bujji.msi_consensus.
models.ConsensusAssessment` (Series 81) -- see `engine.py`'s module
docstring for the full "Architecture boundary" reasoning, and
`docs/MSI_STRATEGY_ELIGIBILITY.md` for the complete Check 2/Check 3
resolutions. SEI never imports `bujji.msi_price_structure` or
`bujji.msi_market_structure` directly.

SEI determines WHICH strategy families are permissible given a
synthesized opportunity read (77) AND the coherence of the multi-
domain understanding backing it (81) -- it never selects a concrete
strategy, strike, expiry, or size, and it performs no scoring,
optimization, or P&L prediction of any kind.

Strategy Eligibility defines the permissible solution space. It never
selects a trade.

Note: there is no real Series 80 (Volatility Structure) brain yet --
only the unrelated legacy `bujji.intelligence.volatility_brain` module
exists. Every end-to-end demonstration involving "volatility" uses a
disclosed mock domain view, exactly as Series 81 established. See
`docs/MSI_STRATEGY_ELIGIBILITY.md` "Known limitations".
"""
from __future__ import annotations
