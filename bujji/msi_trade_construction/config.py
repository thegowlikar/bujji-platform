"""Trade Construction Foundation config — Series 90.

Every constant here is a STRUCTURAL policy choice (a risk-management
convention a desk would declare up front), never a value fit to
historical returns. Per the user's explicit Series 90 requirement,
these are cited in explanations as "configured" evidence, never as
"this performed best historically."
"""
from __future__ import annotations

# --- Expiry policy ---------------------------------------------------------
DEFAULT_MIN_DTE = 1     # Refuse same-day-expiry (0 DTE) construction by default.
DEFAULT_MAX_DTE = 45    # Beyond this, price/vol evidence used to select the
                        # strategy is considered too stale to still apply.

# --- Liquidity proxy (Deliverable 1 finding) --------------------------------
# Real NSE Bhavcopy data carries NO bid/ask (verified: 100% None across a
# real 1,876-contract chain) -- bujji.intelligence.liquidity_brain's real
# spread-based check is therefore UNAVAILABLE in historical replay. Open
# interest is the only real per-contract liquidity signal Bhavcopy
# provides, so it is used here as a DISCLOSED, WEAKER proxy (a raw
# activity floor, not a spread-tightness measure) -- never silently
# treated as equivalent to a live spread check.
MIN_OPEN_INTEREST = 500.0

# --- Risk-free rate (same value as legacy VolatilityBrain/GreeksBrain and
# msi_volatility_structure -- one shared constant, not re-derived) --------
DEFAULT_RISK_FREE_RATE = 0.065

# --- Per-family target deltas for the leg(s) that anchor the structure.
# Each is a declared risk-shape choice (e.g. "0.20 delta short strikes on
# an Iron Condor" is a conventional defined-risk-selling convention), not
# a value optimized against any return series.
FAMILY_DELTA_TARGETS = {
    "LONG_DIRECTIONAL": 0.40,
    "SHORT_DIRECTIONAL": 0.40,
    "NEUTRAL_PREMIUM_SELLING": 0.20,
    "NEUTRAL_PREMIUM_BUYING": 0.40,
    "VOLATILITY_EXPANSION": 0.50,
    "VOLATILITY_COMPRESSION": 0.50,
    "IRON_CONDOR": 0.20,
    "IRON_FLY": 0.50,
    "BUTTERFLY": 0.50,
    "RATIO": 0.30,
    "COVERED": 0.30,
    "SYNTHETIC": 0.50,
    "CALENDAR": 0.50,
}

# --- Wing width policy (Iron Condor / Iron Fly / Butterfly) ---------------
# Preferred source: the day's real VSB expected_move_pct (a real,
# observable evidence point -- "how far the market itself is pricing a
# move to"). Falls back to a fixed structural point-width ONLY when VSB's
# expected move is unavailable (never silently substituted without
# disclosure -- the Explanation always states which source was used).
WING_WIDTH_FALLBACK_POINTS = 200.0
WING_WIDTH_EXPECTED_MOVE_MULTIPLIER = 1.0  # Wing width = 1x the day's 1-sigma expected move.

# --- Strike interval fallback (matches config/config.yaml's NIFTY value;
# real available strikes in the chain are always preferred -- this is
# only used to detect "impossible" wing widths against a real grid). ---
STRIKE_INTERVAL_FALLBACK = 50.0
