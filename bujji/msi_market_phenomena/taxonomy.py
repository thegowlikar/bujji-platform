"""Market Phenomena Classifier (MPC) taxonomy — Series 103. Plain string
constants (house convention, never enum.Enum).

Honesty disclosure (mirrors this project's own established "NOT
DEMONSTRATED" precedent, Sprint 115-121): the mission's example list
names 21 phenomena. Only the subset below maps cleanly, causally, and
without fabrication onto REAL, already-computed Production Intelligence-
layer fields (PriceStructure/MarketStructure/VolatilityStructure/
MarketDirection) or real, directly-observable price data (gaps). The
remainder (False Breakout, Liquidity Vacuum, Price Acceptance/Rejection,
Premium Expansion/Collapse, Strike Rotation, Delta Migration, Theta
Dominance, Gamma Acceleration, Trend Reversal) would require real
options-chain Greeks time-series or tick-level order-book data that is
not currently exposed as a single real Intelligence-layer field this
package can read causally -- see ALL_NOT_CLASSIFIABLE_V1 below. Building
fake classifiers for these would violate this project's core "never
fabricate" rule; they are honestly declared unclassifiable instead."""
from __future__ import annotations

MSI_MARKET_PHENOMENA_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- Classifiable in v1.0, each backed by a real, disclosed rule over
# real Intelligence-layer fields (see engine.py's _PHENOMENON_RULES). ----
PHENOMENON_TREND_EXPANSION = "TREND_EXPANSION"
PHENOMENON_TREND_FAILURE = "TREND_FAILURE"
PHENOMENON_RANGE_COMPRESSION = "RANGE_COMPRESSION"
PHENOMENON_VOLATILITY_EXPANSION = "VOLATILITY_EXPANSION"
PHENOMENON_VOLATILITY_COMPRESSION = "VOLATILITY_COMPRESSION"
PHENOMENON_MOMENTUM_PERSISTENCE = "MOMENTUM_PERSISTENCE"
PHENOMENON_MOMENTUM_EXHAUSTION = "MOMENTUM_EXHAUSTION"
PHENOMENON_MEAN_REVERSION = "MEAN_REVERSION"
PHENOMENON_GAP_CONTINUATION = "GAP_CONTINUATION"
PHENOMENON_GAP_FAILURE = "GAP_FAILURE"

ALL_CLASSIFIABLE_PHENOMENA_V1 = (
    PHENOMENON_TREND_EXPANSION, PHENOMENON_TREND_FAILURE, PHENOMENON_RANGE_COMPRESSION,
    PHENOMENON_VOLATILITY_EXPANSION, PHENOMENON_VOLATILITY_COMPRESSION,
    PHENOMENON_MOMENTUM_PERSISTENCE, PHENOMENON_MOMENTUM_EXHAUSTION, PHENOMENON_MEAN_REVERSION,
    PHENOMENON_GAP_CONTINUATION, PHENOMENON_GAP_FAILURE,
)

# --- Honestly declared unclassifiable in v1.0 (real reasoning, not a
# silent omission -- every MarketPhenomenaReport discloses this list). ---
ALL_NOT_CLASSIFIABLE_V1 = (
    "FALSE_BREAKOUT", "LIQUIDITY_VACUUM", "PRICE_ACCEPTANCE", "PRICE_REJECTION",
    "PREMIUM_EXPANSION", "PREMIUM_COLLAPSE", "STRIKE_ROTATION", "DELTA_MIGRATION",
    "THETA_DOMINANCE", "GAMMA_ACCELERATION", "TREND_REVERSAL",
)
NOT_CLASSIFIABLE_REASON = (
    "requires real options-chain Greeks time-series or tick-level order-book "
    "data not currently exposed as a single real Intelligence-layer field this "
    "package can read causally -- not built rather than fabricated"
)

# --- Confidence. Same NONE/LOW/MODERATE/HIGH scale used project-wide. -----
CONFIDENCE_NONE = "NONE"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MODERATE = "MODERATE"
CONFIDENCE_HIGH = "HIGH"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_NONE, CONFIDENCE_LOW, CONFIDENCE_MODERATE, CONFIDENCE_HIGH)
