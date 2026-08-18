# Phase 17F.0.2 — WebSocket Capture Design

**Status: DESIGN ONLY. No code. Awaiting review before Step 4.**

Audit of Layer 0 invariants and the `FyersTickFeed` concurrency model,
followed by the minimal design for an `on_tick` emission path.

---

## Part 0 — Two Audit Findings That Change the Plan

### 0.1 The scaling risk is RETIRED. It was already answered on disk.

The prior plan named one change capable of hurting trading: full-mode
`ltp` might be unscaled, shifting every live option premium by a power of
ten. I gated Step 7 behind an empirical check during a market-hours
certification run.

**That check has already been running in production for weeks.**

`run_live_shadow.py` logs the websocket price and the REST spot within
~40 ms of each other during every live session startup:

```
2026-07-28  websocket_connectivity=OK price=23995.95   quote_api_connectivity=OK spot=23995.95
2026-07-29  websocket_connectivity=OK price=23985.35   quote_api_connectivity=OK spot=23985.35
2026-07-30  websocket_connectivity=OK price=24250.20   quote_api_connectivity=OK spot=24250.20
2026-07-31  websocket_connectivity=OK price=24317.15   quote_api_connectivity=OK spot=24317.15
```

**Four sessions, four price levels, exact equality — ratio 1.000000
every time.** Full-mode `ltp` is scaled identically to the certified REST
path. No power-of-ten discrepancy exists.

The scaling comparison stays in the certification script as a standing
regression check, but it **no longer gates anything**.

### 0.2 My consumer audit was incomplete. Full mode is already in production.

The Step-3 audit grepped `bujji/` and `tests/` and missed repo-root
scripts. Corrected inventory of every `FyersTickFeed` construction:

| Site | Mode | Role |
|---|---|---|
| `bujji/app.py:116` | **lite** (default) | Stack A — the deprecated legacy stack |
| `run_live_shadow.py:186` | **`litemode=False`** | **The live shadow stack — the process actually running** |
| `tools/proof_b_stale_closure.py`, `tools/proof_c_concurrent_stress.py` | lite | Concurrency proof harnesses |

`run_live_shadow.py` has passed `litemode=False` since the Sprint 114
Day 1 fix, with an inline comment pointing at
`docs/DAY1_LIVE_SESSION_FINDINGS.md`.

**Consequence: there is no litemode migration to perform for capture.**
The collector attaches to the live shadow feed, which is already in full
mode. Step 7 of the prior plan — "flip `app.py` to `litemode=False`" —
is aimed at the deprecated stack and is **not required by this phase**.
`app.py` stays untouched.

### 0.3 And lite mode would have made spot capture useless anyway

From `DAY1_LIVE_SESSION_FINDINGS.md`, read from real SDK source: for an
INDEX symbol, lite mode fires the callback only when the encoded price
*differs* from the stored one — `ltp` is the only field it compares. Full
mode compares every field including `exch_feed_time`, which changes on
essentially every broadcast.

**A live session on 2026-07-28 received exactly one real tick all day for
`NSE:NIFTY50-INDEX` in lite mode.**

So full mode is not an optimisation for spot capture — it is the
precondition. Capturing NIFTY spot ticks in lite mode would have produced
a near-empty Layer 0 and looked like a broken collector.

---

## Part 1 — Layer 0 Invariant Verification

Verified by execution against a live store, not by inspection:

| # | Invariant | Method | Result |
|---|---|---|---|
| 1 | Records immutable | Re-append an identical observation | `DUPLICATE` — no second row |
| 2 | MOC separate from CaptureEvent | Read both version constants; scan MOC types | MOC `1.1.0`, Layer 0 `1.2.0`; no `CAPTURE`/`GAP` in `ALL_OBSERVATION_TYPES` |
| 3 | Replay ordering preserved | Union stream over obs → capture → obs | `['OBSERVATION','CAPTURE_EVENT','OBSERVATION']`; obs-only view `[24300.0, 24310.0]`; two runs identical |
| 4 | Certification gate unchanged | Uncertified market obs vs capture event | Observation `REJECTED`; capture event `ACCEPTED` (correctly ungated) |

All four hold. Full regression: **5,278 passed**.

---

## Part 2 — `FyersTickFeed` Concurrency & Lifecycle Audit

### 2.1 Threads

| Thread | Created by | Runs |
|---|---|---|
| Caller thread | The application | `start()`, `stop()`, `subscribe()`, `force_reconnect()` |
| `fyers-tick-feed` (daemon) | `_connect_locked()` | `socket.connect()` — blocking SDK loop |
| SDK internal threads | `fyers_apiv3` | Invoke `on_connect` / `on_message` / `on_error` / `on_close` |
| asyncio event loop | TickEngine / HealthEngine | Polls `latest()` / `tick_age_seconds()` — never blocks on the feed |

The four SDK callbacks execute on **SDK-owned threads**, not the caller's.
This is the context any tick hook will run in.

### 2.2 Locks — three, with a fixed discipline

| Lock | Guards | Held during |
|---|---|---|
| `_lifecycle_lock` | Connection *identity*: `_generation`, `_socket`, `_current_handle`, `_started`, `_stopped`, `_connected`, `_pending_symbols` | Currency checks, socket construction/teardown, generation advance |
| `_lock` | Payload data only: `_ltp`, `_last_tick_at`, `_last_error` | Cache reads/writes |
| `_reconnect_lock` | Serialises `force_reconnect()` end-to-end | The whole detach→close→rebuild sequence |

**Nesting order is fixed and documented: `_lifecycle_lock` OUTER,
`_lock` INNER, never reversed.** The two are held simultaneously in
exactly one place — `on_message`, where a callback must validate currency
and write payload atomically.

### 2.3 Generation identity

`_ConnectionHandle` exists because the four callbacks must be defined
*before* the socket they belong to is constructed (they are its
constructor arguments). A handle is created first, closed over by all
four callbacks, populated with its socket immediately after construction,
and **before** the connect thread starts.

Every callback's first action, inside `_lifecycle_lock`, is
`handle is self._current_handle`. A superseded generation's callback is a
complete no-op: no state mutation, no log line, no subscribe, no hooks.

### 2.4 The deadlock precedent that constrains this design

`force_reconnect()` carries an explicit, hard-won rule:

> "`close_connection()` itself now runs OUTSIDE `_lifecycle_lock`.
> Holding `_lifecycle_lock` across a call into the SDK's
> `close_connection()` was a real deadlock risk: a synchronous `on_close`
> invoked from inside that call would try to re-enter `_lifecycle_lock`
> (non-reentrant) from the same thread and block forever."

**This is precisely the hazard class a tick hook introduces.** Any
callable invoked while a lock is held can, directly or transitively,
re-enter the feed and deadlock the SDK thread. The design in Part 3
eliminates it structurally: hooks are invoked with **zero locks held**.

### 2.5 The proven hook pattern — already used twice

`on_connect` and `on_close` both solve "run a callback outside the lock":

```
with self._lifecycle_lock:
    ...currency check + state mutation...
    hooks_to_call = list(self._on_connect_hooks)   # captured UNDER the lock
# lock released
for hook in hooks_to_call:
    hook()
```

Capturing the list under the lock prevents a concurrent registration from
racing; invoking after release prevents deadlock. **The tick hook reuses
this shape verbatim.**

### 2.6 Shutdown and reconnect

| Operation | Sequence |
|---|---|
| `stop()` | Under `_lifecycle_lock`: `_stopped=True`, `_connected=False`, `_current_handle=None`, detach socket. **Then** close outside the lock. Detach-before-close means every in-flight callback sees itself stale. |
| `force_reconnect()` | `_reconnect_lock` held throughout. Retire generation + detach under `_lifecycle_lock` → **close outside it** → re-acquire and `_connect_locked()`. Checks `_stopped` before *and* after the close. |
| `_pending_symbols` | Never cleared. `on_connect` deterministically resubscribes — the SDK does not preserve subscriptions across its own reconnects, so this class does it. |

---

## Part 3 — The Minimal Change

```
FYERS websocket
      │
   on_message (SDK thread)
      │
      ├─► _ltp[symbol] = ltp        EXISTING — unchanged, still first
      │
      └─► on_tick hooks(raw msg)    NEW — invoked with ZERO locks held
                │
          Layer 0 collector  (bounded queue → writer thread)
                │
          RawObservationStore
```

### 3.1 Registration

`on_tick(hook: Callable[[dict], None])` — mirrors the existing
`on_connect` / `on_disconnect` registration methods exactly, appending to
an instance-level `_on_tick_hooks` list.

Hooks live on the **instance**, not the handle, so capture survives
reconnects automatically — the new generation gets the same hook list.

*Convention (matching existing hooks): register before `start()`.* The
existing `on_connect`/`on_disconnect` appends are likewise unguarded;
this design does not change that pattern, and registering post-start is
outside its contract.

### 3.2 Invocation, inside `on_message`

Ordering, precisely:

1. Extract `symbol` / `ltp`; return early if absent (unchanged — acks are
   not price ticks).
2. Acquire `_lifecycle_lock`. Currency check. **Unchanged, still first.**
3. Acquire `_lock` (inner). Write `_ltp` / `_last_tick_at`. **Unchanged.**
4. Release `_lock`. Capture `hooks_to_call = list(self._on_tick_hooks)`
   while still holding `_lifecycle_lock`.
5. Release `_lifecycle_lock`.
6. **With no locks held**, invoke each hook with the complete raw `msg`.
7. Any hook exception: catch, count, log, continue to the next hook.

### 3.3 What the hook receives

The **complete raw `msg` dict** — not the two fields the cache extracts.
No filtering, no normalisation, no interpretation before Layer 0 sees it.

### 3.4 Rules honoured

- No indicators, no aggregation, no intelligence, no strategy logic — the
  hook hands a dict to a queue.
- **No second websocket connection** — one feed, one socket, an
  additional consumer.
- **No duplicate persistence** — the collector writes through the existing
  Layer 0 `RawObservationStore` / `EventStore`.

---

## Part 4 — Concurrency Proof

**Claim 1 — no deadlock is reachable through a tick hook.**
Hooks are invoked only after both locks are released (step 5→6). A hook
therefore holds no feed lock when it runs, so no re-entrant call into the
feed — `latest()`, `subscribe()`, even `force_reconnect()` — can block on
a lock this thread already holds. The `force_reconnect` precedent (2.4)
is the same rule applied to `close_connection()`.

**Claim 2 — no new lock, no new ordering.**
The change introduces no lock. It adds one list read inside an existing
`_lifecycle_lock` critical section and one loop outside it. The
outer/inner discipline is untouched, so no new ordering pair exists and
lock-order inversion remains impossible.

**Claim 3 — `latest()` behaviour is bit-identical.**
`latest()` reads `_ltp` under `_lock`. The write to `_ltp` is unchanged in
value, in position, and in the lock it holds. Hooks fire strictly *after*
that write, so no observer can see a tick reach a hook before the cache.
Existing consumers cannot distinguish hook-registered from
hook-unregistered operation.

**Claim 4 — stale generations emit nothing.**
The currency check is unchanged and still the first action under
`_lifecycle_lock`. A superseded callback returns before reaching the hook
capture, so a retired socket cannot inject observations into Layer 0.

**Claim 5 — a hook cannot kill the feed.**
Every invocation is individually wrapped. An exception increments a
counter, logs, and proceeds to the next hook. The SDK thread returns
normally in all cases. Capture degrades; the feed survives.

**Claim 6 — the socket thread never blocks on I/O.**
The hook's only permitted action is a non-blocking enqueue. Disk and
SQLite belong to the collector's writer thread. Queue-full is handled at
the put (record-and-drop → `CaptureEvent[QUEUE_OVERFLOW]`), never by
waiting.

**Falsifiable test for Claim 1:** register a hook that acquires
`_lifecycle_lock` and returns. Under the design it succeeds; under any
lock-held variant it deadlocks. This is the acceptance test.

---

## Part 5 — Lifecycle Proof

| Phase | Behaviour | Hook consequence |
|---|---|---|
| Pre-`start()` | Hooks registered on the instance | Captured by every future generation |
| `start()` | `_connect_locked()` builds gen 1 | Callbacks close over handle 1 |
| Steady state | `on_message` fires per tick | Hook invoked once per tick, post-cache, lock-free |
| Disconnect | `on_close` → `_connected=False`, disconnect hooks fire outside the lock | Collector emits `CaptureEvent[DISCONNECT]` |
| SDK auto-reconnect / `force_reconnect()` | Old generation retired; new socket; `_pending_symbols` resubscribed by `on_connect` | Old-gen callbacks no-op; connect hooks fire → `CaptureEvent[RECONNECT_RECOVERED]` |
| `stop()` | `_stopped=True`, handle `None`, detach, then close | All later callbacks stale → **no hooks fire after stop** |

**Reconnect gap handling needs no new machinery.** `on_connect(hook)` and
`on_disconnect(hook)` already exist and already fire outside the locks.
The collector registers on both and emits the corresponding
`CaptureEvent`s — the disconnect/recovery pair from Step 3, linked by
`related_event_id`.

**One honest limitation:** the SDK has its own internal reconnect path
that may not always surface `on_close`. A gap that produces no callback
is invisible to this mechanism — which is exactly why the slow REST quote
poll exists as a control channel. Ticks silent while quotes arrive is a
feed failure, detectable without any callback.

---

## Part 6 — Failure Modes

| Failure | Behaviour | Recorded as |
|---|---|---|
| Hook raises | Caught, counted, logged; next hook runs; SDK thread returns normally | Error counter (not a `CaptureEvent` — an internal defect, not a market blind spot) |
| Queue full | Non-blocking drop, counted | `CaptureEvent[QUEUE_OVERFLOW]` with count |
| Writer thread dies | Queue fills → overflow path | `CaptureEvent[QUEUE_OVERFLOW]`; capture stops, feed and trading unaffected |
| Websocket disconnect | Existing reconnect logic; disconnect hook fires | `CaptureEvent[DISCONNECT]` → `[RECONNECT_RECOVERED]` |
| Silent SDK reconnect (no `on_close`) | Undetectable via callbacks | Detected by the REST control channel; unexplained completeness gap |
| Token expiry mid-session | `AuthenticationError` on the REST channel; websocket drops | `CaptureEvent[AUTH_FAILURE]` |
| Certification demoted mid-session | Gate re-reads live per write; ticks rejected | Rejection store entries |
| Stale-generation callback | Returns before hook capture | Nothing — correct |
| `stop()` during in-flight tick | Callback finds itself stale | Nothing |
| Unclean shutdown with queued items | Bounded drain deadline | `CaptureEvent[SHUTDOWN_DRAIN_INCOMPLETE]` with count |
| Disk full | Writer fails; capture halts | Loud failure; feed and trading unaffected |

---

## Part 7 — Certification Dependency

| Item | Status | Gates |
|---|---|---|
| Spot REST (`direct_sdk_fyers_broker_py`) | ✅ `CERTIFIED_AVAILABLE` | Quote control channel |
| **WebSocket (`fyers_websocket`)** | ❌ **Not run** | **All tick writes.** Uncertified ticks are rejected — correct gate behaviour. |
| Full-mode price scaling | ✅ **Retired** (Part 0.1) | Nothing. Four live sessions, exact equality. |
| Tick payload field census | ❌ Unknown | Whether `event_time` is populatable for ticks |

The hook and collector can be **built and unit-tested** without
certification — synthetic ticks exercise every path. Only *live writes*
are gated.

---

## Part 8 — Rollback Plan

| Change | Rollback | Blast radius |
|---|---|---|
| `on_tick` registration + invocation in `fyers_ws.py` | **Stop registering the hook.** With an empty list the loop body never executes — rollback is configuration, not a revert. | None when unregistered |
| Collector | Default-off; disable via config | None |
| `CaptureEvent` / schema 1.2.0 | Already shipped, additive, prior versions recognized | None |
| `app.py` litemode | **No change proposed** (Part 0.2) | None |

**Kill switch:** the collector is default-off, and the hook is inert
without a registration. Disabling capture returns the system to exactly
its current behaviour without touching a line of code.

**Full revert:** removing the `on_tick` method and its four-line
invocation restores `fyers_ws.py` byte-for-byte. No data migration —
Layer 0 is append-only and a session's records are identifiable by
session id.

---

## Part 9 — Test Strategy for Step 4

**Concurrency (the acceptance suite)**
- A hook that acquires `_lifecycle_lock` completes without deadlock
  (falsifies Claim 1 if the design regresses).
- A raising hook does not stop the feed; the cache still updates; the
  error counter increments.
- Multiple hooks: all invoked; one raising does not suppress the others.
- No-hook operation is behaviourally identical — existing
  `test_concurrency_lifetime_proof_p3.py`, `test_fault_injection_scenarios.py`,
  `test_tick_silence_watchdog.py`, `test_reconnect_count_metric.py`,
  `test_tick_engine.py` pass unchanged.

**Ordering**
- Cache is written before the hook observes the tick.
- `latest()` returns the same value with and without a hook registered.

**Lifecycle**
- Hooks fire for the current generation only; a retired handle's callback
  emits nothing.
- No hook fires after `stop()`.
- Hooks survive `force_reconnect()` and receive ticks from the new
  generation.

**Regression**
- Full suite green. Baseline: **5,278 passed**.

---

## Part 10 — Gate Status

| Gate | Status |
|---|---|
| Layer 0 invariants (1) | **Verified empirically** — Part 1 |
| Lifecycle / thread / lock audit (2) | **Complete** — Part 2 |
| Minimal change design (3) | **Complete** — Part 3 |
| Consumer preservation (4) | **Proven** — Claim 3, Part 4 |
| Concurrency proof | **Complete** — Part 4, with a falsifiable test |
| Lifecycle proof | **Complete** — Part 5 |
| Failure modes / reconnect / certification / rollback | **Complete** — Parts 6–8 |
| Design review | **Pending — awaiting operator approval** |
| Implementation | **Not started. Not authorized.** |

### Summary of what changed in this audit

Two things I previously flagged as risks are now resolved, both by
evidence that already existed:

1. **Scaling** — settled by four live sessions logging WS and REST prices
   ~40 ms apart, exact equality every time. No longer gates anything.
2. **The litemode migration** — not required. The live stack already runs
   full mode; only the deprecated Stack A does not, and it is out of
   scope.

What remains genuinely open is the websocket **certification** (symbol
integrity and the field census), which still gates live tick writes and
still needs one market-hours run.
