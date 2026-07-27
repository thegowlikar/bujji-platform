"""Static configuration for the Live Market Event Engine."""
from __future__ import annotations

from . import taxonomy

SCHEMA_VERSION = taxonomy.LIVE_MARKET_EVENTS_VERSION

DEFAULT_DETECTION_CONTEXT_LIVE = "LIVE"
DEFAULT_DETECTION_CONTEXT_REPLAY = "REPLAY"
DEFAULT_DETECTION_CONTEXT_BATCH = "BATCH"

DEFAULT_ORIGINATING_SOURCE = "live_market_events.engine"

PRICE_GAP_FRACTION_THRESHOLD = taxonomy.PRICE_GAP_FRACTION_THRESHOLD
