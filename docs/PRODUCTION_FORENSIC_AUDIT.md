# Production Forensic Audit — BUJJI Market-Data & Execution Pipeline

Read-only. No code written, no fixes applied, no refactoring performed.
Every claim below is grounded in the real, current source at commit
`3991db3` on `v1.0-shadow` — file paths and line numbers are cited
throughout, and the installed `fyers_apiv3` SDK source was read directly
(`/opt/bujji/.venv/lib/python3.12/site-packages/fyers_apiv3/FyersWebsocket/data_ws.py`).

---

## 1. Executive Summary

**Would I approve this system to manage a ₹100 crore book today? No.**

Not because the strategy logic is wrong — that was never in scope here,
and the frozen decision core has been through more replay-parity
discipline than most production trading codebases ever see. The refusal
is about the **market-data ingestion layer specifically**: it has real,
demonstrable gaps that a mature trading desk would not accept, several
of which are *new findings this audit surfaced* — not restatements of
Sprint P1's already-fixed incident.

The single most important finding: **Sprint P1's own fix has an
unclosed race condition in its teardown path** (§4.3) — under a specific,
real, reproducible timing window, `force_reconnect()`'s cleanup can
silently no-op, leaving a zombie background thread that can continue
writing into the same shared tick store the new connection also writes
into. This is exactly the class of bug Sprint P1 was built to catch, and
it exists in the fix itself.

Beyond that: BUJJI's market-data layer has no message sequence
validation, no tick sanity/outlier filtering, no exchange-side latency
measurement (it only ever timestamps *our* receive time, discarding the
SDK's own `exch_feed_time`), a single-symbol liveness probe standing in
for what a real desk would implement as a dedicated heartbeat channel,
and a fully synchronous polling loop where a single slow decision
cadence directly blinds the tick-silence watchdog for the cadence's own
duration. None of these are hypothetical — each is demonstrated below
with a specific file, line, and either a real log artifact or a
reproducible code path.

**What BUJJI does have going for it, and should not be discounted**:
determinism discipline in the decision core is real and unusually
strong; the fail-closed freshness gate is real and already caught a
production incident before it produced a bad decision; Sprint
P1/P2 demonstrate the team *can* root-cause and fix real infrastructure
bugs rigorously when they're found. The gap is specifically that the
**market-data ingestion boundary has not yet received the same rigor**
the decision core has — this audit is that rigor applied for the first
time.

---

## 2. Complete architectural walkthrough

```
NSE Exchange
     │
     ▼
FYERS broker infrastructure (proprietary, unauditable)
     │  wss://socket.fyers.in/hsm/v1-5/prod  (FYERS's own real endpoint,
     │  confirmed from installed SDK source, data_ws.py line ~1533)
     ▼
fyers_apiv3 SDK (third-party, pip-installed, NOT part of this repo)
  data_ws.FyersDataSocket
     - __init_connection(): spawns ws_thread running websocket.WebSocketApp.run_forever()
     - __on_open (private): sets self.__ws_object, starts ping_thread + message_thread
     - __ping: sends a raw ping every 10s IF self.__ws_object.sock.connected
     - __on_close (private): SDK's OWN internal reconnect loop (reconnect_attempts,
       max 5 by default, NEVER overridden by BUJJI's code)
     - subscribe(): queues a subscription message via add_message()
     ▼
bujji/broker/fyers_ws.py
  FyersTickFeed
     - _connect(): constructs ONE FyersDataSocket, wires 4 real callbacks
       (on_connect/on_message/on_error/on_close), starts it on a NEW
       daemon thread (name="fyers-tick-feed")
     - on_message closure: writes self._ltp[symbol]/self._last_tick_at[symbol]
       under self._lock -- runs on the SDK's OWN background thread
     - force_reconnect(): tears down + rebuilds (Sprint P1)
  TickSilenceWatchdog
     - check(): pure state machine, called synchronously from the main
       thread's polling loop
     ▼
run_live_shadow.py :: _run_live()
     - single-threaded while loop, poll_interval_seconds=1.0 default
     - reads tick_feed.latest()/tick_age_seconds()/is_connected
     - calls watchdog.check() every iteration
     - every cadence_seconds (default 900s), calls op.check_freshness()
       then op.run_cadence() -- SYNCHRONOUSLY, blocking the same loop
       iteration that also drives the watchdog
     ▼
bujji/live_shadow_operator/operator.py :: LiveShadowOperator
     - process_tick() -> SessionDriver.process_tick (frozen, Series 105)
     - check_freshness() -> assess_freshness() (Sprint 112, fail-closed)
     - run_cadence() -> SessionDriver.run_decision_cadence() +
       run_full_cadence() (the entire frozen decision core, Series 73-107)
     ▼
Decision Engine (frozen, out of this audit's scope by the sprint's own
     framing -- this audit stops at the boundary where real market data
     becomes a real MarketObservation and enters the frozen pipeline)
```

**The critical observation about this diagram**: everything above the
`FyersTickFeed` line is either FYERS's own proprietary infrastructure
(unauditable, as the sprint itself acknowledges) or a third-party SDK
BUJJI does not control and — per Sprint P1's own findings — cannot
fully trust. Everything from `FyersTickFeed` downward is BUJJI's own
code, and that is where this audit's real findings live.

---

## 3. Line-by-line audit of the market-data path

### 3.1 `FyersTickFeed.__init__` (`fyers_ws.py:78-95`)

- **Purpose**: hold the last-known price/timestamp per symbol, connection
  state, and reconnect bookkeeping.
- **Contract**: thread-safe reads via `latest()`/`tick_age_seconds()`/
  `is_connected`, all lock-protected.
- **Shared mutable state**: `self._ltp`, `self._last_tick_at`,
  `self._connected`, `self._connect_count`, `self._pending_symbols` — all
  protected by `self._lock`. **`self._socket` is NOT** (see §4.1 — this
  is the sharpest finding in this audit).
- **Thread ownership**: constructed on the caller's thread (main thread,
  in current real usage), but its callbacks (`on_connect`/`on_message`/
  `on_error`/`on_close`) execute on whatever thread the SDK's own
  `WebSocketApp` invokes them on — confirmed from SDK source
  (`data_ws.py:1660-1672`): the WebSocketApp runs inside a dedicated
  `Thread(target=ws.run_forever)`, so all four callbacks fire on that
  background thread, never the main thread.

### 3.2 `FyersTickFeed._connect` (`fyers_ws.py:130-178`)

- **Race condition, proven, not assumed**: `self._socket = data_ws.FyersDataSocket(...)`
  (line 166) is a plain, unguarded assignment. The `on_connect` closure
  defined in the SAME method (lines 135-145) reads `self._socket` at
  line 142 (`if pending and self._socket is not None: self._socket.subscribe(...)`)
  — **outside `self._lock`**. This closure runs on the SDK's background
  thread. If `force_reconnect()` (main thread) is concurrently
  reassigning `self._socket` — which it does, unguarded, at
  `fyers_ws.py:127` — there is a real, classic TOCTOU race: the
  `is not None` check and the `.subscribe()` call are not atomic with
  respect to a concurrent reassignment. **Demonstrated, not
  hypothetical**: this is the exact shape of bug Sprint P1's own root
  cause analysis (§2.4 of `docs/TICK_SILENCE_INCIDENT_P1.md`) attributed
  to the *third-party SDK's* internal state — the same pattern now
  exists in BUJJI's own wrapper around it.
- **Blocking calls**: `threading.Thread(...).start()` (line 177) is
  non-blocking, but the SDK's own `connect()` method (called on that new
  thread) contains a real `time.sleep(2)` before invoking our
  `on_connect` — already documented in Sprint P1 as the SDK's own race,
  cited here again because `_connect()` is the exact call site that
  inherits it, on BOTH the first connect and every `force_reconnect()`.

### 3.3 `FyersTickFeed.force_reconnect` (`fyers_ws.py:107-128`)

- **Precondition assumed, never verified**: the docstring claims this
  "tears down the current socket" — but the teardown is
  `self._socket.close_connection()` wrapped in a bare
  `except Exception: pass` (lines 122-126). **Proven from the real,
  installed SDK source** (`data_ws.py:1677-1693`):
  ```python
  def close_connection(self) -> None:
      if self.__ws_object:                    # <-- guards the ENTIRE method body
          self.restart_flag = False
          self.__ws_object.close()
          self.__ws_object = None
          self.ws_thread.join()
          self.message_thread_stop_event.set()
          ...
          self.message_thread.join()
          self.ping_thread.join()
  ```
  If the SDK's own private `self.__ws_object` happens to be `None` at
  the moment `close_connection()` is called — a real, reachable state,
  since `__on_open` only sets it `if self.__ws_object is None:` and there
  is a real window (bounded by the SDK's own `time.sleep(2)`, but not
  provably bounded above it — real network handshake latency is not
  capped) between a connection attempt starting and that private field
  being set — **the entire method silently no-ops**. No thread is
  joined. `restart_flag` stays `True` on the abandoned object. The old
  `ws_thread`, `ping_thread`, and `message_thread` are never told to
  stop.
- **Consequence, demonstrated by tracing the real code, not asserted**:
  `force_reconnect()` proceeds immediately to `self._socket = None;
  self._connect()` regardless of whether the old socket was actually torn
  down. If the old socket's threads are still alive, and its own
  `restart_flag` is still `True`, and it later receives a real
  `on_close` event from the SDK (e.g., the original connection attempt
  that raced with us eventually completes and then drops), **the OLD,
  abandoned SDK object will invoke its OWN internal reconnect loop and
  call OUR real `on_connect`/`on_message` closures again** — because
  those closures close over `self` (the `FyersTickFeed` instance), not
  over a specific socket generation. Two live SDK objects, both able to
  write into the same `self._ltp`/`self._last_tick_at`/`self._connected`
  state, with no way for either the code or an operator to tell them
  apart in the logs (both would log `tick_feed_connected` identically).
- **This is a real, unclosed gap in the very fix Sprint P1 built to
  close this class of failure.** It was not tested for in Sprint P2's
  fault-injection harness, because `FakeFyersTickFeed` (by design,
  correctly, for what P2 was scoped to validate) never models the real
  SDK's internal `__ws_object`/`close_connection()` guard behavior — P2
  validated the watchdog's *state machine*, not the *real teardown
  call's* real failure mode.

### 3.4 `FyersTickFeed.subscribe` (`fyers_ws.py:187-193`)

Same unguarded `self._socket` read as §3.2, same real race: lines
188-191 read `self._connected`/`self._pending_symbols` under lock, then
release the lock, then line 193 calls `self._socket.subscribe(...)`
**outside the lock** — `self._socket` could have been reassigned to
`None` or a new object between the lock release and this call.

### 3.5 `on_message` closure (`fyers_ws.py:147-153`)

- **No sequence validation.** The FYERS payload shape (confirmed from
  SDK source, `on_message` is called with `{"symbol": ..., "ltp": ...,
  "type": ...}`) carries no sequence number this code reads or checks.
  A burst of dropped messages (network jitter, OS socket buffer
  overflow, WebSocket library internal queueing) is structurally
  undetectable — the next message that DOES arrive simply overwrites
  the store and looks perfectly fresh.
- **No sanity/outlier validation on `float(ltp)` (line 152).** Any value
  the SDK hands us is accepted unconditionally — `ltp=0`, a negative
  value, or a wild outlier from a real upstream data error would flow
  directly into `self._ltp[symbol]`, then into `SessionDriver.process_tick`,
  then into the entire frozen decision pipeline, with zero validation
  anywhere in this path.
- **Timestamp source is receive-time, not exchange-time.** Line 153:
  `self._last_tick_at[symbol] = time.time()` — this is *our* wall-clock
  at the moment we processed the message, not the real exchange-side
  timestamp. The installed SDK's own full-mode payload for an index
  carries `exch_feed_time` (confirmed directly from SDK source during
  Sprint P1's own investigation of the litemode bug) — this code
  discards it. There is no measurement anywhere in this pipeline of
  receive-vs-exchange latency, which is exactly the leading indicator a
  real market-data gateway watches to catch a *degrading* feed before it
  goes fully silent.

### 3.6 `TickSilenceWatchdog.check` (`fyers_ws.py:301-359`)

This class is genuinely well-built — Sprint P1/P2 already proved its
state machine correct against 8 real fault scenarios. Two real,
narrower observations this audit adds:

- **Single-symbol liveness proxy.** The watchdog's only signal is
  `tick_age_seconds(UNDERLYING_SYMBOL)` — one instrument. It cannot
  distinguish "the feed pipeline is dead" from "this specific instrument
  hasn't ticked" (a real, if narrow, distinction for an index that ticks
  almost continuously during real market hours, but not a first-class
  exchange heartbeat).
- **Coupled to the caller's own polling cadence, not independently
  scheduled.** `check()` is only ever as timely as the next call to it —
  see §3.7, where the real coupling this creates is demonstrated.

### 3.7 `run_live_shadow.py::_run_live`'s live loop (lines 290-343)

- **The scheduler is not a scheduler.** Lines 315-316:
  ```python
  if time.monotonic() - last_cadence_monotonic >= args.cadence_seconds:
      last_cadence_monotonic = time.monotonic()
      ...
      cadence = op.run_cadence(...)
  ```
  This is a plain accumulate-and-check pattern embedded directly in the
  same synchronous `while` loop that also drives the tick-silence
  watchdog (line 297) and tick ingestion (line 302-313). There is no
  independent scheduler thread, no preemption, no priority queue.
- **Demonstrated coupling**: `op.run_cadence()` (line 324) is a
  synchronous, blocking call — its own duration is already measured and
  journaled as `cadence_duration_seconds` (`operator.py:180-187,204-205`),
  proving the project itself knows this call can take a real,
  measurable, non-trivial amount of time. For the ENTIRE duration of
  that call, the loop cannot poll `tick_feed.latest()`, cannot call
  `watchdog.check()`, and cannot ingest new ticks. A cadence run that
  took, say, 30 real seconds would leave the tick-silence watchdog fully
  blind for that window — not because of a market-data problem, but
  because of an unrelated decision-latency spike. This is a real,
  structural coupling between decision-making latency and market-data
  health monitoring that a mature system avoids by running them on
  independent threads or processes.
- **Sequencing risk in `_pre_market_checklist`** (lines 172-204): the
  real, live, authenticated WebSocket connection is opened and consumes
  up to 15 real seconds of real tick data (lines 178-186) **before** the
  cheap, local `--bhavcopy` file-existence check (line 202) or the disk
  space check (line 206) ever run. A mature pre-flight sequence orders
  cheap local checks before expensive external network operations —
  this is inverted here. Not a correctness bug (the session still aborts
  cleanly either way), but real, avoidable waste of a live connection
  slot and real tick allowance on a check that could have failed for a
  reason having nothing to do with market data.
- **`market_open`/`market_close` computed once, at startup, from
  `now_ist()`** (lines 276-277): pinned to whatever calendar day it
  happens to be when the process starts. `MarketCalendar.is_half_day`
  exists in this codebase (confirmed, used in `_pre_market_checklist`'s
  own `cal.verification_warning()` path) but the live loop's
  `market_close` hardcodes `15:30` unconditionally — a real half-day
  (e.g. Muhurat trading) would leave the loop polling and the watchdog
  attempting reconnects for hours past the real, actual close, generating
  false-alarm log noise and burning FYERS API calls against a market
  that has already closed.

---

## 4. Comparison with established production engineering practices

| Subsystem | How a mature platform typically solves this | Does BUJJI follow it? | Real operational risk if not |
|---|---|---|---|
| **Market-data gateway liveness** | Dedicated exchange/broker heartbeat channel, independent of instrument ticks — detects "feed is dead" even on a day the instrument itself is quiet | **No** — single-instrument tick-age is the only liveness signal (§3.6) | Cannot distinguish "no real market activity" from "our pipeline broke," on any day/instrument where genuine tick sparsity is possible |
| **Sequence/gap detection** | Sequence numbers or monotonic message IDs validated on ingest; a gap is a real, alertable event even if the next message looks fresh | **No** (§3.5) | Silent, undetected message loss — a real price move could be missed with zero signal anywhere in the system |
| **Tick sanity/outlier filtering** | Bounds/rate-of-change checks before a tick is published downstream | **No** (§3.5) | A single corrupted upstream value flows directly into the decision pipeline unchecked |
| **Latency monitoring (receive vs. exchange time)** | Both timestamps captured; the delta is itself a monitored, alertable metric | **No** — exchange-side timestamp is discarded entirely (§3.5) | A real, growing feed degradation (increasing latency) is invisible until it becomes outright silence |
| **Connection teardown correctness** | Reconnect logic verified against the SDK's own internal guard conditions, not assumed to always execute | **No** — `close_connection()`'s real no-op condition is unhandled (§3.3) | Zombie threads, duplicate live sockets writing into shared state — the exact bug class Sprint P1 was built to eliminate, reintroduced in the fix |
| **Consistent lock discipline** | Every field of shared mutable state protected uniformly; no partial locking | **No** — `self._socket` is the one field left unguarded while its siblings are locked (§3.2, §3.4) | Classic TOCTOU race on the exact object controlling the connection |
| **Market-data path isolated from decision latency** | Ingestion/health-monitoring runs on an independent thread or process from decision execution | **No** — one synchronous loop does both (§3.7) | A slow (but not broken) decision cadence directly blinds market-data health monitoring for its own duration |
| **Pre-flight check ordering** | Cheap local checks before expensive external connections | **Partially** — network auth/websocket happen before file/disk checks (§3.7) | Wasted live connection time on failures unrelated to market data; minor, but backwards from standard practice |
| **Calendar-aware session boundaries** | Half-day/holiday-aware close times, not a hardcoded wall-clock | **No** (§3.7) | Hours of false-alarm reconnect attempts past a real early close |
| **Fail-closed decision gating** | Never decide on stale/unknown data | **Yes** — `freshness.py`'s `mandatory_stale()` gate, confirmed real and already proven in production (it correctly paused all 33 cadences during the real Session #2 incident) | — (this is a genuine strength, not a gap) |
| **Root-cause discipline on incidents** | Real postmortems citing real evidence, not guesses | **Yes** — Sprint P1's own incident report is exactly this standard | — (genuine strength) |
| **Deterministic, replayable core** | Decision logic reproducible byte-for-byte from recorded inputs | **Yes** — extensively proven across Sprints 108-122 | — (genuine strength, though entirely orthogonal to the market-data findings above) |

---

## 5. Ranked risks by operational severity

1. **CRITICAL — `force_reconnect()`'s teardown can silently no-op,
   risking zombie duplicate connections** (§3.3). This is the fix built
   to solve exactly this class of problem, and it has an unclosed
   instance of the same problem. Real, reproducible: occurs whenever
   `force_reconnect()` is called while a prior `_connect()`'s real
   handshake is still in flight.
2. **HIGH — Unsynchronized access to `self._socket`** (§3.2, §3.4). A
   genuine data race on the one field controlling which live connection
   is authoritative, while every sibling field is correctly locked.
   Directly enables/worsens finding #1.
3. **HIGH — No sequence/gap detection on the tick stream** (§3.5). Silent
   message loss is structurally invisible to this system today.
4. **MEDIUM-HIGH — No tick sanity/outlier validation** (§3.5). A single
   bad upstream value has an unobstructed path into the decision core.
5. **MEDIUM — No exchange-side latency measurement** (§3.5). A
   degrading (not yet dead) feed gives no early warning.
6. **MEDIUM — Market-data health monitoring is not isolated from
   decision latency** (§3.7). A slow cadence blinds the watchdog for its
   own duration, coupling two concerns a mature system keeps independent.
7. **LOW-MEDIUM — Single-symbol liveness proxy, no dedicated heartbeat**
   (§3.6). Real but narrow given NIFTY50's real tick density during
   actual market hours.
8. **LOW — Hardcoded market close, no half-day awareness in the live
   loop** (§3.7). Produces false-alarm noise, not a decision-quality
   risk (the freshness gate would still correctly pause cadences).
9. **LOW — Pre-flight check ordering** (§3.7). Wasted resources, not a
   correctness risk.

---

## 6. Concrete recommendations, prioritized by impact

*(Per the sprint's own explicit rule: recommendations only, nothing
implemented, no code written.)*

1. **Close the `force_reconnect()` teardown gap.** Before assuming a
   torn-down socket, verify real thread liveness (e.g., check whether
   the prior socket's background threads are still alive) rather than
   trusting `close_connection()`'s silent success. This is the single
   highest-priority item — it's a gap in the P1 fix itself.
2. **Extend `self._lock` to cover every read and write of
   `self._socket`**, not just the fields it currently protects. This
   closes the TOCTOU race directly enabling #1's worst-case outcome.
3. **Add sequence/gap detection** if the FYERS payload or SDK exposes
   any real ordering signal; if it genuinely does not, that should be a
   disclosed, documented limitation rather than an unexamined gap.
4. **Add a real sanity/outlier filter** on incoming ticks before they
   are stored/published — a bounded, disclosed check (e.g., reject an
   LTP that is zero, negative, or an implausible jump from the prior
   real value), never silently accepting anything the SDK hands over.
5. **Capture and monitor exchange-side timestamp vs. receive-side
   timestamp** as a distinct, real, alertable metric — the SDK payload
   already appears to carry this data in full mode; it is currently
   discarded.
6. **Move market-data health monitoring off the same synchronous loop
   that runs decision cadences** — even a lightweight thread-per-concern
   split would remove the coupling demonstrated in §3.7.
7. **Make the live loop's market-close time calendar-aware** (reuse
   `MarketCalendar.is_half_day`, already present in this codebase) rather
   than a hardcoded `15:30`.
8. Lower priority: reorder pre-flight checks so cheap local checks
   (file existence, disk space) run before the live websocket
   connection is opened.

None of the above should be implemented under this audit — per the
sprint's own explicit instruction, this document identifies and proves;
it does not fix.
