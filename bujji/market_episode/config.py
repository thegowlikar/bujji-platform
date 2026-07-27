"""Static configuration for the Market Episode Engine."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.MARKET_EPISODE_VERSION

DEFAULT_DETECTION_CONTEXT_LIVE = "LIVE"
DEFAULT_DETECTION_CONTEXT_REPLAY = "REPLAY"
DEFAULT_DETECTION_CONTEXT_BATCH = "BATCH"

DEFAULT_ORIGINATING_SOURCE = "market_episode.engine"

# Deliverable 5 — configurable temporal proximity window. Two events
# for the same instrument, of compatible event-type families, whose
# timestamps fall within this many seconds of each other's episode
# are eligible to be grouped into the same Episode. Purely structural
# (a clock measurement), never a market judgment.
DEFAULT_PROXIMITY_WINDOW_SECONDS = 300.0

# Time-based lifecycle transitions (see taxonomy.py's module docstring
# for the full transition-table reasoning). Both are silence
# durations, measured from an episode's `latest_update`:
#   * No compatible event for >= this many seconds -> ACTIVE goes QUIESCENT.
DEFAULT_QUIESCENT_AFTER_SECONDS = 900.0
#   * No compatible event for a further this-many seconds after going
#     QUIESCENT -> QUIESCENT goes CLOSED.
DEFAULT_CLOSE_AFTER_SECONDS = 1800.0
