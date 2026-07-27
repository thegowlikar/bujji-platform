# Market Episode Engine v1 (MEE v1) — BUJJI Engineering Series 76

## Philosophy

`bujji/market_episode/` is the third layer in BUJJI's factual pipeline:

```
Observation (73A/73B/73C) -> Market Event (75) -> Episode (76) -> (future) MSI
```

Series 75's Market Event Engine answers "what objectively changed between two
observations." This sprint answers the next, still purely structural
question: "which of those changes belong to the same coherent sequence, over
time." It never answers "what does that sequence mean."

**Episodes describe cohesive sequences of factual changes. They never
describe market meaning.**

There is no `BREAKOUT_EPISODE`, no `ACCUMULATION_EPISODE`, no trend/support/
resistance/momentum vocabulary anywhere in this package — enforced by the
isolation tests in `tests/test_market_episode_engine.py`, not just asserted
in prose.

## Invariants

Derived before any grouping code was written (see `bujji/market_episode/engine.py`'s
module docstring for the verbatim, cross-referenced version):

| # | Invariant | Enforced by | Proven by |
|---|-----------|-------------|-----------|
| INV-1 | An Event belongs to at most one OPEN Episode at a time. | `process_event` attaches an event to exactly one episode per call; CLOSED episodes are never candidates. | `test_event_belongs_to_at_most_one_active_episode` |
| INV-2 | Episodes never merge retroactively. | No merge operation exists anywhere in the engine. | `test_episodes_never_merge_retroactively` |
| INV-3 | Closing an Episode never changes its prior contents. | Growth/closure always produce a NEW immutable snapshot; ids/tuples only grow (superset). | `test_closing_never_changes_prior_contents` |
| INV-4 | A CLOSED episode never reopens. | `taxonomy.VALID_TRANSITIONS[STATE_CLOSED] == ()`; `_open_candidates` filters CLOSED out before any compatibility check. | `test_closed_episode_never_reopens` |
| INV-5 | Deterministic multi-match tie-break: join the most recently active compatible episode; ties broken by lexicographically-smallest `episode_id`. | `_select_join_target`'s sort key `(latest_update, episode_id)`. | `test_deterministic_join_tiebreak` |
| INV-6 | `episode_id` is fixed at creation and never recomputed on growth. | `_episode_id` is called only in `_create_episode`; `_grow_episode` always copies the input episode's id. | `test_deterministic_episode_creation`, `test_deterministic_extension_same_episode_id` |
| INV-7 | Duplicate-event idempotency: processing the same event twice never double-counts or double-creates. | `process_event`'s membership scan over ALL episodes before any join/create decision. | `test_duplicate_event_is_idempotent` |
| INV-8 | Episodes are scoped to a single instrument stream by construction (not a stored field). | Every entrypoint (`process_event`, `advance_time`, `generate_episodes_for_events`, `LiveMarketEpisodeStream`) is designed to be driven by one instrument's event stream at a time, mirroring `ObservationSeries`' single-instrument scope (73A) and Series 75's `generate_events_for_series` single-series scope. | Implicit in every test — no test ever crosses instruments within one call sequence. |
| INV-9 | Time-based transitions are independent of event-driven growth. | `advance_time` and `process_event` are separate pure functions sharing no mutable state. | `test_advance_time_independent_of_process_event` |

### Resolved ambiguity — why INV-8 has no stored `instrument` field

`MarketEvent` (Series 75) does not uniformly carry `instrument` in `detail`
(only `OBSERVATION_CREATED` does). Rather than inventing a field the spec's
model list does not call for, or unreliably decoding an opaque
`observation_id` hash, this engine adopts the same operating scope Series 75
itself uses: a caller drives one instrument's event stream through one
`process_event`/`advance_time` sequence at a time (exactly like
`ObservationSeries` and `generate_events_for_series` are single-instrument by
construction). "Same instrument" is therefore true by construction, not by a
runtime field comparison, and cross-instrument grouping is structurally
impossible — the engine never receives events from more than one instrument
in a single call sequence.

### Resolved ambiguity — episode_type can be upgraded on growth, episode_id cannot

An episode's `episode_type` starts as whichever single-family type its
founding event maps to (`PRICE_MOVEMENT_EPISODE`, `VOLUME_ACTIVITY_EPISODE`,
`OI_ACTIVITY_EPISODE`, `VOLATILITY_EPISODE`). If a later compatible event
belongs to a *different* family, the grown snapshot's `episode_type` is
deterministically upgraded to `MULTI_FACTOR_EPISODE` — this reflects Deliverable
5's explicit example ("grouping a PriceChanged with a VolumeChanged for the
same instrument within the same window is sensible"). `episode_id` never
changes when this happens (INV-6) — only the descriptive `episode_type` field
of the new snapshot does.

## episode_id design: fixed at creation, content-hash of founding event only

`episode_id = "EPS-" + md5(episode_type + "|" + founding_event_id)[:24]`,
minted exactly once in `_create_episode`. An episode always has exactly one
founding event (an episode is created the moment `process_event` finds no
compatible open episode for an event) — so no ordering ambiguity ever exists
for the id-defining content.

Growth never recomputes `episode_id`: `_grow_episode` always copies the
input episode's `episode_id` into the new snapshot. This is required for two
properties this design needs:

1. **Stable reference across the episode's life** — a consumer holding an
   `episode_id` from an early snapshot can always find later snapshots of
   the *same* episode.
2. **Replay/live parity for a growing episode** — hashing "current content"
   would make batch and live disagree on an id for the identical underlying
   episode whenever the events interleave differently across live vs. replay
   traversal order. Hashing only the immutable founding content avoids this.

Growth (and lifecycle transitions) are represented the append-only-compatible
way: each growth/transition produces a **new immutable `Episode` object**
with the same `episode_id` and a superset `originating_event_ids`/
`originating_observation_ids` tuple, never a mutation of a prior object.

## Episode lifecycle

```
CREATED -> ACTIVE -> QUIESCENT -> CLOSED
             ^  |         |
             |__|         |
             ACTIVE (self) |
             ^_____________|
            (reactivation before close)
```

`bujji/market_episode/taxonomy.py`'s `VALID_TRANSITIONS`:

| From | Valid To |
|------|----------|
| `CREATED` | `ACTIVE` |
| `ACTIVE` | `ACTIVE`, `QUIESCENT`, `CLOSED` |
| `QUIESCENT` | `ACTIVE`, `CLOSED` |
| `CLOSED` | *(none)* |

Beyond the literal linear 4-state diagram, three transitions are deliberately
added, each documented in `taxonomy.py`'s module docstring:

- **`ACTIVE -> ACTIVE`** (self-transition): growing an already-ACTIVE episode
  does not need a visible state change.
- **`QUIESCENT -> ACTIVE`** ("reactivation before close"): a compatible event
  arriving after an episode has gone quiet, but before it has formally
  closed, reactivates it. This is explicitly different from the forbidden
  `CLOSED -> ACTIVE`.
- **`ACTIVE -> CLOSED`** (direct): `engine.advance_time` allows a single large
  time jump to skip visibly sitting in `QUIESCENT` — this transition is
  already a valid table entry, so a coarse-grained `advance_time` tick (e.g.
  a scheduler that ticks once an hour) still closes correctly in one call.

`CLOSED -> anything` has no entry — enforced structurally (INV-4), not just
documented.

### Time-based transition triggers (`bujji/market_episode/config.py`)

Both are silence durations measured from an episode's `latest_update`:

- `DEFAULT_QUIESCENT_AFTER_SECONDS = 900.0` — no compatible event for 15
  minutes: `ACTIVE -> QUIESCENT`.
- `DEFAULT_CLOSE_AFTER_SECONDS = 1800.0` — a further 30 minutes of silence
  after going quiescent (i.e. 45 minutes of total silence): `QUIESCENT ->
  CLOSED` (or `ACTIVE -> CLOSED` directly, per the coarse-tick case above).

`DEFAULT_PROXIMITY_WINDOW_SECONDS = 300.0` is Deliverable 5's configurable
temporal-proximity window: two events are eligible to group into the same
episode only if their timestamps fall within this many seconds of each
other's episode activity.

## Relationship to Series 75 (Market Events) and MSI (forward-looking)

MEE consumes `bujji.live_market_events.models.MarketEvent` objects by
reference only (`originating_event_ids`) — it never copies a `MarketEvent`'s
`detail` payload, and it never copies an `Observation`'s payload either
(`originating_observation_ids` are transitively collected from each joining
event's own `originating_observation_ids`). MSI (not built in this sprint)
is expected to consume `Episode` objects the same way, through the
`typing.Protocol` stubs in `runner.py` (`MSIEpisodeConsumer`,
`ObservatoryEpisodeConsumer`, `ReplayEpisodeConsumer`,
`QualificationEpisodeConsumer`) — all unwired, in-memory-only defaults for
now, matching Series 74/75's precedent for un-connected future interfaces.

## Replay compatibility (Deliverable 9)

`runner.generate_episodes_for_events` (batch) and
`runner.LiveMarketEpisodeStream` (incremental) both delegate every state
change to the SAME two engine functions, `engine.process_event` and
`engine.advance_time` — there is exactly one grouping/lifecycle
implementation in this package. Parity therefore holds by construction, not
by coincidence, proven in
`tests/test_market_episode_engine.py::test_replay_live_parity` (identical
`episode_id`, `episode_type`, `start_time`, `latest_update`, `end_time`,
`originating_event_ids`, `originating_observation_ids`, and `current_state`
between the two entrypoints for an identical input event sequence;
`provenance.detection_context` differs by design, `REPLAY` vs. `LIVE`,
exactly like Series 75's own event provenance).

## Extension philosophy

Adding a new `episode_type` requires a deliberate addition to
`taxonomy.ALL_EPISODE_TYPES` plus `_EVENT_TYPE_TO_EPISODE_TYPE`, never an
inferred string. Adding a new compatibility rule to `engine.is_compatible`
must remain limited to observation-identity relationships, timestamp
proximity, and event-type family compatibility — never anything resembling
market meaning; any change that would require importing directional/pattern
vocabulary belongs in MSI instead.

## File tree

```
bujji/market_episode/
├── __init__.py
├── taxonomy.py        # EpisodeType constants, lifecycle states, VALID_TRANSITIONS
├── models.py           # Episode, EpisodeProvenance (frozen dataclasses)
├── config.py            # schema version, proximity window, silence-duration thresholds
├── engine.py            # process_event, advance_time, is_compatible — pure functions, invariants documented + enforced
├── serialization.py     # deterministic JSON round-trip
├── query.py              # read-only lookups by id/state/time-range/event/observation
├── runner.py             # generate_episodes_for_events (batch), LiveMarketEpisodeStream (incremental), Protocol stubs
└── journal.py            # MarketEpisodeJournal — append-only JSONL recorder

tests/test_market_episode_engine.py
```
