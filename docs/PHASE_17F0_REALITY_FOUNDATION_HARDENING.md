# Phase 17F.0 — Reality Foundation Hardening

**Status: AUDIT AND DESIGN ONLY. No code. No implementation.**

Phase 17F's design audit is accepted. Before any materializer is written,
this phase hardens the foundation those materializers will stand on.

> Before teaching Bujji to interpret markets, prove that Bujji can
> remember markets correctly.

---

## Part 0 — The Blocking Discovery

Auditing the production capture path for objective (C) surfaced a finding
that changes the collector design fundamentally, and it must be stated
before anything else:

### `FyersTickFeed` is a last-value cache, not a tick stream.

Verified by direct source read (`bujji/broker/fyers_ws.py`):

```python
def on_message(msg: dict) -> None:
    symbol, ltp = msg.get("symbol"), msg.get("ltp")
    if symbol is None or ltp is None:
        return
    ...
    self._ltp[symbol] = float(ltp)          # overwrite, not append
    self._last_tick_at[symbol] = time.time()
```

Three consequences, all confirmed:

1. **Only `symbol` and `ltp` survive.** Every other field in the FYERS
   full-mode tick payload — bid, ask, volume, OI, exchange feed time,
   last traded quantity — is discarded at this line. This was already
   known (17B flagged "21 of 23 fields dropped"); what was *not* known is
   the second point.
2. **There is no per-tick consumer hook.** The only hooks that exist are
   `_on_connect_hooks` and `_on_disconnect_hooks` (verified by grep).
   Nothing in the class emits a tick to a listener.
3. **Consumers poll a dictionary.** Because `_ltp[symbol]` is overwritten
   in place, **every tick between two polls is permanently lost.** A poll
   at 09:20:00 and another at 09:20:05 sees two prices; the market may
   have produced forty.

**Therefore: a tick collector cannot be built by "subscribing" to the
existing feed. The emission point does not exist.** Phase 17F.0 must add
one.

This is not a small change. `fyers_ws.py` carries a documented
concurrency lifetime proof (`docs/CONCURRENCY_LIFETIME_PROOF_P3.md`) with
a two-lock discipline (`_lifecycle_lock` outer, `_lock` inner, never
reversed) and generation-based callback invalidation. A tick hook added
carelessly — fired while holding either lock — would block the SDK's
socket thread on downstream disk I/O and could deadlock the feed. The
design in Part 4 addresses this explicitly.

**Honest restatement of project status:** Layer 0 exists and is correct,
but *nothing can currently write ticks into it*, because the tick data
never reaches a point where it could be captured. That is the single
highest-priority item in this phase.

---

## Part 1 — Layer 0 → Layer 1 Dependency Chain Verification

### 1.1 Current, verified state

| Link | Status | Evidence |
|---|---|---|
| Broker → Layer 0 | **BROKEN — no path exists** | No collector; `FyersTickFeed` has no emission hook (Part 0) |
| Layer 0 store | **SOUND** | 17E: immutable, gated, deduplicated, replayable, 78 tests |
| Layer 0 → Layer 1 | **BROKEN — no path exists** | `CandleAggregator.ingest()` is fed by an arbitrary caller; nothing connects it to Layer 0's replay stream |
| Layer 1 store | **SOUND but unwired and empty** | `CandleStore` exists, correct immutability discipline, zero `.db` file on disk, zero production importers |
| Layer 1 → Layer 2 | **Not built** | Correct — 17F scope |

**Verdict: the chain is correctly *ordered* but not yet *connected*.**
Layers 0 and 1 are both individually sound and share no wiring. 17F.0
builds the two missing links (broker→L0, L0→L1) and hardens the record
schema that flows across them.

### 1.2 Direction check — no inversions between L0 and L1

Verified by import inspection:

- `bujji/market_reality/` imports `state_persistence`, `market_observation`
  — both *below or beside* it. It imports nothing from Layer 1+.
  Enforced by `test_market_reality_safety.py`'s AST scan, which explicitly
  forbids `bujji.market_timeseries` among others.
- `bujji/market_timeseries/` imports `live_observation` only. It does not
  import Layer 0 today (that is the missing link, not an inversion).

**No dependency inversion exists between Layer 0 and Layer 1.** The
inversions identified in the 17F audit (`context_window` sitting on
derived intelligence) are at Layer 3 and are untouched by this phase.

### 1.3 One boundary risk inside Layer 1

`bujji/market_timeseries/indicators.py` lives inside the Layer 1 package.
Indicators are Layer 3+ concerns. Nothing in the materialization path may
import it, and the 17F safety test must forbid it — otherwise the layer
boundary erodes from inside the package that defines it.

---

## Part 2 — Objective A: Candle Lineage

### 2.1 Current `Candle` (verified exact shape)

```
instrument, kind, interval, window_start, window_end,
open, high, low, close, volume, tick_count, open_interest, schema_version
```

It records **what** the bar was. It records **nothing** about where the
bar came from. Phase 17D Part 2.1 requires every derived record to carry
its inputs' lineage forward; a `Candle` cannot do that today, so
`REPLAY_VERIFIED` is unprovable.

### 2.2 Required additions

| Field | Purpose |
|---|---|
| `source_observation_ids` | The exact Layer 0 `observation_id`s folded into this bar. The chain that makes the rebuild proof resolvable back to raw facts. |
| `materializer_id` | Which materializer produced it (e.g. `tick_to_candle`). |
| `calc_version` | Content hash of the calculation, via the existing `epistemics.lineage.calc_version_for()`. Changing the aggregation logic changes this automatically — nobody can forget to bump it. |
| `first_event_time` / `last_event_time` | **Actual observed event-time span**, distinct from the nominal `window_start`/`window_end`. |
| `input_schema_version` | The Layer 0 schema version the inputs carried, so a mixed-version rebuild is detectable. |

### 2.3 Why `first_event_time`/`last_event_time` matter (not redundant with the window)

`window_start`/`window_end` are **nominal wall-clock boundaries**
(09:15:00–09:20:00). They say what window the bar *represents*, not what
was *observed*. A bar whose ticks all arrived between 09:15:02 and
09:15:09 and a bar with ticks spread evenly across the full five minutes
are materially different evidence — and today they are indistinguishable
except by `tick_count`, which conflates density with span.

Recording the real observed span makes "we saw the whole window" and "we
saw the first nine seconds and then the feed went quiet" different,
inspectable facts. For level-interaction memory, that difference decides
whether a touch was genuinely observed or merely inferred from a sliver.

### 2.4 Storage implications

`CandleStore`'s existing discipline is preserved exactly: idempotent on
identical content, `ConflictingCandleError` on differing content at the
same key, never a silent overwrite. The new columns are additive.

**Immutability caveat:** a re-materialization under a *new* `calc_version`
legitimately produces a different candle for the same
`(instrument, interval, window_start)`. Under today's rules that raises
`ConflictingCandleError` — a legitimate recomputation would be
indistinguishable from data corruption.

#### DECIDED (operator, this phase): `calc_version` joins the primary key.

```
PRIMARY KEY (instrument, interval, window_start, calc_version)
```

Consequences, all of which must be designed for explicitly:

**(a) `ConflictingCandleError` becomes sharper, not weaker.** It now
raises only when the *same* calculation, over the *same* window,
produces *different* content. That is a genuine contradiction and always
indicates a real problem — a non-deterministic materializer, or corrupted
inputs. Two calculation versions disagreeing is expected and is no longer
an error; the same calculation disagreeing with itself never is.

**(b) Every read must resolve a version — and must never mix them.**
This is the non-obvious consequence and the one that matters most. A
series assembled from bars computed under different rules is not a series;
it is a silent splice of two incompatible definitions, and it would be
invisible in a chart. Therefore:

- Read methods take an explicit `calc_version`, defaulting to the
  materializer's **current** version resolved at call time (deterministic,
  never "whatever was written last").
- A single result set may contain **exactly one** `calc_version`. If a
  requested range has bars under more than one version, the store returns
  the requested version's bars and **reports the shortfall explicitly**
  (which windows are missing at that version) rather than quietly filling
  from another version.
- Silently mixing versions in one result is a defect by definition, and
  gets a dedicated test.

**(c) Migration cost: zero.** Verified — no `market_timeseries` `.db`
file exists anywhere on disk, and the store has zero production
importers. There is no historical candle data to migrate, so the primary
key changes before the table is ever populated. This schema decision is
free now and would be expensive later; making it at this moment is the
cheapest it will ever be.

**(d) Storage growth is bounded by intent.** Keeping N calculation
versions multiplies row count by N. That is the point — it is what makes
a rule change auditable and comparable — but it means old versions should
be prunable by explicit operator action, never by automatic cleanup. A
pruning policy is not needed for session one and is deferred.

---

## Part 3 — Objective B: The `as_of` Contract

### 3.1 The naive version is wrong

The obvious contract — "return records whose event time ≤ `as_of`" — is
insufficient, and using it would produce memory that is subtly,
invisibly wrong.

Consider: an observation with `event_timestamp = 09:20:00` that was
*captured* at `09:24:30` (a late-arriving tick, a delayed poll, a
reconnect backfill). A query "as of 09:21:00" filtering only on event
time would **include** it — using information that did not exist at
09:21:00. That is look-ahead, and it is the exact failure mode that makes
historical memory look better than reality.

### 3.2 The contract: bitemporal

Layer 0 already stores both timestamps distinctly and refuses to backfill
one from the other (17E, `Layer0Lineage.event_timestamp` /
`capture_timestamp`). That was deliberate, and this is what it was for.

**A historical query is scoped by two bounds:**

| Bound | Meaning |
|---|---|
| `as_of_event_time` | Only events that *happened* at or before this instant. |
| `as_of_knowledge_time` | Only observations that were *captured* at or before this instant. |

A memory query asking "what could Bujji have known at time T about the
market up to time T" sets **both to T**. This is the default and the only
form a materializer may use when computing a fact stamped at T.

Analytical/forensic queries may set them differently on purpose (e.g.
"everything we now know about what happened up to 09:20") — but that must
be an explicit, deliberate act, never the default, and results so
obtained must never feed a materializer.

### 3.3 Enforcement rules

1. **`as_of` is a required parameter** on every Layer 1 and Layer 2 read.
   Not optional-with-a-default — omission must be a signature error, so
   look-ahead becomes impossible to write rather than something reviewers
   must catch. (This is Phase 16A's own design, finally implemented.)
2. **Materializers receive an `as_of`-scoped read view, never raw store
   access.** A materializer cannot widen its own window.
3. **`epistemics.lineage.look_ahead_violation()` already exists** and
   compares a source event time against an artifact's `as_of`. Reuse it
   as the assertion primitive rather than writing a second comparator.
4. **A no-look-ahead property test** is the acceptance gate: a fact
   computed `as_of` T must be byte-identical whether computed at T or a
   year later with far more data present.

---

## Part 4 — Objective C: Collector Architecture

```
FYERS
  │
  ├── WebSocket (ticks)      ──┐
  ├── REST quotes            ──┤
  ├── REST depth             ──┼──► COLLECTORS ──► CertificationGate ──► Layer 0
  └── REST optionchain       ──┘                    (fail closed)
                                                        │
                                                        ▼
                                                  MATERIALIZERS (17F)
```

Every collector shares one contract: **capture and forward, interpret
nothing.** A collector may resolve identity (expiry/strike from the
instrument master) because that is a lookup, not a transformation. It may
not compute, filter on value, smooth, or discard "implausible" data.

### 4.1 Tick Collector — requires the new emission hook

**Prerequisite (Part 0): add a per-tick hook to `FyersTickFeed`.**

Design constraints, driven by the file's existing concurrency proof:

- The hook receives the **complete raw tick dict**, not the two fields
  `on_message` currently extracts.
- It must fire **outside both locks**. The socket callback thread must
  never block on disk I/O; holding `_lifecycle_lock` across a Layer 0
  append would stall reconnect handling and risks deadlock.
- Pattern: inside the existing locks, perform the currency check and
  copy the payload; release; then invoke hooks — precisely the shape
  `on_connect` already uses (`hooks_to_call = list(...)` captured under
  the lock, invoked after release). **Follow that existing, proven
  pattern rather than inventing a new one.**
- The hook must be **additive and default-absent**: with no hook
  registered, `FyersTickFeed` behaves exactly as it does today. Existing
  consumers (`bujji/tick/engine.py`, `live_observation/runner.py`,
  `live_pipeline_bridge.py`, `live_shadow_operator/`, `app.py`) must be
  bit-for-bit unaffected.
- A hook raising an exception must never kill the feed: catch, count,
  log, continue. A broken collector must degrade capture, not trading.

**Buffering:** the socket thread hands ticks to a bounded in-memory queue;
a separate writer drains it into Layer 0. If the queue saturates, the
overflow is **counted and recorded as a rejection-class event** — never
silently dropped. A silent drop would make Layer 0 quietly incomplete,
which is worse than a visible gap.

### 4.2 Quote Collector

Polls `ltp` on a configured interval for instruments where a tick
subscription is unavailable or as a cross-check against the tick stream.
Emits `QUOTE`-kind observations. Interval is configuration, not a
constant.

### 4.3 Depth Collector

Polls `depth` — **the only endpoint carrying futures OI** (established
live 2026-08-12, now wired into `get_futures_quote()`). Emits
`MARKET_DEPTH` observations carrying the full 5-level book plus
`oi`/`pdoi`/`oi_percent`.

Rate limiting is a first-class concern: `depth` is per-symbol, so a wide
instrument set multiplies request count. The collector must respect a
configured budget and **record when it skipped a poll due to budget**, so
a gap in depth history is attributable rather than mysterious.

### 4.4 Option Chain Collector

Polls `optionchain` per underlying, emitting one `OPTION_CHAIN`
observation per poll containing the full strike ladder as returned. Strike
count is configuration; `subscription.py`'s existing
`DEFAULT_STRIKES_EACH_SIDE = 3` band logic is reusable for deciding
*which* strikes to tick-subscribe, but the chain snapshot itself should
capture the broader ladder the endpoint already returns.

### 4.5 Failure handling

| Failure | Required behaviour |
|---|---|
| WebSocket disconnect | Existing reconnect logic handles it. The collector records a **capture gap marker** so replay can distinguish "market was silent" from "we were disconnected". This distinction is essential and is currently unrepresentable. |
| Token expiry mid-session | `AuthenticationError` propagates (never swallowed). Capture stops; a gap marker is written; the operator is alerted. Layer 0 must never receive fabricated data to "cover" the gap. |
| Certification demotion mid-session | The gate re-reads status live per write (17E design). Writes stop immediately for that instrument. Rejections are recorded, so the demotion is visible in the data. |
| Rate limit (429) | Back off; record a skipped-poll marker. Never retry so aggressively that the trading path's own broker calls are starved. |
| Queue saturation | Record overflow count as a rejection-class event. Never silently drop. |
| Malformed payload | Validator rejects it into the rejection store with a reason. It is a permanent, attributable fact. |
| Disk full | Fail loudly and stop capture. A partially-written Layer 0 is acceptable (append-only, torn-line tolerant); a silently-truncated one is not. |

### 4.6 Restart recovery

Reuse the existing, proven recovery pattern (`state_persistence`,
`position_lifecycle/recovery.py`): replay the append-only log, deduplicate
by id, count malformed/schema-mismatched records, return a three-state
report.

For Layer 0 specifically this is already implemented — `RawObservationStore`
rebuilds `_seen_ids` on construction, and `replay_with_report()` returns
the diagnostics. **A restart mid-session therefore resumes without
duplicating and without losing prior observations**, already verified by
17E's `test_duplicate_detection_survives_restart`.

What must be added: a **session-boundary marker** so a replay can tell
"the collector restarted at 11:42" from "the market was quiet at 11:42."
Same principle as the disconnect gap marker.

### 4.7 Isolation from the trading path — non-negotiable

The collector runs **beside** the existing runtime, never inside its
decision loop. It must not:

- share a thread or lock with order placement or position management;
- be able to block, slow, or fail the trading path;
- import any `msi_*`, execution, or strategy module.

If capture dies, trading continues unaffected. If trading dies, capture
continues unaffected. **Production safety is priority A, and it means the
memory system may never become a new way for the trading system to
fail.**

---

## Part 5 — Objective D: Reality Validation Session Protocol

One NSE session (09:15–15:30 IST).

#### DECIDED (operator, this phase): session one captures NIFTY spot only.

Scope: **`NSE:NIFTY50-INDEX`, a single instrument, tick + quote capture.**
No VIX, no futures, no options, no depth, no option chain.

Three things follow from this, worth stating so the session's results are
read correctly:

- **It isolates the variable under test.** Session one is not testing
  market coverage; it is testing whether the capture→store→replay→rebuild
  chain works at all. One instrument makes any failure attributable to
  the mechanism rather than to load, symbol resolution, or rate limits.
- **It is consistent with the certification gate.** Futures and options
  are `CERTIFIED_AVAILABLE` and *would* be gate-eligible; holding them
  back is a deliberate scoping choice, not a certification limit. India
  VIX, by contrast, is genuinely blocked — Phase 17A.5 never produced a
  VIX certification artifact, so `INSTRUMENT_INDEX` resolves to
  `CERTIFICATION_MISSING` and the gate would reject it anyway. Spot-only
  and certification agree here; that agreement is a useful check that the
  gate behaves as designed.
- **It under-tests load, deliberately.** One index symbol produces far
  fewer ticks than a 16-series option band. Queue sizing, rate limits and
  disk throughput are therefore **not** validated by session one, and no
  conclusion about them may be drawn from it. Widening scope after the
  five proofs pass is what exercises those.

The Completeness Monitor's `expected_count` for the session should be
derived from the configured quote-poll interval, not from an assumption
about tick frequency — an index's tick rate is an observation, not a
known constant, and session one is partly how we find out what it is.

### 5.1 Before the session

- FYERS token refreshed (operator action).
- Certification artifacts present and `CERTIFIED_AVAILABLE` for the
  instruments being captured; the gate fails closed otherwise.
- Layer 0 directory empty or at a known baseline; baseline record count
  recorded.
- Collector configured; confirmed to hold **no order permissions** and no
  import path to any execution module.
- Existing trading/shadow runtime state captured, so post-session
  comparison can prove non-interference.

### 5.2 During the session

Capture only. No materialization, no queries, no interpretation. Record
operationally: reconnect count, queue high-water mark, rejection count,
skipped-poll count, and any gap markers.

### 5.3 After the session — the five proofs

| # | Proof | Pass criterion |
|---|---|---|
| 1 | **Observations captured** | Layer 0 record count > 0, spanning the session; Completeness Monitor reports per-instrument expected vs received with named missing intervals. A `DEGRADED` result is an acceptable *pass* if the gaps are attributable to recorded disconnect/skip markers — the requirement is honesty, not perfection. |
| 2 | **Duplicates handled** | Re-append a sample of captured observations verbatim; every one returns `DUPLICATE`, record count unchanged, rejection log unpolluted. |
| 3 | **Rejections recorded** | Inject known-bad candidates (forbidden derived field, missing required field, uncertified source). Each lands in the rejection store with payload hash, reasons, validator version, timestamp, source — **and is absent from the accepted store**. |
| 4 | **Replay works** | Two independent replays yield byte-identical ordered streams. A replay after process restart matches one before it. `ReplayReport` counts reconcile with capture-time counters. |
| 5 | **Candles rebuild correctly** | Materialize candles from Layer 0; delete them entirely; re-materialize; results byte-identical. Every candle's `source_observation_ids` resolve to records still present in Layer 0. Independently, spot-check a sample of materialized candles against FYERS' own historical candles for the same windows — **discrepancies recorded, not reconciled away** (a difference between our aggregation and the exchange's is a finding worth keeping). |

### 5.4 Non-interference proof (priority A)

The existing trading/shadow runtime must show **zero behavioural
difference** with the collector running: no new errors in its logs, no
changed decision output, no added latency beyond noise. If capture
affects trading in any measurable way, the session fails regardless of
how well capture itself performed.

### 5.5 Explicit non-goals for this session

No level memory, no queries, no "did we learn anything", no strategy
evaluation. The session answers exactly one question: **can Bujji observe
and remember one day of market reality, correctly and provably?**

---

## Part 6 — Objective E: Component Audit

### 6.1 Reuse unmodified

| Component | Role in 17F.0 |
|---|---|
| `state_persistence.EventStore` | All durability. Append-only JSONL, flush+fsync per record, torn-trailing-line tolerance. |
| `state_persistence` recovery pattern (`RecoveryReport`, `deduplicated_events`) | Restart recovery vocabulary and semantics. |
| `market_observation` (`Observation`, `build_observation`, `ObservationProvenance`) | Canonical record shape and the sole `observation_id` minting site. |
| `epistemics.lineage` (`Lineage`, `calc_version_for`, `look_ahead_violation`, `descends_from`) | Candle lineage and the `as_of` assertion primitive. |
| `epistemics.identity.resolve_calculation_identity` | Materializer identity stamping. |
| `market_reality` (17E) | Layer 0 itself — the collectors' sole destination. |
| `market_timeseries.subscription` | Symbol-list construction (spot + VIX + ATM±N band). Deterministic, no side effects. |
| `live_observation` (`Tick`, `AggregationWindow`, `add_tick`, `close_window`) | Tick-window mechanics including late/out-of-order handling. |

### 6.2 Reuse with additive extension

| Component | Extension | Risk |
|---|---|---|
| `CandleAggregator` | Input from Layer 0 replay; emit lineage fields | Low — arithmetic unchanged |
| `CandleStore` | Lineage columns, mandatory `as_of`, `FUTURES` kind, more intervals | Low — additive; `calc_version` in PK needs approval (Q1) |
| `FyersTickFeed` | **Per-tick emission hook, full payload, fired outside locks, default-absent** | **HIGH — concurrency-critical file with a documented lifetime proof.** Treated as its own gated step with dedicated concurrency tests. |

### 6.3 Must not be created

- **Another storage system.** `EventStore` for append-only logs, SQLite
  (WAL, `synchronous=FULL`) for indexed reads. Both already exist.
- **Another candle engine.** `CandleAggregator` is the only one. Its
  wall-clock-aligned windows and refusal to fabricate empty bars are
  correct and must not be reimplemented.
- **Another memory system.** Market memory is 17F.4; `outcome_memory`
  (what *we* traded) and `market_regime_memory` (current state) are
  different things and stay untouched.
- **Another lineage type.** `epistemics.Lineage` is canonical.
- **Another tick model.** `live_observation.Tick` exists.

### 6.4 Must not be touched

`msi_*` (all), `trading_brain`, `execution_engine`, `runtime_execution`,
`production_runtime`, `mic_replay`, `context_window` (inverted — see 17F
audit), `market_timeseries/indicators.py`, and every order-placement path.

---

## Part 7 — Implementation Sequence

Ordered by the stated priority: **A production safety → B data correctness
→ C replay proof → D memory usefulness.**

| Step | Scope | Gate | Priority |
|---|---|---|---|
| **17F.0.1** | `FyersTickFeed` per-tick hook — additive, default-absent, fired outside locks | Existing concurrency tests (`test_concurrency_lifetime_proof_p3.py`, fault-injection, reconnect-metric, tick-silence-watchdog) all green; with no hook registered, behaviour provably identical | **A** |
| **17F.0.2** | Candle lineage fields + `calc_version` stamping | Full regression green; existing candle tests unaffected | B |
| **17F.0.3** | Bitemporal `as_of` contract — mandatory on Layer 1 reads | Omitting `as_of` is a signature error; no-look-ahead property test passes | B |
| **17F.0.4** | Collectors (tick, quote, depth, chain) + gap/skip markers + bounded queue | Isolation proof: trading path bit-for-bit unaffected; failure modes each produce an attributable record | **A + B** |
| **17F.0.5** | Layer 0 → `CandleAggregator` wiring | Deterministic rebuild from Layer 0 alone | B + C |
| **17F.0.6** | **Reality Validation Session** (Part 5) | All five proofs + non-interference | **C** |

Only after 17F.0.6 passes does 17F.2 onward (futures memory, chain
materializer, level memory) begin.

---

## Part 8 — Decisions and Remaining Questions

### Decided by the operator (this phase)

| # | Question | Decision | Where specified |
|---|---|---|---|
| 1 | `calc_version` in the candle primary key? | **Yes — included.** PK becomes `(instrument, interval, window_start, calc_version)`. Migration cost is zero (no `.db` file exists yet). | Part 2.4 |
| 2 / 4 | Session-one capture scope | **NIFTY spot only.** No VIX, futures, options, depth, or chain. | Part 5.1 |

### Still open — not decided, not assumed

3. **Queue overflow policy.** Record-and-drop with a counted rejection
   (bounded memory, visible loss) versus block the writer (no loss, but
   backpressure reaches the socket thread). *My recommendation:
   record-and-drop* — capture must never be able to stall the feed, and
   priority A says the memory system may not become a new way for the
   trading system to fail.
   *Note: session one (spot only) will not stress this either way, so it
   can be decided before implementation without waiting on data.*

5. **Gap-marker representation.** A disconnect/skip marker is not a market
   observation. Store it as a distinct record kind in Layer 0, or in a
   separate capture-diagnostics log? *My recommendation: a distinct
   record kind in Layer 0* — a replay must see gaps in the same ordered
   stream as the data, or it will reconstruct a market that never went
   quiet.
   *This one does affect session one: proof #1 (observations captured)
   depends on gaps being attributable, so it needs deciding before
   17F.0.4.*

---

## Part 9 — Gate Status

| Gate | Status |
|---|---|
| Layer 0 → Layer 1 dependency verification | **Complete** — Part 1 |
| A. Candle lineage design | **Complete** — Part 2 |
| B. `as_of` contract design | **Complete** — Part 3 (bitemporal) |
| C. Collector architecture | **Complete** — Part 4 |
| D. Reality validation session protocol | **Complete** — Part 5 |
| E. Component reuse audit | **Complete** — Part 6 |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |

### The honest summary

Layer 0 is sound. Layer 1 is sound. **Neither is connected to anything**,
and the tick data needed to feed them is currently discarded at the
socket callback before any collector could see it. 17F.0 exists to fix
exactly that — and to make sure the candles it produces can prove where
they came from, and that no query about the past can ever see the future.
