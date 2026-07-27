"""Live Observation Producer Framework vocabulary — BUJJI Engineering
Series 74 (Live Observation Producer Framework v1 / LOPF v1).

Lives at `bujji/live_observation/`, outside `mic_v2`, `bujji.mic_replay`,
`bujji.production_runtime`, `bujji.trading_brain`, and
`bujji.strategy_selector` -- same isolation discipline as Series
73A/73B/73C. This package is NOT a new observation domain: it is the
live-production framework that will eventually feed the Series 73A
Observation Contract from real-time broker events instead of historical
Bhavcopy files (Series 73B/73C's ingestion path). Historical replay and
live production must emit structurally identical `Observation` objects
(see `bujji.market_observation.models.Observation`) -- only the source
differs.

Following this project's established convention (see
`bujji/market_observation/taxonomy.py`, `bujji/futures_observation/taxonomy.py`,
`bujji/options_observation/taxonomy.py`), closed vocabularies here are
plain string constants collected into `ALL_*` tuples, not `enum.Enum`
classes.
"""
from __future__ import annotations

LIVE_OBSERVATION_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# EventType -- the closed set of live event kinds a Producer may emit
# (Deliverable 3). A new event kind requires a deliberate addition here,
# never an inferred string.
# ---------------------------------------------------------------------------
EVENT_TICK_RECEIVED = "TICK_RECEIVED"
EVENT_CANDLE_CLOSED = "CANDLE_CLOSED"
EVENT_OPTION_CHAIN_UPDATED = "OPTION_CHAIN_UPDATED"
EVENT_FUTURES_UPDATED = "FUTURES_UPDATED"
EVENT_VIX_UPDATED = "VIX_UPDATED"
EVENT_CONNECTION_ESTABLISHED = "CONNECTION_ESTABLISHED"
EVENT_CONNECTION_LOST = "CONNECTION_LOST"
EVENT_HEARTBEAT = "HEARTBEAT"
EVENT_PRODUCER_ERROR = "PRODUCER_ERROR"

ALL_EVENT_TYPES = (
    EVENT_TICK_RECEIVED,
    EVENT_CANDLE_CLOSED,
    EVENT_OPTION_CHAIN_UPDATED,
    EVENT_FUTURES_UPDATED,
    EVENT_VIX_UPDATED,
    EVENT_CONNECTION_ESTABLISHED,
    EVENT_CONNECTION_LOST,
    EVENT_HEARTBEAT,
    EVENT_PRODUCER_ERROR,
)

# Event types that carry a translatable market fact (i.e. a payload that
# `engine.translate_event` should turn into an `Observation`). The
# connection-lifecycle/heartbeat/error events are infrastructure signals,
# never translated into an Observation.
TRANSLATABLE_EVENT_TYPES = (
    EVENT_TICK_RECEIVED,
    EVENT_CANDLE_CLOSED,
    EVENT_OPTION_CHAIN_UPDATED,
    EVENT_FUTURES_UPDATED,
    EVENT_VIX_UPDATED,
)

# ---------------------------------------------------------------------------
# ProducerState -- Producer lifecycle (Deliverable 7). A first-class,
# explicit state machine, never an implicit boolean flag soup.
# ---------------------------------------------------------------------------
STATE_CREATED = "CREATED"
STATE_CONNECTING = "CONNECTING"
STATE_CONNECTED = "CONNECTED"
STATE_STREAMING = "STREAMING"
STATE_RECONNECTING = "RECONNECTING"
STATE_STOPPED = "STOPPED"
STATE_FAILED = "FAILED"

ALL_PRODUCER_STATES = (
    STATE_CREATED,
    STATE_CONNECTING,
    STATE_CONNECTED,
    STATE_STREAMING,
    STATE_RECONNECTING,
    STATE_STOPPED,
    STATE_FAILED,
)

# ---------------------------------------------------------------------------
# Valid lifecycle transitions -- the literal spec diagram is
# CREATED -> CONNECTING -> CONNECTED -> STREAMING, with CONNECTING and
# STREAMING each able to fail/drop. Beyond the literal diagram, this
# module's own judgment call (disclosed per the spec's request) adds:
#   * STREAMING -> RECONNECTING on a dropped feed while already
#     streaming (distinct from CONNECTING -> FAILED, which is a
#     never-yet-connected failure), and RECONNECTING -> STREAMING /
#     FAILED as the two possible outcomes of a reconnect attempt.
#   * STOPPED is reachable from every non-terminal state (CREATED,
#     CONNECTING, CONNECTED, STREAMING, RECONNECTING) because a caller
#     must always be able to request a clean shutdown regardless of
#     where the producer currently is in its lifecycle -- this mirrors
#     the project's "never trap the caller" discipline.
#   * FAILED and STOPPED are terminal: no transitions out. A failed or
#     stopped producer must be reconstructed, never resurrected in
#     place (mirrors MOC's "never fabricate/never silently recover"
#     discipline applied to lifecycle state).
# ---------------------------------------------------------------------------
VALID_TRANSITIONS = {
    STATE_CREATED: (STATE_CONNECTING, STATE_STOPPED),
    STATE_CONNECTING: (STATE_CONNECTED, STATE_FAILED, STATE_STOPPED),
    STATE_CONNECTED: (STATE_STREAMING, STATE_RECONNECTING, STATE_STOPPED, STATE_FAILED),
    STATE_STREAMING: (STATE_RECONNECTING, STATE_STOPPED, STATE_FAILED),
    STATE_RECONNECTING: (STATE_STREAMING, STATE_CONNECTED, STATE_FAILED, STATE_STOPPED),
    STATE_STOPPED: (),
    STATE_FAILED: (),
}

# ---------------------------------------------------------------------------
# AggregationInterval -- the closed set of window granularities the
# aggregation framework understands (Deliverable 5). TICK means
# "no aggregation, pass through" -- it is included for symmetry with
# `bujji.market_observation.taxonomy.RESOLUTION_TICK`.
# ---------------------------------------------------------------------------
INTERVAL_TICK = "TICK"
INTERVAL_ONE_SECOND = "ONE_SECOND"
INTERVAL_FIVE_SECOND = "FIVE_SECOND"
INTERVAL_FIFTEEN_SECOND = "FIFTEEN_SECOND"
INTERVAL_THIRTY_SECOND = "THIRTY_SECOND"
INTERVAL_ONE_MINUTE = "ONE_MINUTE"
INTERVAL_FIVE_MINUTE = "FIVE_MINUTE"

ALL_AGGREGATION_INTERVALS = (
    INTERVAL_TICK,
    INTERVAL_ONE_SECOND,
    INTERVAL_FIVE_SECOND,
    INTERVAL_FIFTEEN_SECOND,
    INTERVAL_THIRTY_SECOND,
    INTERVAL_ONE_MINUTE,
    INTERVAL_FIVE_MINUTE,
)

# Interval -> nominal window length in seconds. TICK has no window
# length (pass-through, never aggregated).
INTERVAL_SECONDS = {
    INTERVAL_ONE_SECOND: 1,
    INTERVAL_FIVE_SECOND: 5,
    INTERVAL_FIFTEEN_SECOND: 15,
    INTERVAL_THIRTY_SECOND: 30,
    INTERVAL_ONE_MINUTE: 60,
    INTERVAL_FIVE_MINUTE: 300,
}

# ---------------------------------------------------------------------------
# Out-of-window-order tick handling (documented per the spec's explicit
# request, consistent with 73B/73C's "never silently drop data"
# precedent): a tick whose timestamp falls before the current window's
# start is NEVER dropped. It is recorded as a rejected/late tick on the
# window (see models.AggregationWindow.late_ticks) rather than being
# folded into the window's OHLC computation out of order -- folding it
# in would silently corrupt the window's `open` (first) / `close`
# (last) semantics. Callers may inspect `late_ticks` and decide their
# own policy (e.g. route to a corrective series); this framework layer
# only disclose it, it never discards it.
# ---------------------------------------------------------------------------
LATE_TICK_REASON_BEFORE_WINDOW_START = "BEFORE_WINDOW_START"

ALL_LATE_TICK_REASONS = (LATE_TICK_REASON_BEFORE_WINDOW_START,)
