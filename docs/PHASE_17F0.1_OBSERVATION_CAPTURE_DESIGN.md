# Phase 17F.0.1 — Observation Capture Design

**Status: ARCHITECTURE ONLY. No code. No implementation.**

Phase 17F.0's hardening audit is accepted, with two operator decisions
recorded: `calc_version` joins the candle primary key, and session one
captures NIFTY spot only.

This document designs the missing Reality Capture path — the link between
the FYERS websocket and Layer 0 that does not currently exist.

> Turn Bujji from a system that sees the market into a system that
> remembers exactly what it saw.

---

## Part 0 — The One Thing This Must Not Break

`bujji/broker/fyers_ws.py` carries a documented concurrency lifetime
proof (`docs/CONCURRENCY_LIFETIME_PROOF_P3.md`) and has **eight live
consumers**: `bujji/tick/engine.py`, `bujji/tick/health.py`,
`bujji/live_observation/runner.py`, `bujji/market_timeseries/subscription.py`,
`bujji/live_shadow_operator/operator.py`, `bujji/live_shadow_operator/safety.py`,
`bujji/live_pipeline_bridge.py`, `bujji/app.py` — plus six test modules
including a fault-injection harness and a reconnect-metric proof.

**The last-value cache is not being replaced, wrapped, or modified.**
`latest()`, `tick_age_seconds()`, `is_connected`, `connect_count`,
`last_error` and the `_ltp` / `_last_tick_at` dictionaries behave exactly
as they do today, byte for byte.

What this phase adds is a **parallel emission path**: a per-tick hook
that fans the same raw payload out to a Layer 0 collector, in addition to
— never instead of — the cache write.

```
FYERS websocket
      │
      ▼
  on_message
      │
      ├──────────────► _ltp[symbol] = ltp      (EXISTING — unchanged)
      │                 cache consumers poll
      │
      └──────────────► tick hooks(raw msg)      (NEW — parallel)
                            │
                            ▼
                      Layer 0 Collector
```

**Acceptance property for the whole phase:** with no tick hook
registered, `FyersTickFeed` must be provably indistinguishable from its
current behaviour. That is the first gate, and it is testable directly —
the existing concurrency, fault-injection and watchdog suites must pass
untouched.

---

## Part 1 — The Emission Hook

### 1.1 Follow the proven pattern, do not invent one

`fyers_ws.py` already solves "run a callback outside the locks" twice —
in `on_connect` (line ~301) and `on_close` (line ~337). Both use the
identical shape:

```
with self._lifecycle_lock:
    ...currency check...
    hooks_to_call = list(self._on_connect_hooks)   # captured under lock
# lock released
for hook in hooks_to_call:
    hook()
```

The tick hook uses **exactly this shape**. Registration mirrors the
existing `on_connect(hook)` / `on_disconnect(hook)` methods (lines
375–379) with an `on_tick(hook)` of the same form.

### 1.2 Required properties

| Property | Requirement |
|---|---|
| **Payload** | The hook receives the **complete raw `msg` dict**, not the two fields the cache extracts. Nothing is filtered before Layer 0 sees it. |
| **Lock discipline** | Hooks are captured under `_lifecycle_lock` and invoked **after both locks are released**. The socket thread must never hold a lock across a queue put, let alone a disk write. |
| **Currency** | The existing generation check runs first, unchanged. A stale-handle callback fires no hooks, exactly as it fires no cache write. |
| **Default-absent** | With no hook registered, the loop body never executes. Zero behavioural change. |
| **Exception isolation** | A hook that raises is caught, counted, logged, and skipped. **A broken collector must degrade capture, never kill the feed.** Capture is subordinate to the feed's survival, not the reverse. |
| **Ordering** | The cache write happens before hooks fire, preserving today's observable ordering for existing consumers. |

### 1.3 One honest note about what a "tick" is here

`on_message` currently returns early when `symbol` or `ltp` is absent,
treating such messages as "connection/subscription ack, not a price
tick". The hook inherits that same definition — it fires only for
messages carrying a symbol and an ltp.

Worth recording explicitly: a FYERS `SymbolUpdate` message is a **feed
update carrying a last-traded price**. It is not necessarily a distinct
trade. Calling these "trades" would be an interpretation, and Layer 0
does not interpret. The observation records what arrived: a feed update,
with whatever fields it carried, at a known moment.

---

## Part 2 — Event Contracts

All five ride the **existing** Layer 0 `RawObservation` shape (17E):
a canonical `market_observation.Observation` (identity + quality +
provenance + value) plus a `Layer0Lineage` block. No new record type is
introduced; these are five populations of one contract, distinguished by
`observation_kind`.

### 2.1 Common fields (every contract, no exceptions)

| Field | Source |
|---|---|
| `observation_id` | Deterministic content hash, minted by `build_observation()` |
| `event_time` | The market event's own timestamp, when the source publishes one (Part 4) |
| `knowledge_time` | When this process received it (Part 4) |
| `source` | `fyers` |
| `access_method` | `fyers_websocket` (ticks) or `direct_sdk_fyers_broker_py` (REST) |
| `symbol` / instrument identity | Instrument, type, and — for derivatives — expiry/strike/right |
| `raw payload` | Exactly what arrived; no defaulting, no coercion |
| `lineage` | Certification status + auditable ref, derived confidence, `transformation_history = (RAW_CAPTURE,)` |

### 2.2 The five contracts

| Contract | `observation_kind` | Source call | Payload (as received) |
|---|---|---|---|
| **TickObservation** | `MARKET_TICK` | WebSocket `SymbolUpdate` | Complete raw msg dict — `ltp` plus every other field present |
| **QuoteObservation** | `QUOTE` | REST `ltp` | `lp`, `bid`, `ask`, `volume`, `open/high/low/prev_close`, `tt`, … |
| **DepthObservation** | `MARKET_DEPTH` | REST `depth` | 5-level `bids`/`asks`, `total_buy_qty`/`total_sell_qty`, **`oi`/`pdoi`/`oi_percent`** |
| **OptionChainObservation** | `OPTION_CHAIN` | REST `optionchain` | Full strike ladder as returned, per-strike OI/volume/LTP/bid/ask |
| **GapObservation** | `GAP` *(new kind, sibling record — §2.3)* | Collector itself | Reason, knowledge-time interval, counts. **No MOC `Observation`, no market value.** |

### 2.3 `GapObservation` is a sibling record, not a market observation

#### DECIDED (operator): MOC stays at 1.1.0. No `TYPE_CAPTURE_GAP`.

This decision is architecturally load-bearing and improves the design, so
its consequences are worked through here rather than treated as a
version-number preference.

**The reasoning it encodes:** a capture gap is not a market observation
domain. `market_observation.ALL_OBSERVATION_TYPES` enumerates *things the
market does* — price, futures, option chain, volatility, depth. "We were
disconnected" is not one of them. Adding it would have made the canonical
market vocabulary describe the observer.

**The consequence: `GapObservation` must not be wrapped in a MOC
`Observation` at all.** The other four contracts are MOC `Observation`s
carrying a market domain type. A gap has no honest value to put there —
`TYPE_UNKNOWN` would be a lie of convenience, asserting it is a market
observation of unknown kind rather than what it is: a fact about capture.

So `GapObservation` becomes a **sibling record type** in the Layer 0
stream:

| Aspect | Market observations | `GapObservation` |
|---|---|---|
| Wraps a MOC `Observation` | Yes | **No** |
| Stored in | Layer 0 accepted `EventStore` | **The same store, same ordered log** |
| `PersistedEvent.event_type` | `MARKET_TICK` / `QUOTE` / … | `GAP` |
| `event_id` | `observation_id` (content hash of identity+value) | Content hash of the gap's own fields |
| Carries lineage | Yes | Yes — source, access_method, knowledge_time |
| Asserts a market fact | Yes | **Never** |

They share the log and the ordering; they do not share the record shape.
That is the accurate modelling of the distinction, and it falls directly
out of your decision.

#### Schema versioning follows the split

- `market_observation.MARKET_OBSERVATION_VERSION` → **unchanged at
  1.1.0.** The market vocabulary gains nothing.
- `market_reality.LAYER0_SCHEMA_VERSION` → **1.1.0 → 1.2.0**, with
  `("1.0.0", "1.1.0", "1.2.0")` recognized. The *Layer 0 record
  vocabulary* genuinely gains a new kind, and a consumer that cannot
  interpret a `GAP` record must be gated out.

This is the first time the two version constants diverge — and that
divergence is correct. They version different things: one the market's
own observable domains, the other Layer 0's record vocabulary. Having
kept them separate in 17E now pays off; a single shared version would
have forced a false choice between lying about the market schema and
under-versioning the capture schema.

### 2.4 A gap is a record about the observer, not the market

`GapObservation` lives in the same ordered Layer 0 stream as market
observations — a replay must encounter gaps *in sequence* with data, or
it will reconstruct a market that never went quiet — but it asserts
nothing about prices.

Two hard rules, both enforced by test rather than convention:

1. **A materializer must never fold a `GAP` record into a candle.**
2. **`replay()` returns the ordered union** of market observations and
   gap records, with an explicit discriminator. A consumer wanting only
   market data filters deliberately; the union is the canonical view,
   because a stream that silently omits its own blind spots is the exact
   artifact this design exists to prevent.

---

## Part 3 — Concurrency Model

### 3.1 Ownership

```
[socket thread]  on_message → hook → queue.put_nowait()     PRODUCER
                                        │
                                   bounded queue            OWNED BY COLLECTOR
                                        │
[writer thread]  drain → validate → Layer 0 append          SINGLE CONSUMER
```

- **The queue is owned by the collector**, never by `FyersTickFeed`. The
  feed knows only that it calls a callable; it holds no reference to a
  queue, a store, or a file. This keeps the concurrency-critical file
  free of new state.
- **Exactly one writer thread** drains the queue. Layer 0's
  `RawObservationStore` maintains an in-memory `_seen_ids` set for
  duplicate detection; a single consumer means no lock is needed around
  it, and no new synchronisation is introduced into Layer 0.
- **The socket thread's only obligation is a non-blocking enqueue.** It
  never touches disk, never touches SQLite, never waits on a consumer.

### 3.2 Backpressure and overflow

The socket thread must never block. Therefore the enqueue is
`put_nowait()`-style, and a full queue must be handled at the moment of
the put.

**Overflow policy — CONFIRMED (operator): record-and-drop.**

When the queue is full, the tick is dropped and an overflow is
**counted**. The dropped tick is not silently lost: on the next
successful write, or at a bounded interval, the collector emits a
`GapObservation` with `reason = QUEUE_OVERFLOW` and the count of ticks
lost.

Rationale: the alternative — blocking the producer — pushes backpressure
onto the socket thread, which would stall the SDK's reader, delay
reconnect handling, and risk starving the *trading* path's own feed
health checks. Priority A states the memory system may never become a new
way for the trading system to fail. **Bounded, visible loss is preferable
to unbounded, invisible risk.**

Queue depth is configuration. Session one (spot only) will not stress it,
which is stated plainly rather than treated as validation.

### 3.3 Shutdown

| Step | Behaviour |
|---|---|
| Stop signal | Feed stops accepting; hook stops enqueuing |
| Drain | Writer drains remaining queued observations, up to a **bounded deadline** |
| Incomplete drain | If the deadline expires with items queued, emit a `GapObservation` with `reason = SHUTDOWN_DRAIN_INCOMPLETE` and the count. **An unclean shutdown is recorded, never assumed clean.** |
| Session close | Emit a session-boundary marker so a replay distinguishes "session ended" from "collector died" |

### 3.4 Isolation from the trading path — restated as a hard constraint

The collector shares **no thread, no lock, and no object** with order
placement or position management. It imports no `msi_*`, no execution
module, no strategy module — enforced by AST safety test, the same
mechanism already guarding `market_reality`.

If capture dies, trading is unaffected. If trading dies, capture is
unaffected.

---

## Part 4 — Bitemporal Correctness

### 4.1 The two times

| Time | Definition | Source |
|---|---|---|
| `event_time` | When the market event occurred | The source's own timestamp, when it publishes one |
| `knowledge_time` | When Bujji received it | Wall clock at the capture boundary |

Layer 0 already stores these as `Layer0Lineage.event_timestamp` and
`capture_timestamp`, distinctly, and **refuses to backfill one from the
other** (17E). This design is what that refusal was for.

### 4.2 Where the clock is read — a deliberate boundary

`bujji/market_reality/` is forbidden from reading the wall clock
(enforced by `test_market_reality_safety.py`, which bans `datetime.now(`,
`time.time(`, `uuid4(`). That constraint stands and is not relaxed.

Therefore: **the collector reads the clock and injects it.** The
collector lives outside Layer 0, stamps `knowledge_time` at the moment of
capture, and passes it in. Layer 0 remains a pure, replay-deterministic
recipient of already-timestamped facts.

`knowledge_time` is stamped **in the hook, on the socket thread, at
arrival** — not in the writer thread after queueing. Stamping it after a
queue delay would silently record when we got around to processing a
tick, not when we learned of it, and would make queue latency invisible.

### 4.3 We do not yet know whether ticks carry an exchange timestamp

This is an honest gap, and the design must not paper over it.

The current `on_message` discards every field except `symbol` and `ltp`,
so **this codebase has never observed what a real FYERS tick payload
actually contains.** Prior documents cite "23 fields including
exch_feed_time," but that figure comes from SDK documentation, not from
our own captured data.

The design therefore handles both cases:

- **If** the payload carries an exchange timestamp → it becomes
  `event_time`.
- **If not** → `event_time` is `null` and `knowledge_time` stands alone.
  Layer 0 already permits a null event time as a legitimate condition,
  with a test asserting it (`test_absent_event_timestamp_is_legal`).

**Session one resolves this empirically** (Part 6.4). Assuming the field
exists and building on it would be exactly the "code existence proves
capability" error this whole Phase-17 arc was created to correct.

### 4.4 The bitemporal query contract (from 17F.0, restated for capture)

A materializer computing a fact stamped at T sets **both** bounds to T:
only events that happened by T, known by T. A late-arriving observation
with `event_time ≤ T` but `knowledge_time > T` is **excluded** — it was
not available at T, and including it is look-ahead.

Capture's obligation is simply to record both times honestly. Everything
downstream depends on that honesty, and nothing downstream can repair its
absence.

---

## Part 5 — Gap Handling

### 5.1 One contract for every form of blindness

Disconnects, overflow, rate-limit skips, token expiry, and unclean
shutdown are all the same fact — *a period during which Bujji could not
observe* — and all become `GapObservation` records differing only by
reason.

| `reason` | Emitted when |
|---|---|
| `DISCONNECT` | Websocket dropped; covers disconnect → reconnect |
| `RECONNECT_RECOVERED` | Connection restored; closes the interval |
| `QUEUE_OVERFLOW` | Queue full; carries the dropped count |
| `RATE_LIMIT_SKIP` | A scheduled REST poll was skipped for budget |
| `AUTH_FAILURE` | Token expired mid-session; capture halted |
| `SHUTDOWN_DRAIN_INCOMPLETE` | Stop deadline expired with items queued |
| `COLLECTOR_RESTART` | Capture process restarted (session-boundary marker) |

### 5.2 A gap has no market event_time

This inverts the usual bitemporal relationship and must be modelled
deliberately.

A gap does not assert that something happened in the market at a
particular instant. It asserts that **during a knowledge-time interval,
no assertion about the market is possible.** So a `GapObservation`
carries:

- `gap_start_knowledge_time`, `gap_end_knowledge_time` (open-ended until
  recovery closes it),
- `reason`, and any count (e.g. ticks dropped),
- **no `event_time`** — claiming one would assert knowledge about a
  period we were blind to.

An unclosed gap (process killed before recovery) remains open-ended, and
that is the honest representation: we do not know when we regained sight,
because we did not.

### 5.3 The consequence that reaches Layer 1 — the most important part of this design

**A candle materialized over a window that overlaps a gap must be flagged
incomplete.**

Without this, memory says *"in the 09:20–09:25 window the high was
24,318"* when the collector was disconnected for four of those five
minutes. The candle is not wrong in the sense of containing false ticks —
every tick it holds is real — but the **claim it implies** ("this is what
happened in this window") is false. That is precisely the class of silent
wrongness this architecture exists to prevent, and it would be invisible
on a chart.

Required consequences:

1. Layer 1 candles gain a **gap-overlap flag** and the gap record ids
   that overlap their window — a natural extension of the
   `source_observation_ids` lineage already specified in 17F.0 Part 2.
2. The Completeness Monitor's missing-interval output must reconcile
   against gap records: an interval with no observations **and** a
   covering gap record is *explained*; one without is *unexplained* and
   is a genuine finding.
3. Level-interaction memory (17F.4) must be able to exclude, or at
   minimum mark, interactions observed across gapped windows. A "touch"
   detected from three ticks during a disconnect is not evidence of the
   same quality as one observed continuously.

**This means gap handling is not a logging nicety — it is a correctness
requirement of the memory layer.** Designing it now, before any candle
exists, is far cheaper than retrofitting it after level memory is built
on unflagged bars.

---

## Part 6 — Validation Session

One NSE session, **NIFTY spot only** (operator decision, 17F.0).

### 6.0 Capture channels: ticks + a slow quote poll

#### DECIDED (operator): the slow quote poll is enabled for session one.

Two channels on one instrument (`NSE:NIFTY50-INDEX`):

| Channel | Kind | Cadence | Purpose |
|---|---|---|---|
| WebSocket ticks | `MARKET_TICK` | Whatever the feed delivers | The thing under test |
| REST quote poll | `QUOTE` | Slow, fixed (proposal: 60s) | **A known denominator, and a heartbeat** |

This is worth more than a completeness denominator, and the extra value
is the reason to keep it:

**The quote poll is a control channel for the tick path.** Tick
frequency is unknown and unbounded — an index may go quiet legitimately,
so "no ticks for 20 minutes" is uninterpretable on its own. A fixed-cadence
quote poll makes it interpretable:

| Ticks | Quotes | Interpretation |
|---|---|---|
| flowing | arriving | Everything healthy |
| **silent** | **arriving** | **The feed is broken, not the market** — a real finding |
| silent | silent | Connectivity or auth failure; expect a `GapObservation` |
| flowing | silent | REST path or rate-limit problem, tick path fine |

Without the second channel, row two is indistinguishable from a genuinely
quiet market. With it, the tick path's silence becomes diagnosable
evidence rather than ambiguity.

`expected_count` for the quote channel is deterministic
(`session_minutes / 60s`), giving the Completeness Monitor something real
to measure. The tick channel's completeness remains descriptive — it is
measured, not scored against an assumed rate, because that rate is one of
the things session one exists to discover.

**Both channels write through the same certification gate.** Spot is
`CERTIFIED_AVAILABLE` for `direct_sdk_fyers_broker_py` (REST); the tick
channel's `access_method` is `fyers_websocket`, which is a **different
access path and therefore a different certification subject.** Per 17E's
gate design, a certification of one access method says nothing about
another — so either the websocket path is certified before session one,
or its writes are rejected. **This must be resolved before 17F.0.1f
(Part 9, item 3);** discovering it at 15:30 on session day would waste
the session.

### 6.1 The five required proofs

| # | Proof | Pass criterion |
|---|---|---|
| 1 | **No silent tick loss** | Every tick the hook received is either persisted in Layer 0, recorded as a rejection, or accounted for by a `GapObservation` overflow count. Hook-invocation count, enqueue count, dequeue count, write count and drop count **must reconcile exactly**. This is the proof that gives the objective its name. |
| 2 | **Deterministic replay** | Two independent replays yield byte-identical ordered streams; a replay after restart matches one before it; `ReplayReport` counts reconcile with capture-time counters. |
| 3 | **Restart recovery** | Kill the collector mid-session and restart. No duplicates, no loss beyond the recorded gap, `COLLECTOR_RESTART` marker present, `_seen_ids` correctly rebuilt (already proven in 17E unit tests; here proven against live data). |
| 4 | **Duplicate handling** | Re-append a sample of captured observations verbatim: every one returns `DUPLICATE`, record count unchanged, rejection log unpolluted. |
| 5 | **Rejected observation tracking** | Inject known-bad candidates (forbidden derived field, missing required field, uncertified source). Each lands in the rejection store with payload hash, reasons, validator version, timestamp, source — **and is absent from the accepted store**. |

### 6.2 Counter reconciliation — the specific arithmetic

Proof 1 is only meaningful if stated as an equation checkable after the
fact:

```
hook_invocations
  == observations_enqueued + enqueue_drops
observations_enqueued
  == observations_written + observations_rejected + queued_at_shutdown
enqueue_drops
  == sum(GapObservation[QUEUE_OVERFLOW].dropped_count)
```

Any imbalance is a failure, and the size of the imbalance localises the
leak. "Roughly matched" is not a pass.

### 6.3 Non-interference proof (priority A)

The existing runtime shows **zero behavioural difference** with the
collector running: no new errors, no changed decision output, no latency
beyond noise. Cache consumers (`latest()`, `tick_age_seconds()`,
watchdog) behave identically. If capture affects trading measurably, the
session fails regardless of how well capture performed.

### 6.4 Session one's second deliverable: a tick payload field census

Because this codebase has never seen a full tick payload (Part 4.3),
session one must produce a **census of the fields FYERS actually sends**:
which keys appear, how often, which are ever null, and — critically —
whether an exchange timestamp is present.

This is a genuine discovery, not a formality. It determines whether
`event_time` is available for ticks at all, and therefore how much of the
bitemporal contract is real versus aspirational for the tick path. It
also finally replaces the documentation-sourced "23 fields" claim with
observed fact.

### 6.5 Explicit non-goals

No materialization, no candles, no level memory, no queries, no "did we
learn anything". Session one answers one question: **can Bujji observe
and remember one day of one instrument, correctly and provably?**

---

## Part 7 — Reuse and Prohibitions

### 7.1 Reused unmodified

| Component | Role |
|---|---|
| `state_persistence.EventStore` | All durability — append-only JSONL, flush+fsync, torn-line tolerance |
| `market_reality` (17E) | Layer 0 store, validator, certification gate, replay, completeness monitor |
| `market_observation` | Canonical `Observation`, `build_observation()` — sole id-minting site |
| `epistemics.lineage` | Lineage contract and `look_ahead_violation()` |
| `FyersTickFeed` | The feed itself — connection lifecycle, reconnect, generation safety, cache |
| `bujji/broker/fyers.py` | REST calls for quote/depth/chain collectors — including the `depth` action wired this phase |
| `market_timeseries.subscription` | Symbol-list construction (unused in session one's single-symbol scope, reused when widening) |
| Existing recovery framework | `RecoveryReport` three-state vocabulary, `deduplicated_events` semantics |

### 7.2 Additive extension only

| Component | Change | Risk |
|---|---|---|
| `FyersTickFeed` | `on_tick(hook)` registration + hook invocation outside locks | **HIGH** — own gated step, dedicated concurrency tests, must prove no-hook behaviour is unchanged |
| `market_reality/taxonomy.py` | `KIND_GAP` + gap reasons; `LAYER0_SCHEMA_VERSION` → 1.2.0 | Low — additive, prior versions stay recognized |
| `market_reality/models.py` | `GapRecord` sibling type | Low — additive |
| `market_reality/store.py` | `append_gap()` | Low — additive; same `EventStore` |
| `market_reality/replay.py` | Ordered union stream + discriminator | Low — additive |
| `market_observation/taxonomy.py` | **No change. MOC stays at 1.1.0.** | None |

### 7.3 Must not be created

- **Another feed.** `FyersTickFeed` is the only websocket client.
- **Another storage.** `EventStore` + Layer 0. No new persistence.
- **Another event system.** The five contracts are populations of the
  existing `RawObservation`; no parallel event bus, no new envelope.
- **Another cache.** The last-value cache stays as-is and is not
  duplicated for collector convenience.

---

## Part 8 — Implementation Sequence

| Step | Scope | Gate |
|---|---|---|
| **17F.0.1a** | `on_tick` hook in `FyersTickFeed` — additive, default-absent, fired outside locks, exception-isolated | All existing concurrency/fault-injection/watchdog/reconnect tests green; provable no-op when unregistered |
| **17F.0.1b** | `KIND_GAP` + `GapRecord` sibling type + union replay + `LAYER0_SCHEMA_VERSION` → 1.2.0 (**MOC untouched**) | Full regression green; 1.0.0/1.1.0 records still valid; gap never folds into a candle |
| **17F.0.1c** | Collector: bounded queue, single writer, `knowledge_time` stamping, counter instrumentation | Counter reconciliation (6.2) provable on synthetic input |
| **17F.0.1d** | Gap emission for all seven reasons | Each reason reproducible on demand and recorded |
| **17F.0.1e** | REST quote collector **enabled** (slow poll, §6.0); depth/chain collectors built but dormant | Quote channel produces a deterministic `expected_count` |
| **17F.0.1x** | **Websocket access-method certification** (§6.0) — resolve before session day | `fyers_websocket` either certified, or a decision recorded on how tick writes are gated |
| **17F.0.1f** | **Validation session** (Part 6) | Five proofs + non-interference + field census |

---

## Part 9 — Decisions, and What Remains Open

### Decided — all prior open questions now closed

| Item | Resolution |
|---|---|
| Gap-marker representation (17F.0 Q5) | **Resolved** — `GapObservation` is a first-class Layer 0 record in the same ordered stream. Parts 2.3, 5. |

| Overflow policy | **Confirmed: record-and-drop**, loss surfaced as `GapObservation[QUEUE_OVERFLOW]` with the dropped count. Part 3.2. |
| Schema versioning | **MOC stays at 1.1.0.** No `TYPE_CAPTURE_GAP`. `GapObservation` becomes a sibling record; `LAYER0_SCHEMA_VERSION` → 1.2.0 alone. Part 2.3. |
| Session-one quote poll | **Enabled** — slow fixed-cadence `QUOTE` alongside ticks, serving as both denominator and control channel. Part 6.0. |

### Newly surfaced — needs resolution before session day

**The websocket is a different certification subject than the REST path.**

Layer 0's gate certifies a `(access_method, instrument_type)` pair, and
17E deliberately made a certification of one access method say nothing
about another — that rule exists because conflating access paths is what
the MCP connector incident proved dangerous.

Session one's two channels use two different access methods:

| Channel | `access_method` | Certification status today |
|---|---|---|
| REST quote poll | `direct_sdk_fyers_broker_py` | **`CERTIFIED_AVAILABLE`** (spot, 2026-08-12) |
| WebSocket ticks | `fyers_websocket` | **No artifact exists → `CERTIFICATION_MISSING`** |

**As designed, every tick would be rejected at the gate.** The gate would
be behaving exactly correctly; the certification simply does not exist
yet.

Three ways forward, in my order of preference:

1. **Certify the websocket path** — extend the certification framework
   with a websocket observation check (symbol echo, timestamp validity,
   field presence) and produce a real artifact. Most consistent with
   every principle established so far, and it converts session one's
   field census into a certification input rather than a side note.
2. **Run session one with ticks writing to the rejection store on
   purpose**, treating it as a certification dry-run. Honest, and it
   still yields the field census — but proof #1 (no silent tick loss)
   becomes a proof about rejections, not captures.
3. Widen the gate to treat websocket as covered by the SDK certification.
   **I recommend against this** — it is precisely the access-path
   conflation the gate was built to prevent.

This needs your decision before 17F.0.1x. Discovering it on session
morning would cost the session.

---

## Part 10 — Gate Status

| Gate | Status |
|---|---|
| Parallel emission path design | **Complete** — Parts 0–1 |
| Five event contracts | **Complete** — Part 2 |
| Concurrency model | **Complete** — Part 3 |
| Bitemporal correctness | **Complete** — Part 4 |
| Gap handling + Layer 1 consequence | **Complete** — Part 5 |
| Validation session protocol | **Complete** — Part 6 |
| Reuse audit | **Complete** — Part 7 |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |

### The honest summary

The capture path does not exist because the tick data is discarded at the
socket callback before anything could see it. This design adds one hook,
one queue, one writer, and one new record kind — and refuses to touch the
cache, the lifecycle, or the trading path.

The hardest part is not the plumbing. It is Part 5.3: **a candle built
over a window we were disconnected for must say so**, or the memory layer
will confidently remember a market that was never observed.
