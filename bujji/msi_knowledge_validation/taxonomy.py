"""Knowledge Validation Engine (KVE) taxonomy — Series 105. Plain string
constants (house convention, never enum.Enum)."""
from __future__ import annotations

MSI_KNOWLEDGE_VALIDATION_VERSION = "1.0.0"
RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# --- The seven, exhaustive, mutually-exclusive validation states. Every
# real hypothesis receives EXACTLY one. -------------------------------------
STATE_NOT_OBSERVED = "NOT_OBSERVED"
STATE_OBSERVED = "OBSERVED"
STATE_REPEATED = "REPEATED"
STATE_EMERGING = "EMERGING"
STATE_VALIDATED = "VALIDATED"
STATE_DECAYING = "DECAYING"
STATE_INVALIDATED = "INVALIDATED"

ALL_VALIDATION_STATES = (
    STATE_NOT_OBSERVED, STATE_OBSERVED, STATE_REPEATED, STATE_EMERGING,
    STATE_VALIDATED, STATE_DECAYING, STATE_INVALIDATED,
)

# --- Consistency verdict, plain string house convention. ------------------
CONSISTENCY_CONSISTENT = "CONSISTENT"
CONSISTENCY_INCONSISTENT = "INCONSISTENT"
CONSISTENCY_UNKNOWN = "UNKNOWN"
ALL_CONSISTENCY_STATES = (CONSISTENCY_CONSISTENT, CONSISTENCY_INCONSISTENT, CONSISTENCY_UNKNOWN)

# --- Evidence growth/decay trend, plain string. ----------------------------
TREND_GROWING = "GROWING"
TREND_STABLE = "STABLE"
TREND_WEAKENING = "WEAKENING"
TREND_UNKNOWN = "UNKNOWN"
ALL_TRENDS = (TREND_GROWING, TREND_STABLE, TREND_WEAKENING, TREND_UNKNOWN)

# --- Real, declarative thresholds. Never tuned against replay outcomes,
# declared once (config.py). Referenced here for documentation only. -------
REPEATED_MIN_OCCURRENCES = 2
EMERGING_MIN_OCCURRENCES = 5
VALIDATED_MIN_OCCURRENCES = 20
VALIDATED_MIN_DIVERSITY = 3
VALIDATED_MIN_REPLAY_SUPPORT_RATIO = 0.5
INVALIDATED_MAX_CONSISTENCY_RATIO = 0.5  # below this fraction of causally-valid-and-legal occurrences -> INVALIDATED
