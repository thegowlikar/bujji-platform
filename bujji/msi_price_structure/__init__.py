"""MSI Brain 1: Price Structure Intelligence (PSI v1) — BUJJI Engineering Series 78.

Lives at `bujji/msi_price_structure/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
`bujji.strategy_selector`, and `fyers_apiv3` — same isolation discipline
as Series 73A/73B/73C/74/75/76/77. This is the first genuine REASONING
brain in the project: it consumes `bujji.market_episode.models.Episode`
objects (Series 76) plus the underlying `bujji.live_market_events.models.
MarketEvent` objects they reference, and produces `PriceStructureAssessment`
— a first-principles interpretation of price structure (Trend, Swing,
Compression, Expansion, Balance), per `docs/MSI_V1_FOUNDATION.md`.

Purely descriptive. No strategy, strike, direction-prediction, or
probability-of-profit vocabulary anywhere in this package.
"""
