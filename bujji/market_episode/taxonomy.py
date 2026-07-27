"""Market Episode Engine vocabulary — BUJJI Engineering Series 76
(Market Episode Engine v1 / MEE v1).

Lives at `bujji/market_episode/`, outside `mic_v2`,
`bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`,
and `bujji.strategy_selector` -- same isolation discipline as Series
73A/73B/73C/74/75. This package implements the layer immediately above
the Live Market Event Engine (Series 75): `Observation -> Market Event
-> Episode -> (future) MSI`. It groups Series 75's `MarketEvent`s into
`Episode`s -- coherent sequences of related factual changes over time.
It answers only "which changes belong together, structurally, over
time" -- it never says what the grouping *means*. Market-directional
or pattern-naming interpretation of any kind (the forbidden-vocabulary
list enforced by this sprint's isolation tests) is MSI's job, entirely
out of scope here.

Following this project's established convention (see
`bujji/market_observation/taxonomy.py`, `bujji/live_observation/taxonomy.py`,
`bujji/live_market_events/taxonomy.py`), closed vocabularies here are
plain string constants collected into `ALL_*` tuples, not
`enum.Enum` classes.

---------------------------------------------------------------------
Deliverable 2 design note -- EpisodeType is structural, not
interpretive:
---------------------------------------------------------------------
`episode_type` describes WHICH KINDS of MarketEvents are grouped
together inside an Episode -- e.g. "this Episode groups PRICE_CHANGED
events" (`PRICE_MOVEMENT_EPISODE`) or "this Episode groups events of
more than one factual family" (`MULTI_FACTOR_EPISODE`). It never
describes what the grouping means for a trader. There is deliberately
NO `BREAKOUT_EPISODE`, `REVERSAL_EPISODE`, or similarly interpretive
type here -- those are market-meaning judgments, which is MSI's
layer, not this one. If a future contributor is tempted to add an
episode type that names a trading pattern rather than a structural
event-family grouping, that is a signal the addition belongs in MSI,
not here.

---------------------------------------------------------------------
Deliverable 3 design note -- Episode lifecycle states and transitions:
---------------------------------------------------------------------
The literal lifecycle diagram in the spec is linear:
`CREATED -> ACTIVE -> QUIESCENT -> CLOSED`. Read literally that would
forbid two things real usage needs, so both are resolved explicitly
below, each a deliberate, disclosed addition to the literal diagram:

  * ACTIVE -> ACTIVE (self-transition / no-op-shaped "stay ACTIVE"):
    when a compatible event extends an already-ACTIVE episode, the
    episode does not need to visibly *leave* ACTIVE and re-enter it --
    it simply keeps growing. This is modelled as a permitted
    self-transition so `advance_time`/`process_event` can always run a
    transition check without special-casing "no visible transition
    needed."

  * QUIESCENT -> ACTIVE ("reactivation before close"): if a new
    compatible event arrives for an episode that has gone QUIESCENT
    (no compatible event for a while) but has NOT yet been formally
    CLOSED (the further silence window has not yet elapsed), the
    episode reactivates -- ACTIVE is not a one-way gate. This is
    explicitly DIFFERENT from CLOSED -> ACTIVE (forbidden, see below):
    QUIESCENT is a "not yet closed, just currently silent" state, not
    a terminal one.

  * CLOSED -> anything is explicitly FORBIDDEN. The literal diagram
    shows no CLOSED -> ACTIVE edge, and Step 0's invariant #4 makes
    this explicit: a CLOSED episode never reopens. A subsequent
    compatible event creates a brand NEW Episode (new episode_id, new
    CREATED state) rather than resurrecting a closed one. CLOSED has
    no outgoing transitions in `VALID_TRANSITIONS` -- enforced, not
    just documented.

CREATED -> ACTIVE is the only transition out of CREATED (immediately,
the moment the founding event(s) are recorded -- CREATED is a
momentary state, present in the table for completeness/symmetry with
Series 74's STATE_CREATED convention, not because an Episode is ever
observed to sit in CREATED across an `advance_time` tick).
"""
from __future__ import annotations

MARKET_EPISODE_VERSION = "1.0.0"

RECOGNIZED_SCHEMA_VERSIONS = ("1.0.0",)

# ---------------------------------------------------------------------------
# EpisodeType — the closed set of structural groupings this engine may
# assign (Deliverable 2). Describes WHICH KINDS of MarketEvents are
# grouped, never what the grouping means. See module docstring.
# ---------------------------------------------------------------------------
PRICE_MOVEMENT_EPISODE = "PRICE_MOVEMENT_EPISODE"        # Groups PRICE_CHANGED / PRICE_GAP_DETECTED / NEW_SESSION_HIGH / NEW_SESSION_LOW.
VOLUME_ACTIVITY_EPISODE = "VOLUME_ACTIVITY_EPISODE"      # Groups VOLUME_CHANGED events.
OI_ACTIVITY_EPISODE = "OI_ACTIVITY_EPISODE"              # Groups OI_CHANGED events.
VOLATILITY_EPISODE = "VOLATILITY_EPISODE"                # Groups VIX_CHANGED events.
MULTI_FACTOR_EPISODE = "MULTI_FACTOR_EPISODE"            # Groups events spanning more than one of the above families.

ALL_EPISODE_TYPES = (
    PRICE_MOVEMENT_EPISODE,
    VOLUME_ACTIVITY_EPISODE,
    OI_ACTIVITY_EPISODE,
    VOLATILITY_EPISODE,
    MULTI_FACTOR_EPISODE,
)

# Which Series-75 MarketEvent types belong to which single-family
# episode type. Purely structural (per-family membership), never a
# meaning judgment. Events not listed here (lifecycle/gap/duplicate/
# late-observation events) are not episode-forming by themselves.
_EVENT_TYPE_TO_EPISODE_TYPE = {
    "PRICE_CHANGED": PRICE_MOVEMENT_EPISODE,
    "PRICE_GAP_DETECTED": PRICE_MOVEMENT_EPISODE,
    "NEW_SESSION_HIGH": PRICE_MOVEMENT_EPISODE,
    "NEW_SESSION_LOW": PRICE_MOVEMENT_EPISODE,
    "VOLUME_CHANGED": VOLUME_ACTIVITY_EPISODE,
    "OI_CHANGED": OI_ACTIVITY_EPISODE,
    "VIX_CHANGED": VOLATILITY_EPISODE,
}

EPISODE_FORMING_EVENT_TYPES = tuple(_EVENT_TYPE_TO_EPISODE_TYPE.keys())

# ---------------------------------------------------------------------------
# Episode lifecycle states (Deliverable 3).
# ---------------------------------------------------------------------------
STATE_CREATED = "CREATED"
STATE_ACTIVE = "ACTIVE"
STATE_QUIESCENT = "QUIESCENT"
STATE_CLOSED = "CLOSED"

ALL_EPISODE_STATES = (
    STATE_CREATED,
    STATE_ACTIVE,
    STATE_QUIESCENT,
    STATE_CLOSED,
)

# Explicit transition table (mirrors Series 74's VALID_TRANSITIONS
# convention exactly) — see module docstring for the reasoning behind
# every transition beyond the literal 4-state linear diagram.
VALID_TRANSITIONS = {
    STATE_CREATED: (STATE_ACTIVE,),
    STATE_ACTIVE: (STATE_ACTIVE, STATE_QUIESCENT, STATE_CLOSED),
    STATE_QUIESCENT: (STATE_ACTIVE, STATE_CLOSED),
    STATE_CLOSED: (),
}


def is_valid_transition(from_state: str, to_state: str) -> bool:
    return to_state in VALID_TRANSITIONS.get(from_state, ())


# States in which an Episode is still eligible to receive new events /
# be considered for membership checks. CLOSED is deliberately excluded
# -- per invariant "an Episode never reopens", closed episodes are
# never checked for new-event compatibility.
OPEN_STATES = (STATE_CREATED, STATE_ACTIVE, STATE_QUIESCENT)
