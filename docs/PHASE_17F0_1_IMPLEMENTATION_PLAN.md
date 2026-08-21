# Phase 17F.0.1 — Reality Capture Foundation: Implementation Plan

**Status: PLAN ONLY. No code beyond the already-prepared, unexecuted
websocket certification script. Awaiting review.**

Goal: Bujji can answer *"what actually happened in the market?"* with
evidence, lineage, and replayability.

---

## Part 1 — Audit Findings

Three audits were run before writing this plan. Two of them changed it.

### 1.1 `FyersTickFeed` consumer audit (objective 3)

Every reference in the codebase, classified by whether it is a real
consumer and whether it depends on lite-mode behaviour:

| Site | Nature | Uses | Lite-mode dependent? |
|---|---|---|---|
| `bujji/app.py:116` | **The only construction site in the codebase** | Constructs with `log_path=`; does **not** pass `litemode`, so inherits the wrapper's `True` default | **No** — never touches message shape |
| `bujji/tick/engine.py` | **Real consumer** | `subscribe()`, `latest(symbol)` | **No** — reads a `float` from the cache |
| `bujji/tick/health.py` | **Real consumer** | `tick_age_seconds(symbol)` | **No** — reads an age, never a payload |
| `bujji/live_observation/runner.py` | Docstring reference; defines its own `TickProducer` protocol | — | No |
| `bujji/live_shadow_operator/operator.py` | Docstring reference | — | No |
| `bujji/live_shadow_operator/safety.py` | Docstring reference | — | No |
| `bujji/live_pipeline_bridge.py` | Docstring reference | — | No |
| `bujji/market_timeseries/subscription.py` | Docstring mention; builds symbol lists only | — | No |

**Decisive finding: zero consumers read raw message dicts.** Verified by
grep for `on_message` / `raw_msg` / `msg[` across both real consumers —
empty. Every consumer reads through `latest()` (a `float`) or
`tick_age_seconds()` (a `float`), both of which `on_message` populates
identically regardless of mode.

**Consequence: the lite→full migration is low-risk at the consumer
level**, because the wrapper normalises to a scalar cache that nothing
bypasses. The risks that remain are non-functional (volume, CPU) plus one
real correctness risk below.

### 1.2 The scaling risk — reduced, not eliminated

The SDK divides price fields by `(10 ** precision) * multiplier`. In lite
mode this is applied explicitly to `ltp`. In full mode, the same division
is applied — but **positionally**, to a subset:

- `depth` branch: fields at index `i < 10`
- `scrips` branch: fields in `precision_calcu_value`
- index branch (what `NSE:NIFTY50-INDEX` uses): fields at index
  `i in [0, 1, 3, 4, 5]` of `index_val`

So whether `ltp` is scaled in full mode depends on its **position** in
the SDK's internal field mapper. It very likely is — but "very likely" is
not a basis for changing the price feed that `TickEngine` reads live
option premiums from. An unscaled `ltp` would be wrong by orders of
magnitude, silently, in a path that touches real position monitoring.

**This must be verified empirically, not reasoned about.** The check is
cheap and decisive: during the certification window, compare the
full-mode websocket `ltp` against a REST `ltp` for the same symbol at the
same moment. Agreement within tick size proves scaling consistency;
disagreement by a power of ten proves the opposite. This is folded into
the certification run (Part 4.1) rather than deferred to migration day.

### 1.3 Layer boundary check (objective 6)

| Layer | Package | Upward dependency? |
|---|---|---|
| Layer 0 | `bujji/market_reality/` | **None** — AST safety test forbids `market_timeseries`, `msi_*`, `intelligence`, broker, execution |
| Layer 1 | `bujji/market_timeseries/` | None — imports `live_observation` only |
| Layer 2 | *(not built)* | — |

No layer moves upward today. The collector introduced by this phase sits
**beside** Layer 0 (it writes into it) and must not be importable from
Layer 0 — enforced by extending the existing safety test.

---

## Part 2 — Files to Create

| File | Purpose |
|---|---|
| `bujji/market_reality/capture_events.py` | `CaptureEvent` model + reasons (objective 4) |
| `bujji/reality_collector/__init__.py` | New package — the collector lives outside Layer 0 |
| `bujji/reality_collector/taxonomy.py` | Collector vocabulary: channel names, counter names |
| `bujji/reality_collector/queue.py` | Bounded queue + overflow accounting |
| `bujji/reality_collector/tick_collector.py` | Websocket → Layer 0 (consumes the new hook) |
| `bujji/reality_collector/quote_collector.py` | REST slow poll → Layer 0 (control channel) |
| `bujji/reality_collector/session.py` | Lifecycle: start, drain, shutdown, counters |
| `scripts/certify_websocket_access.py` | **Already written and deployed, not executed** |
| `scripts/run_reality_capture_session.py` | Operator entry point for the validation session |
| `tests/test_capture_events.py` | `CaptureEvent` model + store integration |
| `tests/test_reality_collector_queue.py` | Bounded queue, overflow, drain |
| `tests/test_reality_collector_session.py` | Counter reconciliation, restart, shutdown |
| `tests/test_reality_collector_safety.py` | AST boundaries; no order/strategy/MSI imports |
| `tests/test_fyers_ws_tick_hook.py` | Hook fires outside locks; no-hook behaviour identical |
| `tests/test_candle_lineage.py` | Candle provenance fields + `calc_version` |

## Part 3 — Files to Modify

| File | Change | Risk |
|---|---|---|
| `bujji/broker/fyers_ws.py` | Add `on_tick(hook)` + invoke hooks **outside both locks**, mirroring the existing `on_connect`/`on_close` pattern (lines ~301/337). Default-absent. Exception-isolated. | **HIGH** — concurrency-critical, documented lifetime proof |
| `bujji/app.py` | Pass `litemode=False` **only after** 1.2 is empirically settled; wire collector as an optional, default-off component | **MEDIUM** — single construction site, but it is the live trading path |
| `bujji/market_reality/taxonomy.py` | `KIND_CAPTURE_EVENT`; `LAYER0_SCHEMA_VERSION` → 1.2.0, recognized `("1.0.0","1.1.0","1.2.0")` | Low — additive |
| `bujji/market_reality/models.py` | `CaptureEvent` import/export surface | Low |
| `bujji/market_reality/store.py` | `append_capture_event()` — same `EventStore`, same ordered log | Low |
| `bujji/market_reality/replay.py` | Ordered **union** stream with discriminator | Low |
| `bujji/market_timeseries/models.py` | Candle provenance fields (Part 6). **No arithmetic change.** | Low |
| `bujji/market_timeseries/store.py` | Provenance columns; PK gains `calc_version` | Low — table is empty, zero migration |
| `tests/test_market_reality_safety.py` | Extend AST bans to cover the collector boundary | Low |
| `bujji/market_observation/taxonomy.py` | **NO CHANGE. MOC stays 1.1.0.** | None |

---

## Part 4 — Objective-by-Objective Plan

### 4.1 Certify the websocket path (objective 1)

`scripts/certify_websocket_access.py` is written, deployed, compile-clean
on the VPS, and verified read-only (zero `place_order`/`cancel_order`/
`modify_order` symbols). Its out-of-hours abort guard was tested live at
16:39 IST: it refused, wrote no artifact, and opened no socket.

It measures, per the objective's list:

| Requirement | How |
|---|---|
| Symbol integrity | Subscribed symbol vs echoed symbol; mismatch = hard `NOT_CERTIFIED` |
| Timestamp availability | Presence of `exch_feed_time` / `last_traded_time` |
| Field availability ratio | Per-key `presence_ratio` over the window |
| Missing fields | `fields_absent_from_every_tick` |
| Ordering | Sequence of received timestamps checked for monotonicity |
| Duplicate ticks | Identical consecutive payload detection |
| Latency (if possible) | `knowledge_time − exch_feed_time`, only when the timestamp exists |

**Addition required before the run** (from 1.2): a paired REST `ltp`
sample at window start and end, recorded in the artifact, so full-mode
scaling is proven rather than assumed. This is a small edit to the
prepared script and is the single most important thing to add before
session day.

**Runs only during NSE hours, by the operator, after their token
refresh.** I will not execute it.

### 4.2 Reality Collector (objective 2)

```
FYERS websocket ──► on_tick hook ──► bounded queue ──► writer thread ──► Layer 0
                         │                                                   ▲
                         └──► existing _ltp cache (UNCHANGED)                │
                                                                             │
FYERS REST ────────► slow quote poll (control channel) ──────────────────────┘
```

- **One websocket**, the existing `FyersTickFeed`. No second feed.
- Socket thread does a non-blocking enqueue and nothing else — never disk,
  never SQLite, never a lock held across I/O.
- Single writer thread drains; Layer 0's `_seen_ids` needs no new locking.
- Quote poll is the **control channel**: ticks silent + quotes arriving
  = feed failure, not market silence. That disambiguation is the reason
  it exists.

### 4.3 Lite/full migration (objective 3)

Sequenced so the risky change happens last and never blind:

1. Certification run in full mode (isolated socket, no production impact).
2. Compare full-mode `ltp` against REST `ltp` — settle 1.2.
3. Only if they agree: flip `bujji/app.py` to `litemode=False`.
4. Verify `latest()` values and `tick_age_seconds()` behaviour are
   unchanged against a lite-mode baseline captured beforehand.

**If scaling disagrees, the migration stops** and the collector runs
lite-mode ticks (price-only capture) until the SDK behaviour is
understood. Capturing less is acceptable; corrupting the live premium
feed is not.

### 4.4 `CaptureEvent` (objective 4)

A capture event is **not** a market observation and does not enter MOC.

| Field | Meaning |
|---|---|
| `event_id` | Content hash of the event's own fields |
| `event_time` | When the **capture condition** occurred (the disconnect happened at T) — not a market event time |
| `knowledge_time` | When the collector recorded it |
| `source` | `fyers` |
| `reason` | `DISCONNECT` / `RECONNECT_RECOVERED` / `QUEUE_OVERFLOW` / `RATE_LIMIT_SKIP` / `AUTH_FAILURE` / `SHUTDOWN_DRAIN_INCOMPLETE` / `COLLECTOR_RESTART` |
| `affected_instruments` | Which symbols were blind |
| `certification_status` | Status at the time of the event |
| `count` | Ticks dropped, polls skipped, etc. where applicable |

Your framing here is better than my earlier one and I've adopted it: a
capture event **does** have its own event time — the disconnect genuinely
happened at a moment. What it does not have is a *market* event time. The
distinction is that it describes the observer, not the market, which is
exactly why it stays out of the market vocabulary.

Stored in the **same** `EventStore`, in the **same ordered log**, with
`event_type = CAPTURE_EVENT`. Replay returns the ordered union — a stream
that silently omits its own blind spots is the artifact this whole design
exists to prevent.

**Hard rule, enforced by test: a materializer must never fold a
`CAPTURE_EVENT` into a candle.**

### 4.5 Candle materialization prerequisites (objective 5)

Provenance only. **No arithmetic change** — `CandleAggregator`'s
first/max/min/last reduction, wall-clock-aligned windows, and refusal to
fabricate empty bars are all untouched.

| New field | Answers |
|---|---|
| `source_observation_ids` | "Which raw observations created this candle?" |
| `first_event_time` / `last_event_time` | Actual observed span, distinct from the nominal window |
| `knowledge_boundary` | The `as_of` knowledge-time this candle was computed under |
| `calc_version` | Which calculation produced it (content hash via existing `epistemics.lineage.calc_version_for()`) |
| `capture_event_overlap` | Gap/capture-event ids overlapping this window — **the flag that stops memory asserting a window it was blind for** |

PK becomes `(instrument, interval, window_start, calc_version)` per your
decision. Table is empty and has zero production importers, so migration
cost is zero.

---

## Part 5 — Components Reused (no new systems)

| Reused | Role | Modified? |
|---|---|---|
| `state_persistence.EventStore` | All durability | **No** |
| `state_persistence` recovery (`RecoveryReport`, `deduplicated_events`) | Restart semantics | **No** |
| `market_reality` (17E) | Layer 0 store, validator, gate, replay, completeness | Additive only |
| `market_observation` | Canonical `Observation`, sole id-minting site | **No** |
| `epistemics.lineage` | `calc_version_for`, `look_ahead_violation`, `Lineage` | **No** |
| `FyersTickFeed` | The one websocket client | +hook only |
| `bujji/broker/fyers.py` | REST for the quote control channel | **No** |
| `CandleAggregator` | Tick→candle arithmetic | **No** |
| `market_timeseries.subscription` | Symbol lists (unused at spot-only scope) | **No** |

**Not created:** another feed, another storage, another event system,
another candle engine, another memory system, another lineage type.

---

## Part 6 — Migration Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | Full-mode `ltp` unscaled → corrupt live premiums | **Critical** | Empirical REST-vs-WS comparison *before* flipping `app.py`; migration blocked on agreement (1.2, 4.3) |
| 2 | Tick hook blocks the socket thread | **Critical** | Hooks invoked outside both locks, mirroring the proven `on_connect` pattern; non-blocking enqueue only |
| 3 | Hook exception kills the feed | High | Catch/count/log/continue — capture degrades, feed survives |
| 4 | Full mode raises message volume → CPU/latency | Medium | Measured during certification; spot-only keeps volume minimal |
| 5 | Collector affects trading path | **Critical** | Separate thread, no shared lock, AST-forbidden imports, default-off |
| 6 | Queue overflow silently loses ticks | High | Record-and-drop with a `CaptureEvent[QUEUE_OVERFLOW]` carrying the count; counter reconciliation proves no silent loss |
| 7 | Websocket uncertified → all ticks rejected | High | Certification is a hard prerequisite (Part 8) |
| 8 | Schema bump breaks existing readers | Low | `1.0.0`/`1.1.0` stay recognized; MOC untouched |

---

## Part 7 — Test Strategy

**Unit**
- `CaptureEvent`: construction, hashing, serialization round-trip, all seven reasons.
- Bounded queue: fill, overflow accounting, drain, shutdown deadline.
- Candle provenance: fields populated, `calc_version` stamped, PK includes it, reads never mix versions.

**Concurrency (the critical suite)**
- Hook fires outside both locks — asserted by attempting a lock acquisition inside the hook without deadlock.
- A raising hook does not stop the feed and does not corrupt cache state.
- With no hook registered, behaviour is identical: existing
  `test_concurrency_lifetime_proof_p3.py`, `test_fault_injection_scenarios.py`,
  `test_tick_silence_watchdog.py`, `test_reconnect_count_metric.py`,
  `test_tick_engine.py` all pass unchanged.

**Integration**
- Synthetic tick stream → collector → Layer 0 → replay → byte-identical ordered stream.
- Counter reconciliation on synthetic input:
  `hook_invocations == enqueued + drops`, `enqueued == written + rejected + queued_at_shutdown`.
- Restart mid-stream: no duplicates, no loss beyond a recorded `CaptureEvent`.

**Safety (AST)**
- Collector imports no `msi_*`, no execution, no strategy, no order path.
- Layer 0 does not import the collector.
- No materializer folds a `CAPTURE_EVENT` into a candle.

**Regression**
- Full suite must stay green. Current baseline: **5,250 passed**.

---

## Part 8 — Certification Dependencies

| Dependency | Status | Blocks |
|---|---|---|
| Spot REST certified (`direct_sdk_fyers_broker_py`) | ✅ `CERTIFIED_AVAILABLE` (2026-08-12) | Quote control channel |
| **Websocket certified (`fyers_websocket`)** | ❌ **Not run** — script prepared, awaiting a market-hours run | **All tick capture.** Uncertified ticks are rejected by the gate, correctly. |
| Full-mode scaling verified | ❌ Not verified | The `app.py` litemode flip |
| Token valid during session | Operator action | The certification run itself |

**Nothing in tick capture can proceed until the websocket certification
runs.** That is the gate behaving as designed, not an obstacle to work
around.

---

## Part 9 — Rollback Strategy

Each step is independently reversible, and the risky ones are last.

| Change | Rollback |
|---|---|
| `on_tick` hook | Remove the registration call. With no hook, the code path is inert — rollback is "stop registering", not "revert the file". |
| `litemode=False` in `app.py` | One-line revert to the default. **The only change touching the live trading path.** |
| Collector | Default-off; disabling it is configuration, not a code change |
| `CaptureEvent` / schema 1.2.0 | Additive; older records stay valid; nothing reads the new kind yet |
| Candle provenance + PK | Table is empty — drop and recreate |
| Layer 0 data | Append-only; a bad session's data is identifiable by session id and can be quarantined without deleting history |

**Kill switch:** the collector is default-off. If anything degrades during
the validation session, disabling it returns the system to exactly its
current state without touching code.

---

## Part 10 — Execution Sequence

| Step | Scope | Gate |
|---|---|---|
| **1** | Add REST-vs-WS `ltp` comparison to the certification script | Compile clean; still read-only; still aborts out-of-hours |
| **2** | **Operator runs websocket certification** during NSE hours | Artifact produced; symbol integrity confirmed; scaling settled |
| **3** | `CaptureEvent` + Layer 0 schema 1.2.0 + union replay | Full regression green |
| **4** | `on_tick` hook in `FyersTickFeed` | All concurrency suites green; no-hook behaviour provably identical |
| **5** | Collector (queue, writer, tick + quote channels, counters) | Counter reconciliation provable on synthetic input |
| **6** | Candle provenance + PK (no arithmetic change) | Existing candle tests unchanged |
| **7** | `litemode=False` flip — **only if step 2 settled scaling** | Live `latest()` values match lite baseline |
| **8** | **Validation session** (spot only, one NSE day) | Five proofs + non-interference + field census |

Steps 3–6 are safe to build before step 2 completes; **step 7 is not**,
and step 8 depends on both.

---

## Part 11 — Gate Status

| Gate | Status |
|---|---|
| Consumer audit (objective 3) | **Complete** — Part 1.1, evidence-based |
| Scaling risk identified | **Complete** — Part 1.2, empirical test defined |
| Collector design (objective 2) | **Complete** — Part 4.2 |
| `CaptureEvent` design (objective 4) | **Complete** — Part 4.4, outside MOC |
| Candle prerequisites (objective 5) | **Complete** — Part 4.5, provenance only |
| Layer boundaries (objective 6) | **Verified** — Part 1.3 |
| Files / risks / tests / rollback / certification deps | **Complete** — Parts 2, 3, 6, 7, 8, 9 |
| Plan review | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |

### The one thing I would flag above all others

The consumer audit came back clean — no consumer reads raw payloads, so
the mode change is safer than expected. But **the SDK scales prices
positionally in full mode**, and `TickEngine` reads `latest()` for live
option premiums. If `ltp` falls outside the scaled index set, premiums
shift by a power of ten, silently, in the live path.

That is the only change in this phase that can hurt trading, and it is
gated behind an empirical check that costs one extra field in the
certification artifact. **Do not let step 7 happen before step 2.**
