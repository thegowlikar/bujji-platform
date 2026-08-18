"""Market Thesis vocabulary — BUJJI Options OS v3, Trading Brain
Intelligence Upgrade, Phase 3.

THIN COMPOSITION LAYER ONLY. A real Market Thesis Generator already
exists and is reused here unmodified:
`bujji.msi_trade_thesis.engine.derive_trade_thesis` (composes Price
Structure, Market Structure, Market Direction, Participant
Positioning, Volatility Structure, and Consensus into a real,
deterministic market thesis) and `bujji.msi_strategy_selection_
foundation.engine.assess_all_families` (a real, declarative per-family
suitability gate). This package does not re-derive either -- it calls
both directly and adds only the fields that neither one already
exposes (see engine.py for the full field-by-field provenance).

Every "environment" field produced here is either a direct pass-
through of an already-real field, or the one small, disclosed
translation below (premium_environment) -- never new market
intelligence.
"""
from __future__ import annotations

MARKET_THESIS_VERSION = "1.0.0"

# premium_environment -- a NEW, small rename of VSB's or
# volatility_intelligence's own real iv_state (IV_RICH/IV_CHEAP/
# IV_FAIR/UNKNOWN or RICH/CHEAP/FAIR/UNKNOWN respectively), needed
# only because neither msi_trade_thesis nor
# msi_strategy_selection_foundation exposes a field literally named
# "premium_environment" -- no new richness computation happens here.
PREMIUM_RICH = "RICH"
PREMIUM_FAIR = "FAIR"
PREMIUM_CHEAP = "CHEAP"
PREMIUM_UNKNOWN = "UNKNOWN"

ALL_PREMIUM_ENVIRONMENTS = (PREMIUM_RICH, PREMIUM_FAIR, PREMIUM_CHEAP, PREMIUM_UNKNOWN)
