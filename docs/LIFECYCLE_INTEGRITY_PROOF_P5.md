# Production Reliability Sprint P5 — Lifecycle Integrity Proof

**Scope:** why did `run_live_shadow.py` (PID 723903, Session #3) remain alive as an OS process after printing `shutdown: clean` at 15:30:02 IST? Investigated live, against the actual still-running process — not reconstructed after the fact. **No Production code was modified, fixed, or refactored.** Read-only diagnostics only (`py-spy dump`, `/proc` inspection, `lsof`, direct SDK source reading — the same technique used throughout this engagement).

---

## 1. Lifecycle Timeline (real, evidence-backed)

| Stage | Time (IST) | Evidence |
|---|---|---|
| Startup | 06:51:21–06:51:25 | `run_live_shadow.py` launched, checklist passed |
| WebSocket creation (generation 1) | 06:51:24 | `tick_feed_connected` — `FyersTickFeed._connect()` constructs `data_ws.FyersDataSocket` #1, spawns wrapper thread `fyers-tick-feed` |
| SDK internal threads spawn (gen 1) | 06:51:24 (implicit) | SDK's own `__on_open` spawns `message_thread`/`ping_thread` for gen 1, per real source (`data_ws.py`, read earlier this engagement) |
| Cloudflare burst — SDK-internal retries | 07:47:06–07:47:28 | 5× `tick_feed_error`/`Handshake status 502`, each followed by `tick_feed_connected` — **all internal to the SAME `FyersDataSocket` instance** (gen 1's own `__on_close` retry path, confirmed by source: it never constructs a new `FyersDataSocket`, only resets `self.__ws_object=None` and calls `self.connect()` again on itself) |
| Watchdog-issued reconnect (generation 2) | 09:15:01 | `tick_feed_force_reconnect` — `FyersTickFeed.force_reconnect()` calls `close_connection()` on gen-1's socket, then constructs an entirely NEW `data_ws.FyersDataSocket` (gen 2) |
| Scheduler / decision cadences | 09:21:28–15:22:34 | 26 cadences, all on gen 2's tick stream |
| Shutdown sequence begins | 15:30:00–15:30:02 | `tick_feed.stop()` → `FyersTickFeed.stop()` → `self._socket.close_connection()` (on gen 2, the only socket `FyersTickFeed` still holds a reference to) → `op.shutdown()` → final report printed → `main()` returns 0 → `sys.exit(0)` |
| Thread termination | **incomplete** | See §3 — one non-daemon thread never terminates |
| Process exit | **never occurred** | Confirmed still alive at 17:07:53 IST, 1h37m after the script's own logic completed |

---

## 2. Thread Table

| Thread | Creator | Purpose | Daemon? | Termination condition | `join()` behaviour | Ownership |
|---|---|---|---|---|---|---|
| `fyers-tick-feed` (wrapper, per generation) | `FyersTickFeed._connect()` | Calls `self._socket.connect()` (blocking SDK call) | **Yes** (`daemon=True` explicit in `bujji/broker/fyers_ws.py`) | Returns once the SDK's own `connect()` hands off to its internal threads | Never joined by BUJJI code (daemon, not required) | BUJJI-created, SDK-executed |
| `ws_thread` (SDK-private, per generation) | SDK's `__init_connection()` | Runs the actual websocket-app loop | Not verified directly (not observed in the final 2-thread state — see §4, already reaped) | Set on real socket close | `close_connection()` calls `self.ws_thread.join()` **only if `__ws_object` was truthy at call time** | **SDK-owned entirely** |
| `message_thread` (SDK-private, per generation) | SDK's `__on_open` | Runs `__process_message_queue` — pops queued outbound messages and sends them | **No** — plain `Thread(target=self.__process_message_queue)`, no `daemon=True` (confirmed by reading the real installed SDK `__init__.py`/`__on_open` source) | `message_thread_stop_event.is_set()` **and** a `message_condition.notify()`/`notify_all()` to wake it out of `wait()` | `close_connection()` calls `self.message_thread.join()` — but only reaches this line if `__ws_object` was truthy; and only joins **the single `self.message_thread` reference that exists at call time** | **SDK-owned entirely — this is the thread that never terminates** |
| `ping_thread` (SDK-private, per generation) | SDK's `__on_open` | Sends periodic pings, checks `self.__ws_object.sock.connected` | Not verified directly (not in final 2-thread state) | Similar gating to `message_thread` | `close_connection()` calls `self.ping_thread.join()` under the same guard | **SDK-owned entirely** |
| `MainThread` | Python interpreter | Runs `run_live_shadow.py`'s own script logic | N/A | Exits when `main()` returns and all non-daemon threads have been joined by CPython's own `threading._shutdown()` | **Blocked**, per §3 | BUJJI script |

**Critical structural fact, confirmed by direct source reading:** `FyersDataSocket` stores exactly **one** `self.message_thread` attribute and **one** `self.ping_thread` attribute — there is no list/registry of every thread ever spawned across the object's lifetime. Each time `__on_open` fires (including on every SDK-internal retry during a burst like 07:47), it **overwrites** `self.message_thread`/`self.ping_thread` with fresh `Thread` objects. Any previous thread that wasn't already stopped is orphaned — the SDK itself loses its own ability to reach or join it, because the only reference to it (`self.message_thread`) has been overwritten.

---

## 3. Proof — Which Thread, and Why

**Live `py-spy dump` against the still-running process (executed twice, ~1.5 hours apart, identical result both times — confirmed persistent, not transient):**

```
Thread 723903 (idle): "MainThread"
    _shutdown (threading.py:1622)
Thread 723912 (idle): "Thread-2 (__process_message_queue)"
    wait (threading.py:355)
    __process_message_queue (fyers_apiv3/FyersWebsocket/data_ws.py:1482)
    run (threading.py:1010)
```

- **`MainThread` is blocked inside CPython's own `threading._shutdown()`** (`threading.py:1622`) — this is the interpreter's built-in finalization routine, which iterates every still-alive **non-daemon** thread and joins it before allowing the process to actually terminate. This is not a BUJJI or SDK code path — it is Python itself refusing to exit while a non-daemon thread is alive. This directly and completely explains "the script printed `shutdown: clean` yet the OS process remained alive": the script's own logic genuinely finished; the *interpreter* is what's still waiting.
- **`Thread-2`, named `"Thread-2 (__process_message_queue)"`, is blocked inside the SDK's own `__process_message_queue`, at the exact `Condition.wait()` call** (`data_ws.py:1482`, matching the real source read in §1 of this report's investigation: `self.message_condition.wait()`). It is waiting for either `message_thread_stop_event.set()` or a new message to arrive — **neither will ever happen again for this specific thread**, because the `FyersDataSocket` instance that owns it is no longer the one `FyersTickFeed._socket` points to (see §1: gen 2 replaced it at 09:15:01), so nothing in the running program still holds a reference capable of calling `close_connection()` on it.

**Root mechanism, precisely identified, two independent contributing paths (both grounded in real SDK source already read during this engagement, not newly speculated):**

1. **Single-reference thread tracking.** As established in §2, `self.message_thread` is a single attribute, overwritten on every `__on_open`. During the SDK's own internal retry burst at 07:47 (5 reconnects, all within generation 1, all via `__on_close`'s internal retry path which — confirmed by source — never calls `close_connection()`, only resets `self.__ws_object=None` and calls `self.connect()` again), each retry's `__on_open` would spawn a **fresh** `message_thread`, silently orphaning whichever one existed before it, with no stop signal ever sent to the orphaned one.
2. **The `close_connection()` no-op condition (Sprint P3, §2), now shown to have a concrete downstream consequence.** Even for the *final* generation's own `message_thread`/`ping_thread`, `close_connection()`'s entire body — including the `message_thread_stop_event.set()` and `message_condition.notify()` calls that would wake the thread — is skipped entirely if `self.__ws_object` happens to be `None` at the exact moment `close_connection()` is called. P3 proved this no-op is real and directly reachable against the live SDK; this sprint shows, for the first time, what it looks like when it actually leaves a thread stuck.

**Whether this was gen-1's orphaned burst thread or gen-2's own final thread hitting the no-op window cannot be distinguished from `py-spy`'s stack trace alone** — both are real, source-grounded mechanisms that produce the exact observed symptom, and either is sufficient to fully explain it. I am not asserting one over the other without more evidence than is available (no thread-creation-time correlation was captured live, and no further diagnostic was attempted, per the sprint's "no fixes, no unrelated engineering" boundary).

**Reproducibility:** not attempted as a clean, isolated repro in this sprint — the mechanism is proven structurally from source (§2, §3) and observed once, live, with a stable, repeated stack trace. A deterministic repro (e.g., driving a fake SDK through the same overwrite-without-stop sequence, similar to the P3 harness style) is a natural next step but was not built here, to respect the sprint's scope.

---

## 4. Runtime Evidence

- **Thread dump:** captured twice (§3), identical, 2 threads total, stable — not growing.
- **Process state:** `S (sleeping)`, both threads idle (`futex_wait_queue`/`Condition.wait()`), **0% ongoing CPU consumption** — this is a permanently blocked, not runaway, process.
- **Open file descriptors** (`/proc/723903/fd`): 3 log files (`fyersDataSocket.log`, `live_shadow_session_s3.log`, `fyersApi.log`, `fyersRequests.log` — all still open, never closed, consistent with the process never reaching true interpreter exit), 1 `eventpoll` fd, 2 connected UNIX domain stream sockets.
- **Network handles:** `ss -tnp` shows **zero** TCP/IP connections — the real websocket to FYERS is genuinely closed. The two open sockets (`lsof`-confirmed: `type=STREAM (CONNECTED)`, paired to each other) are a UNIX domain socketpair, not network traffic.
- **New, secondary finding — a leaked `asyncio` event loop, distinct from the thread-block issue.** `lsof` shows `aiohttp/_websocket.cpython-312-x86_64-linux-gnu.so` still loaded in memory, and the open `eventpoll` + UNIX socketpair are the standard signature of an `asyncio` event loop's self-pipe. This traces directly to `run_live_shadow.py::_pre_market_checklist()`, which does `loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)` and is used for `broker.connect()`/`get_spot()`/`get_option_chain()` — **`loop.close()` is never called anywhere in that function** (confirmed by direct grep during the P4 sprint, where this was flagged as unknown **U10** and explicitly not investigated at the time, out of respect for that sprint's own scope boundary). This sprint's live evidence confirms U10 was a real, live resource leak — **but it is not what blocks process exit** (open file descriptors and un-closed event loops do not prevent CPython's `threading._shutdown()` from completing; only live non-daemon threads do). Reported here as a distinct, secondary, now-confirmed finding, not conflated with the primary root cause.
- **Object ownership:** every thread and lock involved in the primary blocking issue (`message_thread`, `message_condition`, `message_thread_stop_event`) is a private attribute of the third-party `data_ws.FyersDataSocket` class. `FyersTickFeed` (BUJJI's own code) never references these directly — it only calls the SDK's public `close_connection()` method, exactly as the SDK's own documentation prescribes.

---

## 5. Classification

**Primary finding (why the process didn't exit): SDK Limitation.**

This is not a Confirmed Production Defect in BUJJI's own code. `FyersTickFeed.stop()` calls the one, correct, publicly documented teardown method (`close_connection()`) exactly as intended. The failure to fully reap every spawned thread is a structural property of the third-party SDK's own design: a single-reference (not a registry) tracking exactly one `message_thread`/`ping_thread` at a time, combined with an internal retry path (`__on_close`) that spawns replacement threads without ever signaling the ones they replace, and a `close_connection()` no-op condition (independently proven live-reachable in Sprint P3) that can skip the one cleanup path that does exist. BUJJI has no public API surface to reach into the SDK's private thread/condition objects to force a more thorough cleanup without reaching into SDK internals directly (which is a design change, out of this sprint's scope).

**Secondary finding (the leaked asyncio loop): confirmed real, but separately classified.** This one *is* BUJJI-owned (a real, missing `loop.close()` in `run_live_shadow.py`), but it is not the cause of the hang — it's an independent, smaller resource leak, now confirmed rather than merely suspected (P4's own U10).

**Neither finding is fixed in this sprint** — per the explicit rule, this was investigation only.

---

## 6. What Would Be Obvious From This Evidence, If A Fix Is Ever Wanted

Not proposed or scoped here, stated only because the sprint's own success criterion asks that a future fix be "obvious from the evidence, not guessed":
- The primary hang has no clean fix reachable from BUJJI's own public-API-only usage of the SDK — it would require either (a) accepting the hang and using an explicit `os._exit()` after logging completes (bypasses graceful interpreter shutdown, a real behavior change, not attempted here), or (b) upstream changes to the SDK itself.
- The secondary asyncio-loop leak has an obvious, narrow fix: call `loop.close()` at the end of `_pre_market_checklist()`. Not implemented here.
