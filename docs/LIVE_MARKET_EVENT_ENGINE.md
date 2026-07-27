# Live Market Event Engine v1 (LMEE v1) — BUJJI Engineering Series 75

`bujji/live_market_events/`

## Philosophy

The Observation Contract (Series 73A/73B/73C) and Live Observation
Producer Framework (Series 74) answer "what was observed." This
package answers exactly one additional question: **what objectively
changed between two observations** — nothing more.

The full chain this sprint sits in:

```
Observation -> Market Event -> Evidence -> Intelligence -> Decision
```

LMEE v1 implements the *Market Event* layer only. It is the first
layer in the chain that reasons across **TIME** (comparing a current
observation to a previous one) but never across **MEANING** (never
judging whether a change is significant, tradeable, bullish, or
otherwise interpretable).

**Market Events describe changes. They never describe meaning.**

A `PriceChanged` event fires identically whether the price moved
because of a routine tick or a flash crash — this module does not know
the difference and must never be asked to. Interpreting *why* a
change happened, or what it implies, is Derived Evidence / Intelligence
/ Decision work — entirely out of scope here, by design.

## Relationship to the Observation Contract (73A–74)

- **73A (`bujji/market_observation/`)**: domain-neutral `Observation` /
  `ObservationSeries`. LMEE consumes these directly — every function in
  `engine.py` takes `Observation` objects built by 73A's own
  `build_observation`, never a hand-rolled dict.
- **73B/73C**: futures/options-specific wrappers around 73A. LMEE does
  not import them directly; it operates on the underlying `Observation`
  shape they also wrap, so it works uniformly across price, futures,
  options, and every other 73A observation domain.
- **74 (`bujji/live_observation/`)**: the live production framework
  that will eventually feed this engine one observation at a time (via
  `runner.LiveMarketEventStream.handle_observation`), the same way
  Series 74's `LiveObservationPipeline` feeds Observations forward.
  LMEE's `MarketEventJournal` and `MarketEventPublisher` Protocol
  deliberately mirror 74's `LiveObservationJournal` and
  `ObservationRecorder`/`MarketStateIndexPublisher` Protocol
  conventions.

## Event lifecycle

For every newly-arrived `Observation` (`current`), LMEE compares it
against, at most, the *immediately previous* `Observation` for the same
series (`previous`), plus — only for the session-high/low family — a
small carried-forward `RunningState`. The single entrypoint
`engine.compare_observations(current, previous, running_state)` composes
independently-testable detector functions (one per event-type family)
and returns `(events, updated_running_state)`.

Event families:

| Family | Needs `previous`? | Needs `RunningState`? |
|---|---|---|
| `OBSERVATION_CREATED/UPDATED/CORRECTED` | yes (created if none) | no |
| `PRICE_CHANGED` / `PRICE_GAP_DETECTED` | yes | no |
| `OI_CHANGED` / `VOLUME_CHANGED` / `VIX_CHANGED` | yes | no |
| `FUTURES_UPDATED` / `OPTION_CHAIN_UPDATED` | yes | no |
| `NEW_SESSION_HIGH` / `NEW_SESSION_LOW` | no (uses state) | **yes** |
| `OBSERVATION_SERIES_GAP_DETECTED` | yes | no |
| `LATE_OBSERVATION_RECEIVED` | yes | no |
| `DUPLICATE_OBSERVATION_DETECTED` | yes | no |

### Design resolution — session high/low vs. "no history beyond what is required"

Deliverable 3 requires comparisons to use "no history beyond what is
required to establish factual change." `NEW_SESSION_HIGH`/`NEW_SESSION_LOW`
appear to violate this at first glance — a running max/min inherently
depends on the whole session, not just the previous observation.

This is resolved the same way Series 74 resolved an analogous tension
for `AggregationWindow`: an OHLC value over a fixed window has exactly
one possible value given its inputs, so it is treated as a raw
Observation *shape* (a fact), not a derived indicator. A running
high/low is the same kind of object — **a single carried-forward fact**
(one float + one observation_id per `(observation_type, instrument)`),
updated by a pure `max`/`min` on each new observation via
`models.SessionExtremesState`. The engine never re-derives it by
scanning the whole series' history; the caller threads
`RunningState` forward one call at a time, exactly like Series 74's
pipeline threads an `AggregationWindow` forward between ticks. This is
minimal accompanying state, not full history replay.

### Design decision — `event_id` content hash

`event_id` is `"MEVT-" + md5(event_type | originating_observation_ids | canonical(detail) | timestamp)[:24]`
(see `engine._event_id`). It deliberately:

- **Excludes** wall-clock-of-detection — re-deriving the same change
  from the same two observations (e.g. rerunning a deterministic
  replay) always produces the same `event_id`.
- **Excludes** `provenance.detection_context` (`LIVE`/`REPLAY`/`BATCH`)
  — the same underlying change detected via batch replay or live
  streaming gets the same `event_id`, which is the basis of the
  replay/live parity guarantee below.
- **Includes** `timestamp`, which is the *observation's own* timestamp,
  never detection time (per MOF's timestamp-ownership discipline) — so
  two structurally-identical changes occurring at genuinely different
  real times (different observation timestamps) get different ids,
  while the same change re-derived from the same inputs never does.

`MarketEvent` never embeds a copy of either Observation's full payload
— it references them via `originating_observation_ids` (their existing
73A `observation_id`s) and carries only the minimal fact-of-change
`detail` payload (e.g. `old_price`/`new_price`/`delta`).

## Replay compatibility

`runner.generate_events_for_series` (batch/replay, walks a whole
`ObservationSeries` pairwise) and `runner.generate_events_for_next_observation`
/ `runner.LiveMarketEventStream` (incremental/live, one observation at a
time) both delegate to the **same** `engine.compare_observations` for
every pairwise comparison. There is exactly one comparison
implementation in this package — parity holds by construction, not by
coincidence. This is proven by
`tests/test_live_market_events.py::TestReplayLiveParity::test_batch_and_incremental_generate_identical_event_ids`,
which feeds an identical `Observation` sequence through both entrypoints
and asserts identical `event_id` sequences.

## Extension rules

- A new `MarketEventType` requires a deliberate addition to
  `taxonomy.ALL_MARKET_EVENT_TYPES`, never an inferred string.
- A new detector function must be independently testable, take
  `(current, previous[, running_state])`, and return `None` /
  `()` / `(events, updated_state)` — never mutate its inputs.
- Detail payloads must stay minimal facts-of-change; never copy a full
  Observation's payload into `MarketEvent.detail`.
- This package must never import `mic_v2`, `bujji.mic_replay`,
  `bujji.production_runtime`, `bujji.trading_brain`,
  `bujji.strategy_selector`, or `fyers_apiv3`, and must never use
  `uuid4()` or unseeded randomness — enforced by
  `tests/test_live_market_events.py::TestIsolation`.
- No bullish/bearish/trending/momentum/compression/expansion/buildup/
  short-covering/long-unwinding/support/resistance/acceptance/rejection
  language anywhere in this package — also enforced by `TestIsolation`.

## What this sprint does NOT connect to

Deliverable 6's publication interface (`runner.MarketEventPublisher`,
mirroring Series 74's `ObservationRecorder`/`MarketStateIndexPublisher`
Protocols) is a broker-neutral `typing.Protocol` stub only. This sprint
connects to none of MSI, Replay, Observatory, or Qualification — those
remain future work, wired in by a later series.
