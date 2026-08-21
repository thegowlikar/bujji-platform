"""bujji.msi_market_phenomena -- Market Phenomena Classifier (MPC),
Series 103.

Answers only one question: "what objectively happened in the market?"
No trading evaluation, no strategy recommendation, no optimisation.
Classification is causal (earliest-possible-timestamp only, no future
candles, no hindsight labels) and declarative (every phenomenon rule is
a disclosed, non-tuned boolean condition over real, already-computed
Intelligence-layer fields -- see engine.py's _PHENOMENON_RULES).

Only 10 of the mission's 21 example phenomena are classifiable in v1.0
without fabrication; the remainder are honestly disclosed as
NOT_CLASSIFIABLE_V1 on every real report, with real reasoning, rather
than built from invented signals.

Isolation: NEVER imported by any Production module (verified by
tests/test_mpc_isolation.py). Reads real Production Intelligence types
(PriceStructure/MarketStructure/VolatilityStructure/MarketDirection) in
exactly one isolated file, translate.py -- engine.py and every other
file in this package stay pure and Production-import-free. Order-placing
functions are never referenced anywhere in this package.

See docs/MARKET_PHENOMENA_CLASSIFIER_ARCHITECTURE.md for the full spec.
"""
from __future__ import annotations
