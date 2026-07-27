"""Live Market Event Engine vocabulary — BUJJI Engineering Series 75
(Live Market Event Engine v1 / LMEE v1).

Lives at `bujji/live_market_events/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
and `bujji.strategy_selector` -- same isolation discipline as Series
73A/73B/73C/74. This package implements the layer immediately above
the Observation Contract: `Observation -> Market Event -> Evidence ->
Intelligence -> Decision`. It answers only "what objectively changed
between two observations" -- it never says what a change *means*.
Meaning is Derived Evidence / Intelligence / Decision work, entirely
out of scope here.

Following this project's established convention (see
`bujji/market_observation/taxonomy.py`, `bujji/live_observation/taxonomy.py`),
closed vocabularies here are plain string constants collected into
`ALL_*` tuples, not `enum.Enum` classes.

---------------------------------------------------------------------
Deliverable 2 design note -- which event types are genuinely
detectable from "current vs. previous observation" alone:
---------------------------------------------------------------------
Every event type below is produced by comparing a *current*
Observation against, at most, the *immediately previous* Observation
in the same series, PLUS a small amount of explicitly-carried running
state for the two families that need it:

  * NEW_SESSION_HIGH / NEW_SESSION_LOW require a running max/min
    across the session so far. This is a genuine tension with
    Deliverable 3's "no history beyond what is required to establish
    factual change" constraint -- resolved explicitly (see
    `engine.py` module docstring) by treating the running high/low as
    a single carried-forward FACT, not a history replay: exactly the
    same precedent Series 74 already established for
    `AggregationWindow` (a window's OHLC is a raw Observation shape
    computed from its inputs, not a derived indicator). A running
    high/low is the minimal-state analogue: one float carried forward,
    updated on each observation, never re-derived by walking the whole
    series again.

  * OBSERVATION_SERIES_GAP_DETECTED / LATE_OBSERVATION_RECEIVED need
    only the previous observation's timestamp (already available as
    "the immediately previous observation") -- no extra state.

  * DUPLICATE detection (see engine.detect_duplicate) needs only the
    previous observation's observation_id and value -- also already
    available.

Everything else (OBSERVATION_CREATED/UPDATED/CORRECTED, PRICE_CHANGED,
PRICE_GAP_DETECTED, OI_CHANGED, VOLUME_CHANGED, VIX_CHANGED,
FUTURES_UPDATED, OPTION_CHAIN_UPDATED, AGGREGATION_WINDOW_CLOSED) is a
pure pairwise (current, previous) comparison with zero extra state.
"""
from __future__ import annotations

LIVE_MARKET_EVENTS_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# MarketEventType -- the closed set of factual-change events this engine
# may emit (Deliverable 2). A new event type requires a deliberate
# addition here, never an inferred string.
# ---------------------------------------------------------------------------
OBSERVATION_CREATED = "OBSERVATION_CREATED"
OBSERVATION_UPDATED = "OBSERVATION_UPDATED"
OBSERVATION_CORRECTED = "OBSERVATION_CORRECTED"
PRICE_CHANGED = "PRICE_CHANGED"
PRICE_GAP_DETECTED = "PRICE_GAP_DETECTED"
NEW_SESSION_HIGH = "NEW_SESSION_HIGH"
NEW_SESSION_LOW = "NEW_SESSION_LOW"
OI_CHANGED = "OI_CHANGED"
VOLUME_CHANGED = "VOLUME_CHANGED"
VIX_CHANGED = "VIX_CHANGED"
FUTURES_UPDATED = "FUTURES_UPDATED"
OPTION_CHAIN_UPDATED = "OPTION_CHAIN_UPDATED"
AGGREGATION_WINDOW_CLOSED = "AGGREGATION_WINDOW_CLOSED"
OBSERVATION_SERIES_GAP_DETECTED = "OBSERVATION_SERIES_GAP_DETECTED"
LATE_OBSERVATION_RECEIVED = "LATE_OBSERVATION_RECEIVED"

# Deliberately NOT in the spec's literal list, but required by
# Deliverable 3's explicit "duplicate detection" requirement -- added
# here rather than silently reusing OBSERVATION_UPDATED for a
# no-op-value repeat, since "nothing changed" is itself a distinct
# factual outcome worth naming, never conflated with "something
# changed."
DUPLICATE_OBSERVATION_DETECTED = "DUPLICATE_OBSERVATION_DETECTED"

ALL_MARKET_EVENT_TYPES = (
    OBSERVATION_CREATED,
    OBSERVATION_UPDATED,
    OBSERVATION_CORRECTED,
    PRICE_CHANGED,
    PRICE_GAP_DETECTED,
    NEW_SESSION_HIGH,
    NEW_SESSION_LOW,
    OI_CHANGED,
    VOLUME_CHANGED,
    VIX_CHANGED,
    FUTURES_UPDATED,
    OPTION_CHAIN_UPDATED,
    AGGREGATION_WINDOW_CLOSED,
    OBSERVATION_SERIES_GAP_DETECTED,
    LATE_OBSERVATION_RECEIVED,
    DUPLICATE_OBSERVATION_DETECTED,
)

# Observation types (bujji.market_observation.taxonomy.TYPE_*) that
# PRICE_CHANGED/PRICE_GAP_DETECTED/NEW_SESSION_HIGH/NEW_SESSION_LOW
# apply to -- price-shaped observations only, never OI/volume/VIX.
_PRICE_OBSERVATION_TYPES = ("PRICE", "FUTURES")

# ---------------------------------------------------------------------------
# Gap/late/duplicate reasons -- closed, never free text.
# ---------------------------------------------------------------------------
GAP_REASON_MISSING_OBSERVATION = "MISSING_OBSERVATION"
ALL_GAP_REASONS = (GAP_REASON_MISSING_OBSERVATION,)

LATE_REASON_TIMESTAMP_BEFORE_LAST_SEEN = "TIMESTAMP_BEFORE_LAST_SEEN"
ALL_LATE_REASONS = (LATE_REASON_TIMESTAMP_BEFORE_LAST_SEEN,)

DUPLICATE_REASON_IDENTICAL_VALUE_AND_ID = "IDENTICAL_VALUE_AND_ID"
ALL_DUPLICATE_REASONS = (DUPLICATE_REASON_IDENTICAL_VALUE_AND_ID,)

# ---------------------------------------------------------------------------
# PriceGapDetected threshold -- a price move between consecutive
# observations exceeding this fraction of the previous price is
# additionally flagged PRICE_GAP_DETECTED (in addition to
# PRICE_CHANGED, never instead of it -- a gap is a large change, not a
# different kind of change). Purely structural/factual: this module
# never judges whether the gap is "real" or tradeable.
# ---------------------------------------------------------------------------
PRICE_GAP_FRACTION_THRESHOLD = 0.02
