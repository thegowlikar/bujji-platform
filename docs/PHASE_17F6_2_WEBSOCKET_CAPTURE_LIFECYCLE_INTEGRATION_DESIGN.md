# Phase 17F.6.2 — Websocket Capture Lifecycle Integration Design

**Status: DESIGN ONLY. No code. No wiring. No schema changes.**

Audits the actual FYERS websocket lifecycle boundary
(`bujji/broker/fyers_ws.py`) and determines where
`CaptureLifecycleTracker` (17F.5 gap closure) should eventually attach,
without coupling `bujji.market_reality` to broker code. Every claim below
was verified by reading the real file and grepping its real callers —
nothing here is inferred from a docstring's own description of itself.

---

## Part 0 — The finding that reframes this whole design

**The websocket lifecycle owner exists, is well-engineered, and is
currently reachable from exactly one place: the deprecated legacy bot.**

- `FyersTickFeed` (`bujji/broker/fyers_ws.py`) is constructed in exactly
  one non-test location: `bujji/app.py`, whose own module docstring reads
  *"[DEPRECATED / LEGACY] Bujji ORB-VWAP ATM Seller... superseded by
  Bujji Options OS (production_runtime/, trading_brain/, ...)."*
- The **active** runtime (`bujji/production_runtime/market_data_provider.py`)
  states, in its own words: *"NO BROKER, NO WEBSOCKET DEPENDENCY HERE,
  DELIBERATELY... A live implementation of this interface is explicitly
  Category B/C future work... Phase-1 ships exactly one concrete
  provider, `ReplayChainProvider`,"* which reads historical NSE bhavcopy
  files, not live data at all.
- `bujji/trading_brain/portfolio_valuation/engine.py` similarly disclaims
  knowledge of FYERS/WebSockets by design.

**Consequence:** there is currently no live websocket path in the active
Trading Brain architecture to integrate with at all. The only working
websocket lifecycle in the repo belongs to a bot the project has already
moved away from. This does not make the audit moot — `FyersTickFeed` is
almost certainly what a future live `MarketDataProvider` implementation
will be built on, since it is the only tested, hardened websocket client
in the codebase (see Part 1's reliability notes) — but it means **the
correct integration point does not exist in the active runtime yet**,
and this design accordingly targets *where it would attach once a live
provider is built*, not a wiring that could happen today.

---

## Part 1 — Answers to the seven audit questions

### 1. Where exactly does connection lifecycle exist?

`bujji/broker/fyers_ws.py`, class `FyersTickFeed`. All four SDK callbacks
(`on_connect`, `on_message`, `on_error`, `on_close`) are defined as
closures inside `_connect_locked()` (lines 273–364), constructed fresh on
every connect/reconnect and passed into `data_ws.FyersDataSocket(...)`.
Lifecycle *identity* state (`_generation`, `_current_handle`, `_socket`,
`_started`, `_stopped`, `_connected`, `_connect_count`) is guarded by a
dedicated `_lifecycle_lock`, kept strictly separate from `_lock` (which
guards only tick/error payload data) — a real, documented, previously-
incident-driven design (`_ConnectionHandle`'s docstring cites a proven
P1-2 bug where a stale callback corrupted current-generation state).

### 2. Does one websocket instance map to one trading session?

**No, and there is no session concept here at all.** One `FyersTickFeed`
instance persists for the life of the owning process and can rebuild its
underlying socket arbitrarily many times (`_generation` increments on
every `_connect_locked()` call, i.e. on `start()` and on every
`force_reconnect()`). `_generation`/`_ConnectionHandle` exist purely to
solve a *concurrency correctness* problem (which callback belongs to
which live socket) — they are not exposed publicly and carry no meaning
a consumer outside this file could read as "session N."

Grep confirms: zero hits for `session_id` or `SessionManifest` anywhere
in `fyers_ws.py`. **This directly confirms the operator's own
observation** — a "session identity" concept does not exist here and
would need to be added at the adapter layer (Part 3), not inside the
broker file.

### 3. Are reconnects automatic?

Two independent mechanisms, layered, with one *not* trusted:

- **SDK-internal** (`reconnect=True` passed to `FyersDataSocket`):
  present, but this module's own docstring documents a real, live-
  incident-driven distrust of it — `docs/TICK_SILENCE_INCIDENT_P1.md`:
  a live session went silent for ~7.5 trading hours while
  `is_connected` stayed `True` and `reconnect_count` stayed `0` the
  entire time, because the SDK's internal retry path can leave a stale
  socket with no error ever surfacing.
- **`TickSilenceWatchdog`** (same file, lines 427–555): this project's
  own external reconnect mechanism, built specifically because the SDK's
  cannot be trusted to self-report failure. It is a plain, injectable
  state machine — no direct reference to `FyersTickFeed` or the SDK —
  polled externally with `tick_age`/`is_connected`/`market_hours`/
  `now_monotonic`, and calls an injected `force_reconnect_fn` after a
  silence threshold and backoff schedule. **This is the mechanism that
  actually decides "we are disconnected" in practice**, not the SDK's
  own `on_close` callback alone — a materially important fact for
  where a capture event should originate (Part 3).

### 4. Can multiple consumers share the same feed?

Structurally yes: `subscribe()` accumulates symbols in `_pending_symbols`
(never cleared across reconnects, which is what makes deterministic
resubscription possible), and both `on_connect(hook)` / `on_disconnect(hook)`
accept **multiple** registered hooks (stored as lists, all invoked). In
practice, exactly one instance is ever constructed today, shared between
`TickEngine` (`bujji/tick/engine.py`) and `HealthEngine`
(`bujji/tick/health.py`) within the one deprecated process.

**Neither `TickEngine` nor `HealthEngine` currently calls
`on_connect()`/`on_disconnect()` at all** — grep across the repo (outside
`fyers_ws.py` itself and tests) for either method call returns zero
hits. The hook mechanism exists and has never been used by anything.
`HealthEngine` instead reads `feed.is_connected` as a plain property poll.

### 5. Where does shutdown happen?

`FyersTickFeed.stop()` (lines 244–271), called today from
`bujji/app.py`'s shutdown path via `TickEngine.stop()`/`HealthEngine.stop()`
(the app's own methods, which presumably call through to the feed —
not traced further here since `app.py` is legacy and out of scope for
where new integration should attach). `stop()`'s own documented
discipline: `_stopped`, `_connected=False`, and the current handle are
invalidated atomically, *before* the socket is torn down, so every
in-flight or late callback (including from the currently-active
generation) becomes a guaranteed no-op from that point forward. This is
exactly the same "stale callback must not resurrect state" discipline a
capture-event adapter would need to inherit — see Part 3.

### 6. What thread executes callbacks?

**A raw background OS thread**, not the asyncio event loop —
`_connect_locked()`'s own comment: `threading.Thread(target=socket.connect,
daemon=True, name="fyers-tick-feed")`. The module's own docstring states
this is deliberate: "Runs on a background OS thread... callers poll this
store from the asyncio event loop instead of bridging threads with
callbacks." All four SDK callbacks execute on this thread, inside
`_lifecycle_lock` for their currency check and state mutation.

**This matters directly for where `append_capture_event()` may be
called from** — see Part 2.

### 7. Can `CaptureLifecycleTracker` safely live there?

**No — and it should not, for two independent reasons, both real:**

- **Architectural**: `bujji/market_reality/` must never be imported by
  `bujji.broker` (this is exactly the boundary
  `test_market_reality_safety.py` mechanically enforces in the other
  direction — Layer 0 must not import broker — but the same boundary
  cuts both ways by design: broker code has never needed to know Layer 0
  exists, and adding that dependency would be the "MCP contamination"-
  shaped mistake the operator flagged).
- **Concurrency/latency**: `state_persistence.EventStore.append()` does
  synchronous file I/O (per-record flush+fsync, per its own established
  discipline). Calling that from inside an SDK callback running on the
  feed's dedicated background thread, while that thread may be holding
  `_lifecycle_lock`, risks adding real I/O latency to the exact code path
  `TickSilenceWatchdog`'s backoff timing depends on, and — if the append
  ever raised — could crash a thread the SDK itself does not supervise.

**The existing hook mechanism is already the correct seam, and needs no
change to `fyers_ws.py` itself to be usable as one:** `on_connect(hook)`
/ `on_disconnect(hook)` are already present, already invoked from inside
the lock-guarded, currency-checked callback bodies (so a hook registered
here inherits the exact same "never fires for a stale/superseded
generation" guarantee for free), and already accept arbitrary external
callables with no import of anything back into `fyers_ws.py`.

**One real gap found, worth naming now rather than discovering it during
implementation:** today's hooks are called with **zero arguments** —
`Callable[[], None]`. An adapter registered via `on_disconnect(hook)`
would know *that* a disconnect happened, but not *why* (SDK-detected
`on_close`, vs. an auth failure inferred from `on_error`'s message, vs. a
watchdog-forced reconnect) — the closed `ALL_CAPTURE_REASONS` vocabulary
(`DISCONNECT` / `AUTH_FAILURE` / `RECONNECT_RECOVERED` / ...) cannot be
populated correctly from an empty-signature hook alone. `last_error` is a
separate property that could be read best-effort at hook-fire time, but
there is no guaranteed ordering tying a specific `on_error` message to
the `on_close` that follows it. **This is a real, small, additive
signature change for the adapter's own future implementation phase** —
not proposed or authorized here, just named so it is not lost.

---

## Part 2 — Correct integration boundary

```
FYERS SDK (fyers_apiv3)
        |
        |  on_connect / on_message / on_error / on_close
        v
FyersTickFeed                              <-- bujji.broker, never imports market_reality
        |
        |  .on_connect(hook) / .on_disconnect(hook)     <-- EXISTING seam, unused today
        v
[NEW, not yet built] FyersWebsocketLifecycleAdapter      <-- lives OUTSIDE both broker and market_reality
        |
        |  record_condition() / record_recovery() / record_point_event()
        v
CaptureLifecycleTracker                    <-- bujji.market_reality, never imports broker
        |
        |  append_capture_event()
        v
RawObservationStore -> EventStore (Layer 0, immutable)
```

This preserves the Broker → Adapter → Market Reality boundary exactly as
specified: `fyers_ws.py` never imports `market_reality`; the adapter
imports *both* (it is explicitly the seam, and seams are allowed to know
about both sides); `market_reality`/`capture_lifecycle.py` continues to
import nothing from `bujji.broker` (already true, already enforced
mechanically by `test_market_reality_safety.py`).

**The adapter's job, precisely:**

1. Register itself via the existing `feed.on_connect(...)` /
   `feed.on_disconnect(...)` hooks (or, if the hook-signature gap above
   is closed first, receive a reason/detail argument directly).
2. Own its own `CaptureLifecycleTracker` instance, constructed with the
   `source`/`access_method` identifying this feed.
3. On a disconnect hook firing: call `tracker.record_condition(reason=...)`.
   Since the hook alone cannot yet distinguish `DISCONNECT` from
   `AUTH_FAILURE`, the adapter's *first* implementation would reasonably
   default to `REASON_DISCONNECT` for every `on_disconnect` firing (the
   closed-vocabulary discipline still holds — it just starts coarser than
   ideal) and only gain `AUTH_FAILURE`-level precision once the hook
   signature is enriched.
4. On a connect hook firing **after a prior disconnect was recorded**:
   call `tracker.record_recovery(...)`. A connect hook firing on the
   *very first* connection (no prior open condition) must **not** call
   `record_recovery()` — `CaptureLifecycleTracker.record_recovery()`
   already handles this correctly today (returns `None`, no fabricated
   event) if the adapter simply always calls it and lets the tracker
   decide; the adapter does not need its own "is this the first connect"
   state.
5. Supply `event_time`/`knowledge_time` from the adapter's own clock read
   at the moment the hook fires — **not** from inside `fyers_ws.py`,
   preserving that module's existing "Layer 0 never reads the wall
   clock" rule by keeping the clock read one layer up, in code that was
   never subject to that rule in the first place (the adapter is not
   part of `bujji.market_reality`).
6. `TickSilenceWatchdog`-driven reconnects are a **separate** signal from
   the plain `on_disconnect` hook -- **now resolved empirically**, not
   assumed (see Part 2A below). A watchdog-forced reconnect's
   `close_connection()` call DOES synchronously invoke the old
   generation's `on_close` closure, but that closure is ALREADY suppressed
   as stale by `FyersTickFeed`'s own currency check by the time it fires
   -- so **`on_disconnect()` never fires for a `force_reconnect()` path,
   only `on_connect()` does, once the new generation comes up.** An
   adapter relying solely on the public hooks would be silently blind to
   every watchdog-forced reconnect's disconnect side. See Part 2A for the
   full trace and its consequence for the adapter design.

---

## Part 2A -- Empirical trace: does `force_reconnect()` fire `on_close`?

**Traced through the installed `fyers_apiv3` SDK source
(`FyersWebsocket/data_ws.py`) and the underlying `websocket-client`
library (`websocket/_app.py`), then proven executable with two new tests
in `tests/test_concurrency_lifetime_proof_p3.py`.**

**The chain, verified line by line:**

1. `FyersDataSocket.__init__` sets `self.restart_flag = reconnect` (our
   code always passes `reconnect=True`, so this starts `True`).
2. `FyersDataSocket.close_connection()` sets `self.restart_flag = False`
   **before** calling `self.__ws_object.close()` -- this is the one line
   that decides everything downstream.
3. The underlying `websocket-client` library's `WebSocketApp.run_forever()`
   teardown path calls `self._callback(self.on_close, ...)`
   **synchronously, before `run_forever()` returns** (`websocket/_app.py`,
   guarded by a `has_done_teardown`/lock pair so it fires exactly once
   regardless of which path triggered it).
4. `close_connection()` then calls `self.ws_thread.join()`, which blocks
   until `run_forever()` (and therefore the `on_close` callback) has
   already completed.
5. Back in `FyersDataSocket.__on_close`, since `restart_flag` is now
   `False` (step 2), it takes the **real-close branch** (`else:`) and
   calls `self.on_close({...})` -> `self.OnClose(message)` -- which IS our
   registered closure, the one `_connect_locked()` built for the OLD
   generation.
6. **So `on_close` DOES fire, synchronously, before `close_connection()`
   returns.** This confirms half the original question.
7. But `FyersTickFeed.force_reconnect()` sets `self._current_handle = None`
   **before** calling `old_socket.close_connection()` (it does this while
   still holding `_lifecycle_lock`, then releases the lock before the
   close call). So when the old generation's `on_close` closure fires in
   step 5-6, its very first line -- `if self._stopped or handle is not
   self._current_handle: return` -- is already `True`. **The closure is a
   complete no-op**: no state mutation, no log line, and critically, the
   public `on_disconnect()` hooks registered by an outside caller are
   never invoked.

**Executable proof** (both pass):

- `test_force_reconnect_close_fires_synchronously_but_is_suppressed_as_stale`
  -- arms a fake socket to synchronously invoke its `on_close` closure
  exactly as the real SDK does (mirroring the trace above, not skipping
  it), calls `force_reconnect()`, and asserts: the old socket really was
  closed AND its closure really did execute (`("close_connection", gen)`
  recorded) -- but the registered `on_disconnect()` hook list stays empty.
- `test_force_reconnect_new_generation_on_connect_still_fires_normally`
  -- proves the other half: the NEW generation's `on_connect` is
  unaffected and fires normally, observable via the public
  `on_connect()` hook, exactly like any other connect.

**Answer to the original ambiguity: a watchdog-forced reconnect currently
produces exactly ONE observable hook firing (`on_connect`, once the new
socket comes up) and ZERO `on_disconnect()` firings -- regardless of
whether the disconnect that triggered it was ever "real" from FYERS's
side.** The `on_close`/`on_disconnect` seam is structurally incapable of
seeing a `force_reconnect()`-initiated transition's disconnect side, by
design (the currency check exists specifically to prevent a
superseded/stale callback from mutating current state -- this is a correct
and desired property of the P1-2 fix, not a bug; it simply means the seam
was never built to also serve as a capture-event source for
watchdog-forced reconnects).

**Consequence for the adapter design (Part 2, revising item 6):** an
adapter cannot rely on `on_disconnect()` alone to observe a
`TickSilenceWatchdog`-driven reconnect. Two options, neither implemented
here:

- **(Recommended)** The adapter also observes `TickSilenceWatchdog`'s own
  state transitions directly (`watchdog_state` going
  `HEALTHY/RECOVERED -> TICK_SILENCE -> RECONNECTING`), polled the same way
  `HealthEngine` already polls `is_connected` -- and calls
  `tracker.record_condition(reason=REASON_DISCONNECT, ...)` on the
  `TICK_SILENCE`/`RECONNECTING` transition itself, independent of whether
  the socket-level hook ever fires. This keeps `fyers_ws.py` completely
  unmodified.
- **(Alternative, requires a `fyers_ws.py` behavior change, NOT proposed
  here)** `force_reconnect()` could fire the old generation's
  `on_disconnect` hooks explicitly, before nulling `_current_handle`,
  as its own deliberate signal -- distinct from the stale-suppression
  mechanism, which would still apply unchanged to any OTHER late
  callback from that generation (e.g. a delayed `on_message`). This is a
  real, small, surgical change if chosen, but is a production behavior
  change to a file with an extensive, incident-driven concurrency proof
  suite (`test_concurrency_lifetime_proof_p3.py`) backing its current
  guarantees -- any change here would need its own proof additions, not
  just a docstring update.

---

## Part 3 — Session identity (the operator's addition)

**Agreed this is needed, and it does not exist anywhere today** — not in
`fyers_ws.py`, not in `CaptureLifecycleTracker`, not in
`CaptureEvent` itself.

`CaptureEvent` (`bujji/market_reality/capture_events.py`) currently
carries `source`/`access_method`/`affected_instruments` — none of which
distinguish "the broker's socket dropped" from "the collector process
itself restarted and opened a fresh socket." Both would currently look
identical: a `DISCONNECT` (or none at all, if the process death was hard)
followed eventually by a `RECONNECT_RECOVERED`.

**Where this belongs:** the adapter layer (Part 2), not
`CaptureLifecycleTracker` and not `fyers_ws.py`. Concretely — **not
designed further here, since this document is audit-only** — a future
adapter would mint a fresh identity value once per process/adapter
lifetime (e.g. at `FyersWebsocketLifecycleAdapter` construction) and pass
it through as part of `detail` or a dedicated field on every
`CaptureEvent` it emits, so a later query can group events by "which
process instance observed this" independent of `related_event_id`'s
own disconnect→recovery pairing. Whether this is a new `CaptureEvent`
field (a schema change, out of scope for this audit-only document) or an
encoded value inside the existing `detail: Optional[str]` field is
exactly the kind of decision that belongs to an eventual **Q-style
decision document**, not this design audit.

---

## Part 4 — What this design explicitly does NOT authorize

- No change to `fyers_ws.py`.
- No new adapter module.
- No change to `CaptureEvent`'s schema (session identity stays a named,
  undesigned requirement, per Part 3).
- No wiring of `CaptureLifecycleTracker` into `fyers_ws.py`,
  `TickEngine`, `HealthEngine`, or `bujji/app.py` (which is deprecated
  and not the correct target regardless).
- No decision yet on whether the hook-signature gap (Part 1, Q7) is
  closed before or alongside adapter implementation.

## Part 5 — What must happen before implementation can start

1. ~~Resolve the live-runtime question~~ **DONE (operator decision,
   post-17F.6.2): prioritize building a live `MarketDataProvider` next.**
   No documented roadmap commitment existed for this (grepped
   `SHADOW_TRADING_BRAIN_VISION_AND_STATUS.md` and every "Category B/C"
   reference in the repo -- none commits to a timeline), so this was a
   product-priority call, not a code fact, and was put to the operator
   directly rather than assumed. Their choice: prioritize it. Next step
   is its own audit-first phase (proposed: **17F.7 -- Live
   MarketDataProvider Design**), auditing `MarketDataProvider`'s
   abstract interface and deciding whether `FyersTickFeed` is reused
   as-is or wrapped -- not started by this document.
2. ~~Resolve the `force_reconnect()` hook-firing ambiguity~~ **DONE** --
   see Part 2A. Traced through the installed SDK source AND proven with
   two new executable tests
   (`tests/test_concurrency_lifetime_proof_p3.py::test_force_reconnect_close_fires_synchronously_but_is_suppressed_as_stale`
   and `::test_force_reconnect_new_generation_on_connect_still_fires_normally`).
   The remaining open item is a DESIGN decision, not a fact-finding one:
   should the adapter also observe `TickSilenceWatchdog` state directly
   (Part 2A's recommendation), or should `force_reconnect()`'s own
   behavior change to fire `on_disconnect()` for the old generation
   before suppression (a `fyers_ws.py` behavior change, out of scope
   for an audit-only document)?
3. **Decide the hook-signature enrichment** (Part 1, Q7): does
   `on_connect`/`on_disconnect` gain an optional reason/detail argument
   (additive, backward compatible — every current registrant, i.e. none,
   is unaffected) before the adapter is built, or does the adapter ship
   first with the coarser default-to-`DISCONNECT` behavior described in
   Part 2, item 3?
4. **Decide session identity's concrete shape** (Part 3) as its own
   small decision, once an adapter is actually being built.

None of these four items are resolved by this document. This document's
job was narrower: prove where the wrong integration point would have
been (inside `fyers_ws.py` directly, or the currently-orphaned legacy
`bujji/app.py`), and prove where the right one is (an adapter, sitting on
the existing but unused `on_connect`/`on_disconnect` hook seam, feeding
`CaptureLifecycleTracker` — once a live consumer of that seam actually
exists in the active architecture).
