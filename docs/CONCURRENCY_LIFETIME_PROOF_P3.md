# Production Reliability Sprint P3 — Concurrency & Lifetime Proof Audit

**Scope.** Exchange → Broker → FYERS SDK → `FyersTickFeed` → tick
callbacks → `TickSilenceWatchdog` → scheduler. Nothing outside this
chain. **No production code was modified, fixed, or refactored.** This
is a read-only proof exercise plus three new, executable, permanently
re-runnable test files (`tests/test_concurrency_lifetime_proof_p3.py`)
that encode the proofs so they don't rot into prose claims again.

All claims below are backed by output actually captured from a real run
against the real installed `fyers_apiv3` SDK and the real, unmodified
`bujji.broker.fyers_ws` module on the server — quoted verbatim, not
reconstructed from memory.

---

## 1. Ownership matrix

| Field | Owner class | Lifetime | Written by | Read by | Lock? | Risk |
|---|---|---|---|---|---|---|
| `FyersTickFeed._socket` | `FyersTickFeed` | Reassigned every `_connect()`/`force_reconnect()` call | main thread (`start`, `force_reconnect`, `_connect`) | `on_connect`/`on_message` closures (SDK's own background threads), `subscribe()` (caller thread), `stop()` | **No** | **CONFIRMED unsynchronized; CONFIRMED consequence (§3), NOT YET PROVEN to crash (§4)** |
| `FyersTickFeed._connected` | `FyersTickFeed` | Toggled per connect/close | `on_connect`, `on_close`, `force_reconnect` | `is_connected` property, `subscribe()` | Yes (`self._lock`) | Synchronized, but see §3 — a *stale* closure can still legally acquire the lock and write a wrong value |
| `FyersTickFeed._connect_count` | `FyersTickFeed` | Monotonic increment | `on_connect` | `connect_count` property | Yes | Same as above — lock protects the increment op, not the *legitimacy* of who's incrementing |
| `FyersTickFeed._ltp`, `_last_tick_at` | `FyersTickFeed` | Per-symbol, updated per tick | `on_message` | `latest()`, `tick_age_seconds()` | Yes | None found — writes and reads are both lock-protected, and a stale generation's ticks are stale *data*, not corrupted structure (see §5) |
| `FyersTickFeed._pending_symbols` | `FyersTickFeed` | Grows via `subscribe()`, never cleared | `subscribe()` | `on_connect` closure, `subscription_state` | Yes | None — deliberately never cleared by design (Sprint P1), single mutation site under lock |
| `FyersTickFeed._started` | `FyersTickFeed` | Set once | `start()` | `start()` (idempotency check) | **No** | Low — only ever called from one thread in every real caller (`run_live_shadow.py`'s startup path); no concurrent-call site exists today |
| `FyersTickFeed._last_error` | `FyersTickFeed` | Overwritten per error | `on_error` | `last_error` property | **No** | Low — plain string overwrite, worst case is a torn/stale read of a diagnostic-only field, no control-flow consequence |
| `data_ws.FyersDataSocket.__ws_object` (SDK-private) | SDK instance | Set in `__on_open`, cleared in `close_connection()`/internal `__on_close` retry | SDK's own websocket thread | `close_connection()`, `subscribe()`, `__ping` | SDK's own `websocket_lock` — **not used to guard `__ws_object` itself** | **CONFIRMED**: `close_connection()`'s entire body, including `restart_flag = False`, is skipped when `__ws_object` is `None` at call time (§2) |
| `data_ws.FyersDataSocket.restart_flag` (SDK-private) | SDK instance | Set at construction from `reconnect=`, only ever cleared by `close_connection()` | SDK internals | `__on_close` | No | Directly caused by the above — if `close_connection()` no-ops, `restart_flag` stays `True` on an abandoned instance forever |

---

## 2. `close_connection()` no-op — CONFIRMED (direct evidence, real SDK)

Real installed source (`fyers_apiv3/FyersWebsocket/data_ws.py`):

```python
def close_connection(self) -> None:
    if self.__ws_object:
        self.restart_flag = False
        self.__ws_object.close()
        self.__ws_object = None
        ...
```

Constructed a real `data_ws.FyersDataSocket` (no network I/O in
`__init__`), called `close_connection()` before `connect()` ever ran.
Real captured output:

```
BEFORE call: __ws_object=None restart_flag=True
AFTER call:  __ws_object=None restart_flag=True return=None
NO-OP CONFIRMED: True
```

**Classification: CONFIRMED.** The guard is real, the no-op is real,
`restart_flag` really does stay `True` with no exception and no
signal. `restart_flag=True` on an object we believe we tore down means
that instance's *own* internal reconnect path (`__on_close`, triggered
by any future socket-level close event on it) is still armed.

**Is the triggering window (`__ws_object is None` at the moment our
code calls `close_connection()`) reachable in production, not just in
this contrived test?** Yes, by inspection of two real call sites:
- `FyersTickFeed._connect()` launches `self._socket.connect()` on a
  background thread; the SDK's own `connect()` does
  `if self.__ws_object is None: self.__init_connection(); time.sleep(2)`
  before setting `__ws_object` in `__on_open`. `force_reconnect()` can
  legitimately be called (by the watchdog) inside that ~2s+handshake
  window.
- The SDK's own `__on_close` internal-retry branch (fires when
  `restart_flag=True`, which every real caller sets) does
  `self.__ws_object = None` and then calls `self.connect()` again —
  there is a real window, between that assignment and the retried
  `connect()` completing, during which `__ws_object` is `None` on an
  instance we still hold via `FyersTickFeed._socket`.

This window is **not exercised by a deterministic reproduction
harness in this sprint** (doing so requires driving the real SDK's
threads with real network timing, which is out of reach without a live
socket) — classified **NOT YET PROVEN reachable via automated
reproduction**, but **CONFIRMED reachable by code inspection** of two
independent, real call paths. I am not rounding this up to a full
CONFIRMED-reproduced status; the honest line is: the no-op itself is
proven; production reachability of the triggering window is argued
from source, not executed.

---

## 3. Stale-generation closure race — CONFIRMED (deterministic, executed)

`FyersTickFeed._connect()`'s `on_connect` closure reads `self._socket`
fresh every time it runs — it does not capture "its own" socket. Built
a fully deterministic (no thread timing) harness swapping only the
SDK import boundary (`bujji.broker.fyers_ws.data_ws.FyersDataSocket`)
for a controllable fake; `FyersTickFeed`'s own code is untouched.

Sequence executed: `start()` → generation 0 connects → `force_reconnect()`
→ generation 1 replaces it → then the **saved, stale generation-0
closure is invoked directly**, modeling a delayed callback firing from
the SDK's own abandoned-but-still-armed retry path (§2's finding is
exactly what makes this realistic, not contrived).

Real captured output:

```
=== Calls triggered by the STALE gen-0 on_connect firing late ===
  ('subscribe', 2, ('A', 'B'), False)

feed._socket is now generation: 2
The stale gen-0 closure's subscribe() call landed on generation: 2
feed.is_connected after stale gen-0 fire: True
feed._connect_count after stale gen-0 fire: 2

CONFIRMED — stale closure acts on the CURRENT (wrong-generation) socket: True
CONFIRMED — stale closure also silently flips feed._connected back to True: True
```

**Classification: CONFIRMED.** A closure with zero generation identity
means: if a zombie old socket (per §2) ever fires a late `on_connect`,
`FyersTickFeed` will (a) silently report `is_connected=True` and
increment `connect_count` even though nothing about the *current*
generation changed, and (b) issue a real `subscribe()` call through
whatever `self._socket` currently is. This is worse than a crash —
it's a silent, wrong, self-consistent-looking state. It would make the
`TickSilenceWatchdog` (which gates activation on `is_connected`) see a
healthy connection when the *actual* live connection's status is
unknown. Encoded permanently as
`test_stale_socket_closure_acts_on_current_generation_socket`.

---

## 4. Unsynchronized `self._socket` read — race CONFIRMED by inspection, crash NOT YET PROVEN empirically

`on_connect`'s `if pending and self._socket is not None: self._socket.subscribe(...)`
and `subscribe()`'s `if new and connected and self._socket is not None: self._socket.subscribe(...)`
both read `self._socket` outside `self._lock`. This is a real,
unsynchronized check-then-use pattern — **CONFIRMED as a data race by
direct code inspection**, no ambiguity there.

Ran 8 real threads (4× `force_reconnect()`, 4× `subscribe()`) hammering
`FyersTickFeed` concurrently for 3s, then again with 24 threads for 8s,
against a fake SDK whose `connect()` fires `on_connect` from a genuinely
separate thread with randomized 0–3ms jitter (not scripted ordering).

Real captured output (heavier run):

```
Ran concurrent force_reconnect()/subscribe() stress for 3.0s with 8 threads.
Errors observed in FyersTickFeed's own code (main-thread or hook calls): 0
is_connected at end: False
connect_count at end: 196
CRASH REACHED (AttributeError/TypeError on self._socket): False
```
```
(24-thread / 8s variant)
connect_count at end: 518
CRASH REACHED: False
```

**Classification: NOT YET PROVEN to crash.** The theoretical race is
real (confirmed by inspection); a hard `AttributeError`/`TypeError`
crash was **not reproduced** across ~700 real reconnect cycles under
heavy concurrent contention. This is an empirical bound, not a proof of
impossibility — CPython's attribute-lookup-then-call sequence for
`self._socket.subscribe(...)` is a small number of bytecode
instructions, and the window where `self._socket` flips to `None`
*between* the `is not None` check and the method-lookup step is
apparently narrow enough that it did not fire in this test's
interleavings. A formal proof of impossibility was not attempted (the
GIL does not make this provably impossible — only empirically rare)
and is out of scope for this sprint's time budget. Encoded permanently
as `test_concurrent_reconnect_and_subscribe_does_not_crash_in_stress`
so any future change that widens the window will be caught by CI.

---

## 5. Thread interaction graph

```
[SDK background thread: "fyers-tick-feed"]        [Main / operator loop thread]
   started by FyersTickFeed._connect()                run_live_shadow.py live loop
        │                                                    │
        ├─ blocks in socket.connect()                        ├─ watchdog.check(...) every
        │     (real network I/O)                             │   iteration (single-threaded —
        │                                                     │   no watchdog-vs-watchdog race
        ▼                                                     │   exists; see §6)
  [SDK internal: message_thread]                              │
  [SDK internal: ping_thread]        <-- both spawned          │
        │                                inside __on_open      │
        ▼                                                      ▼
   on_message(msg) ──────────► FyersTickFeed._ltp / _last_tick_at (locked, safe)
        │
   on_connect() ──────────────► FyersTickFeed._connected / _connect_count (locked,
        │                        but §3: no generation check — stale fire = wrong write)
        │                        FyersTickFeed._socket.subscribe(...) (UNLOCKED read, §4)
        │
   on_close(msg) ─────────────► FyersTickFeed._connected=False (locked)
        │
   [SDK internal __on_close retry path, if restart_flag still True — §2]
        └─ can re-arm __ws_object=None → self.connect() again, entirely
           inside the SAME FyersDataSocket instance, on ITS OWN thread,
           independent of whether FyersTickFeed._socket still points to it

[Main thread] FyersTickFeed.force_reconnect() ──► self._socket = None (UNLOCKED
        write, §4) ──► self._socket = <new instance> ──► spawns a NEW
        "fyers-tick-feed" thread for the new instance. The OLD instance's
        own SDK-internal threads (message_thread/ping_thread/retry) are
        NOT guaranteed to have exited — close_connection() may have
        no-op'd (§2).
```

**Key structural finding:** there is no "generation" or "epoch" concept
anywhere in this graph. Every closure and every SDK-internal thread
identifies itself only by which *variable* currently holds a reference
to it (`self._socket`), never by an identity check against the
instance it was created for.

---

## 6. Claims checked and found to be false positives / non-issues

- **`TickSilenceWatchdog` internal state (`_state`, `_reconnect_attempt`,
  etc.) is not thread-safe.** Checked: the *only* real caller is the
  live loop's single synchronous thread (`run_live_shadow.py`'s
  `_run_live()` calls `watchdog.check(...)` once per loop iteration,
  never concurrently). **FALSE POSITIVE as a live risk** — no second
  caller exists anywhere in the codebase (grepped: `watchdog.check(`
  and `TickSilenceWatchdog(` each have exactly one call site). Real
  risk only if a future change added a second caller; not a current
  concurrency bug.
- **`_started` and `_last_error` being unlocked.** Checked call sites:
  `_started` is written once from `start()`, which itself has exactly
  one caller in `run_live_shadow.py`'s startup sequence (not called
  from a background thread). `_last_error` is diagnostic-only, read by
  nothing that drives control flow. **FALSE POSITIVE as a
  correctness risk**, though both remain technically unsynchronized —
  listed in the ownership matrix for completeness, not escalated.

---

## 7. Summary classification

| # | Concern | Classification |
|---|---|---|
| 1 | `close_connection()` no-ops when `__ws_object is None` | **CONFIRMED** (direct execution against real SDK) |
| 2 | That no-op window is reachable in production | **CONFIRMED by code inspection**, not yet reproduced via automated harness |
| 3 | Stale/zombie closures act on the current-generation socket, silently corrupting `is_connected`/`connect_count` | **CONFIRMED** (deterministic, executed, encoded as a permanent test) |
| 4 | Unsynchronized `self._socket` read is a real data race | **CONFIRMED** by inspection |
| 5 | That race is reachable as a hard crash | **NOT YET PROVEN** — 0/~700 reconnect cycles under heavy concurrent stress; empirical bound only, not a proof of impossibility |
| 6 | `TickSilenceWatchdog` internal state needs synchronization | **FALSE POSITIVE** — single caller, no concurrent access exists today |
| 7 | `_started`/`_last_error` unsynchronized writes are a live risk | **FALSE POSITIVE** — single-caller / diagnostic-only, respectively |

## 8. What remains unknown

- Whether the §2/§4 windows can be forced to fire against the *real*
  network-backed SDK (not the fake) was not attempted — would require
  a live or replayed WebSocket server and is a materially larger
  effort than this sprint's harness-based approach.
- Whether CPython's bytecode-level atomicity guarantee for the
  `self._socket.subscribe` two-step lookup holds under PyPy or a
  future CPython with sub-interpreters was not evaluated (out of
  scope — this deployment runs CPython 3.12 only, confirmed via
  `python3 -m pytest` header: `platform linux ... Python 3.12.3`).

No fixes were applied. No production file under `bujji/` was modified —
confirmed via `git status --short`, only `tests/test_concurrency_lifetime_proof_p3.py`
is new. Full regression: 3058/3058 passing (3055 prior + 3 new).
