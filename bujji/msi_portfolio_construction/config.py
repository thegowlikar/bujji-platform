"""Portfolio & Risk Construction config — Series 91.

Deliverable 1 finding, acted on here per the user's explicit instruction
to investigate reuse before approximating:

  MARGIN-PER-LOT: config/config.yaml already carries a real, deterministic,
  non-live figure for exactly this purpose --
  `simulated_margin_per_lot: 100000`, the value `bujji.capital.policy`'s
  own SIMULATION tier uses ("For replay and pure development -- capital
  numbers are whatever the test/replay configuration says, not tied to
  any real account at all", per `capital/providers.py`'s own docstring).
  REUSED HERE VERBATIM (`REPLAY_MARGIN_PER_LOT_ESTIMATE`), not re-derived
  --  this package's margin estimate is deliberately the SAME number the
  production SIMULATION policy already uses, so a future single-source
  config value is a trivial follow-up, not a redesign.

  AVAILABLE CAPITAL: no equivalent deterministic figure exists ANYWHERE
  in this codebase -- `CapitalSnapshot.available_margin` is populated
  exclusively from a live `broker.get_funds()` call (see
  `bujji/capital/broker_adapter.py`), and there is no config key, no
  paper-trading starting-balance constant, nothing. This is a genuine,
  disclosed gap, not an oversight: `REPLAY_ASSUMED_TOTAL_CAPITAL` below
  is a NEW, clearly-marked-TEMPORARY placeholder introduced because no
  reusable deterministic source exists -- see
  docs/PORTFOLIO_RISK_CONSTRUCTION.md Section on production integration
  for the exact adapter path once a live scheduler is available (wire
  `bujji.capital.broker_adapter.build_snapshot`'s real `available_margin`
  in, replacing this constant, not extending it).
"""
from __future__ import annotations

# --- Margin (reused from config/config.yaml's SIMULATION-tier value) -----
REPLAY_MARGIN_PER_LOT_ESTIMATE = 100_000.0

# --- Capital (NEW, temporary -- no deterministic source exists to reuse) -
REPLAY_ASSUMED_TOTAL_CAPITAL = 2_000_000.0

# --- Same safety buffer / max-lots policy as production CapitalManagementEngine's defaults ---
SAFETY_BUFFER = 0.90
CONFIGURED_MAX_LOTS_PER_TRADE = 1

# --- Contract multiplier (matches config/config.yaml market.lot_size; NIFTY-only in this corpus) ---
DEFAULT_LOT_SIZE = 75

# --- Risk policy (structural desk-risk choices, never tuned against replay outcomes) ---
ALLOW_UNDEFINED_RISK = False
MIN_CONFIDENCE_FOR_ADMISSION = "MODERATE"

# --- Concentration limits (structural, disclosed placeholders) -----------
MAX_POSITIONS_PER_STRATEGY_FAMILY = 3
MAX_POSITIONS_PER_EXPIRY = 4
MAX_POSITIONS_PER_UNDERLYING = 8

# --- Portfolio exposure limits, in index-point-equivalent units
# (raw per-contract Greek x ratio x approved_lots x lot_size) -- structural
# placeholders representing a desk's configured risk appetite, never fit
# to replay P&L. ---------------------------------------------------------
MAX_ABS_PORTFOLIO_DELTA = 500.0
MAX_ABS_PORTFOLIO_VEGA = 5_000.0

RISK_FREE_RATE = 0.065  # Same constant as every other Black-Scholes consumer in this codebase.
