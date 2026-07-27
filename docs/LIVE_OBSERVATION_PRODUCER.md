# Live Observation Producer Framework v1 (Engineering Series 74)

## Philosophy

**Historical replay and live production produce identical Observation objects. Only the producer changes.**

Series 73B (`bujji/futures_observation/`) and 73C (`bujji/options_observation/`)
ingest historical Bhavcopy CSV rows and, via `bujji.market_observation.engine.build_observation`
(Series 73A), mint immutable `Observation` records. Series 74
(`bujji/live_observation/`) is not a new observation *domain* — it is the
live-production *framework* that will eventually feed that same
Observation Contract from real-time broker events instead of files. A
`FUTURES_UPDATED` live event and a Bhavcopy futures row both end up as
the exact same `FuturesObservation` type, minted by the exact same
`build_futures_observation` function. The only thing that differs is
where the normalized fields came from.

## Step 0 findings — what already exists (disclosed honestly)

Investigation of `bujji/broker/` (real, uncommitted work-in-progress at
the time of writing) found:

- **`bujji/broker/fyers_ws.py`** — a real, working `FyersTickFeed` class.
  It runs the official `fyers_apiv3.FyersWebsocket.data_ws.FyersDataSocket`
  client on a background OS thread and exposes a thread-safe **poll-based**
  "latest price per symbol" store (`latest(symbol)`, `tick_age_seconds(symbol)`,
  `is_connected`, `connect_count`) rather than an async/callback event
  stream. Its own `on_message` handler receives raw payloads shaped
  `{"symbol": ..., "ltp": ..., "type": ...}` and stores only `symbol -> ltp`
  plus a last-tick timestamp — it does not itself construct any typed
  observation model.
- **`bujji/broker/base.py`**, `Broker.live_tick_credentials()` — an
  abstract hook (`(app_id, access_token) | None`) a concrete `Broker`
  can expose so the WebSocket only gets wired where credentials
  concretely exist.
- **`bujji/broker/fyers.py`** — `FyersBroker`, the concrete REST-backed
  implementation used by prior series' MCP tools (`fyers_historical`,
  `fyers_quote`, `fyers_profile`, etc.).

**Conclusion**: a genuine live WebSocket transport already exists
(`FyersTickFeed`), but it is a thin polling store of raw floats, not a
typed event stream and not wired to any Observation-shaped model. There
is no existing Producer abstraction, no existing event taxonomy, and no
existing translation from ticks to `Observation`s anywhere in the
codebase before this series. "Reuse existing infrastructure" therefore
means: reuse the *data shapes* already established (73A's `Observation`
contract, 73B's `build_futures_observation`, 73C's `build_option_observation`)
and design `bujji/live_observation/`'s `ObservationProducer` Protocol so
that a future concrete Producer can wrap `FyersTickFeed` (translating its
polled `latest(symbol)`/`tick_age_seconds(symbol)` reads into
`LiveObservationEvent`s) without this package importing `fyers_apiv3` or
`FyersTickFeed` directly. This sprint does not build that FYERS-backed
Producer — per Deliverable 10's explicit allowance, the demonstration
uses a synthetic, deterministic, in-memory Producer instead. Nothing in
`bujji/broker/` was modified.

## Architecture

```
bujji/live_observation/
    taxonomy.py       Event types, lifecycle states + transition table, aggregation intervals.
    models.py          LiveObservationEvent, ProducerState, AggregationWindow, Tick, LateTick.
    config.py          Schema version, default aggregation interval/source.
    engine.py           Pure functions: event -> Observation translation, window add/close,
                        lifecycle transition validation.
    serialization.py   Deterministic JSON round-trip for events/state/windows.
    query.py           Read-only lookups over recorded events/state.
    runner.py           ObservationProducer Protocol, publication-interface stubs,
                        LiveObservationPipeline, SyntheticEventProducer (reference impl).
    journal.py          Append-only JSONL audit trail.
```

### Producer Interface (Deliverable 2)

`runner.ObservationProducer` is a `typing.Protocol` with
`start()/stop()/subscribe()/unsubscribe()/publish()`. It is structural —
any object with these methods satisfies it, checked via
`@runtime_checkable`. It never imports a broker SDK type; the one
concrete implementation shipped in this sprint,
`runner.SyntheticEventProducer`, drives a fixed, hand-authored sequence
of `LiveObservationEvent`s through a `LiveObservationPipeline`
deterministically — no real broker connection, no `time.sleep`, no wall
clock (every timestamp comes from the event data itself).

### Event model (Deliverable 3)

`taxonomy.ALL_EVENT_TYPES`: `TICK_RECEIVED`, `CANDLE_CLOSED`,
`OPTION_CHAIN_UPDATED`, `FUTURES_UPDATED`, `VIX_UPDATED`,
`CONNECTION_ESTABLISHED`, `CONNECTION_LOST`, `HEARTBEAT`,
`PRODUCER_ERROR`. Only the first five (`TRANSLATABLE_EVENT_TYPES`) carry
a market fact translatable into an `Observation`; the rest are
infrastructure signals.

`models.LiveObservationEvent` is frozen: `event_id`, `event_type`,
`timestamp`, `source`, `payload` (generic — interpreted only by
`engine.translate_event`), `sequence` (monotonic per-producer, for
tracing).

### Translation layer (Deliverable 4)

`engine.translate_event(event)` dispatches on `event.event_type`:

| event_type | delegates to |
|---|---|
| `FUTURES_UPDATED` | `bujji.futures_observation.engine.build_futures_observation` |
| `OPTION_CHAIN_UPDATED` | `bujji.options_observation.engine.build_option_observation` |
| `TICK_RECEIVED`, `CANDLE_CLOSED`, `VIX_UPDATED` | `bujji.market_observation.engine.build_observation` (PRICE / VOLATILITY_VIX — no dedicated 73B/73C-style package exists for these) |
| connection/heartbeat/error | not translated (`None`) |

No file in this package calls `hashlib` — `observation_id` minting stays
the sole authority of `bujji.market_observation.engine.build_observation`,
verified by `tests/test_live_observation_producer.py::TestEventTranslation::test_engine_never_reimplements_id_minting`.

### Aggregation framework (Deliverable 5)

**OHLC-from-ticks is transport, not an indicator — the design reasoning
(disclosed per this series' brief, since the spec left it ambiguous):**

MOF Deliverable 1 draws the Observation / Derived Evidence line at
whether a value is *computed/interpreted* (a choice of formula, lookback,
or smoothing method — a moving average, RSI) versus *recorded* (a raw
fact with exactly one possible value given the inputs). A one-minute
candle's OHLC has exactly one possible value given a set of ticks over a
window: open is definitionally the first tick's price, close the last,
high/low the max/min. There is no formula choice involved. This also
matches 73A's own precedent — `VALUE_KIND_OHLC` is already a first-class
raw `ObservationValue` shape, not something requiring the Evidence
Graph. So: closing an `AggregationWindow` into one `Observation`
(`engine.close_window`) is in scope for this framework. A moving average
or RSI computed over a series of closed windows would NOT be — that
stays MSI/Derived-Evidence work, untouched here.

`models.AggregationWindow` is intentionally thin: `instrument`,
`interval`, `window_start`/`window_end`, `ticks` (raw, accumulated),
`late_ticks`, `is_closed`. `engine.add_tick`/`should_close`/`close_window`
are the only three window operations; none of them compute an
indicator.

**Out-of-window-order tick handling** (disclosed, consistent with 73B's
"never silently drop data" precedent): a tick timestamped before the
window's own `window_start` is never folded into the OHLC computation
(that would silently corrupt the open/close semantics) and is never
dropped either — it is recorded on `window.late_ticks` with reason
`BEFORE_WINDOW_START`, fully disclosed and queryable. Policy on what to
do with a late tick (route to a corrective series, alert, etc.) is left
to the caller; this framework layer only ever discloses it.

### Lifecycle (Deliverable 7)

States: `CREATED, CONNECTING, CONNECTED, STREAMING, RECONNECTING,
STOPPED, FAILED`.

Transition table (`taxonomy.VALID_TRANSITIONS`):

| from | to |
|---|---|
| `CREATED` | `CONNECTING`, `STOPPED` |
| `CONNECTING` | `CONNECTED`, `FAILED`, `STOPPED` |
| `CONNECTED` | `STREAMING`, `RECONNECTING`, `STOPPED`, `FAILED` |
| `STREAMING` | `RECONNECTING`, `STOPPED`, `FAILED` |
| `RECONNECTING` | `STREAMING`, `CONNECTED`, `FAILED`, `STOPPED` |
| `STOPPED` | *(terminal)* |
| `FAILED` | *(terminal)* |

Beyond the spec's literal diagram, this module's own judgment call
(disclosed): `STREAMING -> RECONNECTING` and `RECONNECTING ->
STREAMING/CONNECTED/FAILED` model a feed that drops mid-stream and
either recovers or gives up; `STOPPED` is reachable from every
non-terminal state because a caller must always be able to request a
clean shutdown regardless of where the producer currently sits in its
lifecycle. `STOPPED`/`FAILED` are terminal — a stopped or failed
producer is reconstructed, never resurrected in place.

`engine.is_valid_transition`/`apply_transition` are pure; `apply_transition`
returns a **new** `ProducerState` with the move appended to its
append-only `history` tuple, and raises `ValueError` on an illegal move
rather than silently coercing it.

### Publication interfaces (Deliverable 6 — future, no downstream coupling)

`runner.ObservationRecorder`, `ReplayRecorder`, `MarketStateIndexPublisher`
are `typing.Protocol` stubs. None of the real systems they represent
exist yet, so this package imports none of them; the default
implementations (`InMemoryObservationRecorder`, `InMemoryReplayRecorder`,
`NoOpMarketStateIndexPublisher`) exist only to make
`LiveObservationPipeline` runnable for tests/the demonstration.

## Event flow

```
Producer.publish(event)
    -> LiveObservationPipeline.handle_event(event)
        -> journal.record_event(event)
        -> replay_recorder.record_event(event)
        -> engine.translate_event(event)  ->  Observation | None
        -> journal.record_translation(event, observation)
        -> observation_recorder.record(observation)   (if translated)
        -> msi_publisher.publish(observation)          (if translated)
        -> if TICK_RECEIVED: roll tick into this instrument's AggregationWindow
             -> if boundary crossed: engine.close_window(...) -> Observation
                -> journal.record_window_close(...)
                -> observation_recorder.record(...) / msi_publisher.publish(...)
```

## Replay compatibility

Because `engine.translate_event` calls the identical 73A/73B/73C
builder functions historical ingestion uses, an `Observation` produced
live and an `Observation` reconstructed later from a recorded
`LiveObservationJournal`/`ReplayRecorder` entry for the same underlying
fact are byte-for-byte identical (same `observation_id`, since identity
hashing depends only on identity fields + value, never on `origin`).
`origin` is set to `ORIGIN_LIVE` by default and is the only field that
distinguishes a live-produced Observation from one reconstructed via
replay or historical Bhavcopy ingestion.

## Verification

- New test file alone: `tests/test_live_observation_producer.py` — 42 passed.
- Full suite baseline (before this series, re-measured, not assumed): 2182 passed.
- Full suite after this series: 2224 passed (2182 + 42 new, 0 regressions).
- AST isolation: no import of `mic_v2`, `bujji.mic_replay`,
  `bujji.production_runtime`, `bujji.trading_brain`,
  `bujji.strategy_selector`, or the real FYERS SDK module name
  `fyers_apiv3` (confirmed live at `bujji/broker/fyers_ws.py`:
  `from fyers_apiv3.FyersWebsocket import data_ws`) anywhere in
  `bujji/live_observation/*.py`. No `uuid4()`, no unseeded randomness,
  no `time.sleep()` in the package.
- Deliverable 10 demonstration
  (`TestEndToEndDemonstration::test_tick_tick_tick_to_one_minute_observation`):
  five hand-authored ticks spanning one minute boundary -> five
  individually-translated PRICE Observations + one closed 1-minute OHLC
  Observation (`{"open": 100.0, "high": 102.0, "low": 99.5, "close": 102.0}`)
  + one partial trailing window force-closed into a second OHLC
  Observation -> all seven delivered to the in-memory recorder stub.
  Runs end-to-end with no real broker connection, no wall clock.
