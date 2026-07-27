"""MSI Brain 2: Market Structure Intelligence (MSSI v1) — BUJJI
Engineering Series 79.

Lives at `bujji/msi_market_structure/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` — same isolation
discipline as Series 73A/73B/73C/74/75/76/77/78. Consumes the same
`bujji.market_episode.models.Episode` (Series 76) /
`bujji.live_market_events.models.MarketEvent` (Series 75) objects
Series 78 consumes, and produces `MarketStructureAssessment` — a
first-principles interpretation of WHERE price is located relative to
structure (support, resistance, breakout, breakdown, retest, rejection,
structural balance), per `docs/MSI_V1_FOUNDATION.md` Deliverable 2
domain 2 (Support & Resistance Intelligence) and Deliverable 1's
Acceptance/Rejection/Auction concepts.

Orthogonal to Series 78 (`bujji.msi_price_structure`), which answers
HOW price is behaving (trend/swing/compression/expansion) — see
`taxonomy.py`'s module docstring for the disclosed Step 0.6
overlap-check finding.

Purely descriptive. No strategy, strike, direction-prediction, or
probability-of-profit vocabulary anywhere in this package.
"""
