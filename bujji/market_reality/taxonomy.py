"""Market Reality Layer 0 vocabulary — Phase 17E.

Closed vocabularies as plain string constants collected into `ALL_*`
tuples, following this codebase's established convention (see
`bujji/market_observation/taxonomy.py`, `bujji/runtime_safety/taxonomy.py`)
rather than `enum.Enum` -- trivially JSON-serializable without a codec.

LAYER 0 RECORDS RAW OBSERVATION ONLY. Nothing in this module (or this
package) names an indicator, a Greek, an implied volatility, a VWAP, a
regime, a classification, or a strategy signal. `FORBIDDEN_PAYLOAD_FIELDS`
below turns that rule from a convention into a runtime rejection.

Distinction that matters throughout this package:
  * `observation_kind` (here)  -- HOW the fact was captured
                                  (a tick vs a depth snapshot vs a candle).
  * `observation_type` (MOC)   -- WHAT domain the fact belongs to
                                  (PRICE, FUTURES, OPTION_CHAIN, MARKET_DEPTH).
Both are recorded; neither is inferred from the other.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Schema version. Bumped 1.0.0 -> 1.1.0 for Phase 17E because MARKET_DEPTH
# is a new observation type a 1.0.0-era consumer cannot be assumed to
# understand. Records carrying 1.0.0 remain valid and readable (see
# market_observation.taxonomy.RECOGNIZED_SCHEMA_VERSIONS, which now
# recognizes both) -- the bump gates NEW consumers, it does not
# invalidate history.
# ---------------------------------------------------------------------------
# Bumped 1.1.0 -> 1.2.0 in Phase 17F.0.1: CAPTURE_EVENT is a new Layer 0
# record kind a 1.1.0-era consumer cannot be assumed to handle, and a
# consumer that silently ignores capture events would reconstruct a
# market that never went quiet. Prior versions stay recognized -- the
# bump gates new consumers, it never invalidates old facts.
#
# NOTE: this version is deliberately INDEPENDENT of
# market_observation.MARKET_OBSERVATION_VERSION, which stays at 1.1.0.
# The two version different things: MOC versions the market's own
# observable domains; this versions Layer 0's record vocabulary. A
# capture event extends the latter and not the former, and having kept
# them separate is what makes that expressible without lying about
# either.
LAYER0_SCHEMA_VERSION = "1.2.0"
RECOGNIZED_LAYER0_SCHEMA_VERSIONS = ("1.0.0", "1.1.0", "1.2.0")

# The validator's own version, recorded on every rejection so a rejection
# is attributable to a specific rule set (Phase 17E scope item 4).
VALIDATOR_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Observation kinds -- HOW the observation was captured.
# ---------------------------------------------------------------------------
KIND_MARKET_TICK = "MARKET_TICK"
KIND_QUOTE = "QUOTE"
KIND_MARKET_DEPTH = "MARKET_DEPTH"
KIND_OPTION_CHAIN = "OPTION_CHAIN"
KIND_CANDLE = "CANDLE"

ALL_OBSERVATION_KINDS = (
    KIND_MARKET_TICK,
    KIND_QUOTE,
    KIND_MARKET_DEPTH,
    KIND_OPTION_CHAIN,
    KIND_CANDLE,
)

# ---------------------------------------------------------------------------
# Capture events -- facts about THE OBSERVER, not the market.
#
# CAPTURE_EVENT is deliberately NOT a member of ALL_OBSERVATION_KINDS. It
# is a sibling record type sharing Layer 0's log and ordering, not an
# observation kind: the observation validator must never accept one as
# market data, and a materializer must never fold one into a candle.
# Keeping it out of that tuple is what makes both of those structural
# rather than matters of discipline.
# ---------------------------------------------------------------------------
KIND_CAPTURE_EVENT = "CAPTURE_EVENT"

REASON_DISCONNECT = "DISCONNECT"
REASON_RECONNECT_RECOVERED = "RECONNECT_RECOVERED"
REASON_QUEUE_OVERFLOW = "QUEUE_OVERFLOW"
REASON_RATE_LIMIT_SKIP = "RATE_LIMIT_SKIP"
REASON_AUTH_FAILURE = "AUTH_FAILURE"
REASON_SHUTDOWN_DRAIN_INCOMPLETE = "SHUTDOWN_DRAIN_INCOMPLETE"
REASON_COLLECTOR_RESTART = "COLLECTOR_RESTART"

ALL_CAPTURE_REASONS = (
    REASON_DISCONNECT,
    REASON_RECONNECT_RECOVERED,
    REASON_QUEUE_OVERFLOW,
    REASON_RATE_LIMIT_SKIP,
    REASON_AUTH_FAILURE,
    REASON_SHUTDOWN_DRAIN_INCOMPLETE,
    REASON_COLLECTOR_RESTART,
)

# Stream item discriminators for the ordered union replay.
STREAM_OBSERVATION = "OBSERVATION"
STREAM_CAPTURE_EVENT = "CAPTURE_EVENT"
ALL_STREAM_ITEM_KINDS = (STREAM_OBSERVATION, STREAM_CAPTURE_EVENT)

# ---------------------------------------------------------------------------
# Instrument types.
# ---------------------------------------------------------------------------
INSTRUMENT_SPOT = "SPOT"
INSTRUMENT_FUTURE = "FUTURE"
INSTRUMENT_OPTION = "OPTION"
INSTRUMENT_INDEX = "INDEX"

ALL_INSTRUMENT_TYPES = (
    INSTRUMENT_SPOT,
    INSTRUMENT_FUTURE,
    INSTRUMENT_OPTION,
    INSTRUMENT_INDEX,
)

# Identity fields each instrument type must carry beyond `instrument`
# itself. A derivative without an expiry is not identifiable; an option
# without a strike/right is not identifiable.
REQUIRED_IDENTITY_FIELDS = {
    INSTRUMENT_SPOT: (),
    INSTRUMENT_INDEX: (),
    INSTRUMENT_FUTURE: ("expiry",),
    INSTRUMENT_OPTION: ("expiry", "strike", "option_type"),
}

OPTION_TYPE_CE = "CE"
OPTION_TYPE_PE = "PE"
ALL_OPTION_TYPES = (OPTION_TYPE_CE, OPTION_TYPE_PE)

# ---------------------------------------------------------------------------
# Required payload keys per observation kind.
#
# "Required" means the key must be PRESENT. It does not mean the value
# must be non-zero: a genuinely zero bid/ask on an illiquid contract is a
# true market fact and must be stored as one (verified live 2026-08-12 on
# a far-OTM NIFTY option: lp=0.05, bid=0, ask=0 -- real, not missing).
# Absent means absent; zero means zero; the two are never conflated.
# ---------------------------------------------------------------------------
REQUIRED_PAYLOAD_FIELDS = {
    KIND_MARKET_TICK: ("ltp",),
    KIND_QUOTE: ("ltp",),
    KIND_MARKET_DEPTH: ("bids", "asks"),
    KIND_OPTION_CHAIN: ("strikes",),
    KIND_CANDLE: ("open", "high", "low", "close"),
}

# ---------------------------------------------------------------------------
# Fields that must NEVER appear in a Layer 0 payload. These are derived
# products, not observations -- they belong to a materializer's output
# (Layer 1+), where the derivation itself becomes part of that record's
# lineage. Enforced at validation time, so the "forbidden in Layer 0"
# rule is a runtime rejection rather than a code-review convention.
# ---------------------------------------------------------------------------
FORBIDDEN_PAYLOAD_FIELDS = (
    "iv",
    "implied_volatility",
    "implied_vol",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "vwap",
    "regime",
    "signal",
    "score",
    "indicator",
    "classification",
    "sentiment",
    "trend",
)

# ---------------------------------------------------------------------------
# Certification states, mirroring Phase 17A.5's own three-state
# vocabulary exactly, plus one Layer-0-specific state for the
# fail-closed case where no certification artifact exists at all.
# ---------------------------------------------------------------------------
CERTIFIED_AVAILABLE = "CERTIFIED_AVAILABLE"
PARTIAL_CERTIFICATION = "PARTIAL_CERTIFICATION"
NOT_CERTIFIED = "NOT_CERTIFIED"
CERTIFICATION_MISSING = "CERTIFICATION_MISSING"

ALL_CERTIFICATION_STATES = (
    CERTIFIED_AVAILABLE,
    PARTIAL_CERTIFICATION,
    NOT_CERTIFIED,
    CERTIFICATION_MISSING,
)

# Only this one state permits a write to Layer 0.
WRITE_PERMITTED_CERTIFICATION_STATES = (CERTIFIED_AVAILABLE,)

# ---------------------------------------------------------------------------
# Confidence -- mechanically derived, never hand-set. See
# models.derive_confidence(); there is no code path that assigns this by
# judgment.
# ---------------------------------------------------------------------------
CONFIDENCE_HIGH = "HIGH"
CONFIDENCE_LOW = "LOW"
ALL_CONFIDENCE_LEVELS = (CONFIDENCE_HIGH, CONFIDENCE_LOW)

# ---------------------------------------------------------------------------
# Rejection reasons -- a closed set, so a rejection is always attributable
# to a named rule rather than free text.
# ---------------------------------------------------------------------------
REJECT_UNKNOWN_KIND = "UNKNOWN_OBSERVATION_KIND"
REJECT_UNKNOWN_INSTRUMENT_TYPE = "UNKNOWN_INSTRUMENT_TYPE"
REJECT_MISSING_INSTRUMENT = "MISSING_INSTRUMENT"
REJECT_MISSING_IDENTITY_FIELD = "MISSING_IDENTITY_FIELD"
REJECT_UNKNOWN_OPTION_TYPE = "UNKNOWN_OPTION_TYPE"
REJECT_MALFORMED_EVENT_TIMESTAMP = "MALFORMED_EVENT_TIMESTAMP"
REJECT_MALFORMED_CAPTURE_TIMESTAMP = "MALFORMED_CAPTURE_TIMESTAMP"
REJECT_MISSING_CAPTURE_TIMESTAMP = "MISSING_CAPTURE_TIMESTAMP"
REJECT_EVENT_AFTER_CAPTURE = "EVENT_TIMESTAMP_AFTER_CAPTURE_TIMESTAMP"
REJECT_CAPTURE_IN_FUTURE = "CAPTURE_TIMESTAMP_IN_FUTURE"
REJECT_MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_PAYLOAD_FIELD"
REJECT_FORBIDDEN_DERIVED_FIELD = "FORBIDDEN_DERIVED_FIELD_IN_LAYER0"
REJECT_UNRECOGNIZED_SCHEMA_VERSION = "UNRECOGNIZED_SCHEMA_VERSION"
REJECT_NOT_CERTIFIED = "SOURCE_NOT_CERTIFIED"
REJECT_MISSING_SOURCE = "MISSING_SOURCE"
REJECT_MISSING_ACCESS_METHOD = "MISSING_ACCESS_METHOD"
REJECT_EMPTY_PAYLOAD = "EMPTY_PAYLOAD"

ALL_REJECTION_REASONS = (
    REJECT_UNKNOWN_KIND,
    REJECT_UNKNOWN_INSTRUMENT_TYPE,
    REJECT_MISSING_INSTRUMENT,
    REJECT_MISSING_IDENTITY_FIELD,
    REJECT_UNKNOWN_OPTION_TYPE,
    REJECT_MALFORMED_EVENT_TIMESTAMP,
    REJECT_MALFORMED_CAPTURE_TIMESTAMP,
    REJECT_MISSING_CAPTURE_TIMESTAMP,
    REJECT_EVENT_AFTER_CAPTURE,
    REJECT_CAPTURE_IN_FUTURE,
    REJECT_MISSING_REQUIRED_FIELD,
    REJECT_FORBIDDEN_DERIVED_FIELD,
    REJECT_UNRECOGNIZED_SCHEMA_VERSION,
    REJECT_NOT_CERTIFIED,
    REJECT_MISSING_SOURCE,
    REJECT_MISSING_ACCESS_METHOD,
    REJECT_EMPTY_PAYLOAD,
)

# ---------------------------------------------------------------------------
# Append outcomes. DUPLICATE is deliberately NOT a rejection -- see
# store.RawObservationStore.append()'s docstring for the rationale.
# ---------------------------------------------------------------------------
OUTCOME_ACCEPTED = "ACCEPTED"
OUTCOME_DUPLICATE = "DUPLICATE"
OUTCOME_REJECTED = "REJECTED"
ALL_APPEND_OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_DUPLICATE, OUTCOME_REJECTED)

# ---------------------------------------------------------------------------
# Transformation history. A Layer 0 record carries EXACTLY this one entry
# -- Layer 0 performs zero transformation, and any component appending a
# second entry is by definition not Layer 0 (Phase 17D Part 2.1).
# ---------------------------------------------------------------------------
TRANSFORMATION_RAW_CAPTURE = "RAW_CAPTURE"

# ---------------------------------------------------------------------------
# Source health -- describes THE FEED, never the market. A DEGRADED feed
# says nothing about price behaviour and must never be read as such.
# ---------------------------------------------------------------------------
SOURCE_HEALTHY = "HEALTHY"
SOURCE_DEGRADED = "DEGRADED"
SOURCE_SILENT = "SILENT"
ALL_SOURCE_HEALTH_STATES = (SOURCE_HEALTHY, SOURCE_DEGRADED, SOURCE_SILENT)

# A feed delivering at least this fraction of expected observations is
# HEALTHY; anything above zero but below it is DEGRADED; exactly zero
# received is SILENT.
SOURCE_HEALTHY_MIN_RATIO = 0.95
