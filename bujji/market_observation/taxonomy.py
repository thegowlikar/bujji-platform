"""Market Observation Contract vocabulary — BUJJI Engineering Series 73A
(Market Observation Contract v1 / MOC v1).

Implements the Observation layer defined by `docs/MOF_V1_FOUNDATION.md`
(Series 72) and referenced as the base layer beneath MSI (Series 71)
and Trading Brain's Decision layer. Lives at `bujji/market_observation/`,
outside `mic_v2`, `bujji.mic_replay`, `bujji.production_runtime`, and
`bujji.trading_brain` -- this module records observed facts, never
interprets them (per MOF Deliverable 1's four-layer distinction:
Observation -> Derived Evidence -> Intelligence -> Decision. MOC v1
implements ONLY the Observation layer).

Following this project's established convention (see
`bujji/runtime_safety/taxonomy.py`, `bujji/authentication/taxonomy.py`),
closed vocabularies here are plain string constants collected into
`ALL_*` tuples, not `enum.Enum` classes -- this keeps them trivially
JSON-serializable without a codec and matches every other *_observation
adjacent module in this codebase.
"""
from __future__ import annotations

MARKET_OBSERVATION_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Schema versions this module recognizes as compatible. Adding a new
# recognized version is a deliberate, reviewed change, never inferred.
# ---------------------------------------------------------------------------
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# ObservationType -- the 17 observation domains named in MOF v1
# Deliverable 2. This is a closed set; a new domain requires a
# deliberate addition here, never an inferred string.
# ---------------------------------------------------------------------------
TYPE_PRICE = "PRICE"
TYPE_OPTION_CHAIN = "OPTION_CHAIN"
TYPE_OPTION_OPEN_INTEREST = "OPTION_OPEN_INTEREST"
TYPE_OPTION_VOLUME = "OPTION_VOLUME"
TYPE_OPTION_LIQUIDITY = "OPTION_LIQUIDITY"
TYPE_FUTURES = "FUTURES"
TYPE_FUTURES_OPEN_INTEREST = "FUTURES_OPEN_INTEREST"
TYPE_VOLATILITY_VIX = "VOLATILITY_VIX"
TYPE_VOLATILITY_IV = "VOLATILITY_IV"
TYPE_MARKET_BREADTH = "MARKET_BREADTH"
TYPE_SECTOR_ROTATION = "SECTOR_ROTATION"
TYPE_ETF_FLOW = "ETF_FLOW"
TYPE_CROSS_ASSET = "CROSS_ASSET"
TYPE_MACRO_CALENDAR = "MACRO_CALENDAR"
TYPE_TIME_SESSION = "TIME_SESSION"
TYPE_CORPORATE_EVENT = "CORPORATE_EVENT"
TYPE_UNKNOWN = "UNKNOWN"

ALL_OBSERVATION_TYPES = (
    TYPE_PRICE,
    TYPE_OPTION_CHAIN,
    TYPE_OPTION_OPEN_INTEREST,
    TYPE_OPTION_VOLUME,
    TYPE_OPTION_LIQUIDITY,
    TYPE_FUTURES,
    TYPE_FUTURES_OPEN_INTEREST,
    TYPE_VOLATILITY_VIX,
    TYPE_VOLATILITY_IV,
    TYPE_MARKET_BREADTH,
    TYPE_SECTOR_ROTATION,
    TYPE_ETF_FLOW,
    TYPE_CROSS_ASSET,
    TYPE_MACRO_CALENDAR,
    TYPE_TIME_SESSION,
    TYPE_CORPORATE_EVENT,
    TYPE_UNKNOWN,
)

# ---------------------------------------------------------------------------
# ObservationResolution -- the time granularity of an ObservationSeries,
# per MOF Deliverable 3/5. A first-class, explicit property, never left
# implicit.
# ---------------------------------------------------------------------------
RESOLUTION_TICK = "TICK"
RESOLUTION_ONE_MINUTE = "ONE_MINUTE"
RESOLUTION_FIVE_MINUTE = "FIVE_MINUTE"
RESOLUTION_FIFTEEN_MINUTE = "FIFTEEN_MINUTE"
RESOLUTION_HOURLY = "HOURLY"
RESOLUTION_DAILY = "DAILY"
RESOLUTION_WEEKLY = "WEEKLY"
RESOLUTION_EVENT = "EVENT"

ALL_RESOLUTIONS = (
    RESOLUTION_TICK,
    RESOLUTION_ONE_MINUTE,
    RESOLUTION_FIVE_MINUTE,
    RESOLUTION_FIFTEEN_MINUTE,
    RESOLUTION_HOURLY,
    RESOLUTION_DAILY,
    RESOLUTION_WEEKLY,
    RESOLUTION_EVENT,
)

# ---------------------------------------------------------------------------
# ValidationStatus -- outcome of the structural Validation stage
# (MOF Deliverable 4). Never silently passes through incomplete data.
# ---------------------------------------------------------------------------
VALIDATION_VALID = "VALID"
VALIDATION_INVALID = "INVALID"
VALIDATION_INCOMPLETE = "INCOMPLETE"
VALIDATION_UNKNOWN = "UNKNOWN"

ALL_VALIDATION_STATUSES = (
    VALIDATION_VALID,
    VALIDATION_INVALID,
    VALIDATION_INCOMPLETE,
    VALIDATION_UNKNOWN,
)

# ---------------------------------------------------------------------------
# SourceQuality -- MOF's own disclosed, evidenced assessment of how
# trustworthy a given ObservationSource is (Deliverable 3's
# ObservationQuality concept, applied at the source level). Never a
# hidden or silently-applied adjustment.
# ---------------------------------------------------------------------------
SOURCE_QUALITY_HIGH = "HIGH"
SOURCE_QUALITY_MEDIUM = "MEDIUM"
SOURCE_QUALITY_LOW = "LOW"
SOURCE_QUALITY_UNKNOWN = "UNKNOWN"

ALL_SOURCE_QUALITIES = (
    SOURCE_QUALITY_HIGH,
    SOURCE_QUALITY_MEDIUM,
    SOURCE_QUALITY_LOW,
    SOURCE_QUALITY_UNKNOWN,
)

# ---------------------------------------------------------------------------
# ObservationOrigin -- how this Observation was produced, per MOF
# Deliverable 4's lifecycle (Live capture vs. Replay vs. a historical
# reconstruction assembled after the fact, e.g. from Bhavcopy).
# ---------------------------------------------------------------------------
ORIGIN_LIVE = "LIVE"
ORIGIN_REPLAY = "REPLAY"
ORIGIN_HISTORICAL_RECONSTRUCTION = "HISTORICAL_RECONSTRUCTION"

ALL_ORIGINS = (
    ORIGIN_LIVE,
    ORIGIN_REPLAY,
    ORIGIN_HISTORICAL_RECONSTRUCTION,
)

# ---------------------------------------------------------------------------
# ObservationValue -- the closed set of value shapes an Observation may
# carry (see models.ObservationValue). Domain-neutral by design: MOC v1
# never introduces a PriceValue/OIValue/VIXValue per-domain subclass --
# every domain's payload rides inside the same generic shape, tagged by
# `value_kind`, so Identity/Value logic never needs to know which of the
# 17 ObservationTypes produced it.
# ---------------------------------------------------------------------------
VALUE_KIND_SCALAR = "SCALAR"
VALUE_KIND_OHLC = "OHLC"
VALUE_KIND_MAPPING = "MAPPING"
VALUE_KIND_TEXT = "TEXT"

ALL_VALUE_KINDS = (
    VALUE_KIND_SCALAR,
    VALUE_KIND_OHLC,
    VALUE_KIND_MAPPING,
    VALUE_KIND_TEXT,
)

# ---------------------------------------------------------------------------
# Gap marker reason -- why a gap was recorded in an ObservationSeries
# (engine.detect_gaps / models.SeriesGap). Closed, never free text.
# ---------------------------------------------------------------------------
GAP_REASON_MISSING_INTERVAL = "MISSING_INTERVAL"
GAP_REASON_OUT_OF_ORDER_SKIPPED = "OUT_OF_ORDER_SKIPPED"

ALL_GAP_REASONS = (
    GAP_REASON_MISSING_INTERVAL,
    GAP_REASON_OUT_OF_ORDER_SKIPPED,
)
