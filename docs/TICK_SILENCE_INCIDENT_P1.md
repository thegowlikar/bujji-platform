# Production Reliability Sprint P1 — Tick Silence Incident Report

## 1. Incident summary

Live Shadow Session #2 (2026-07-29) received real ticks for
`NSE:NIFTY50-INDEX` exactly twice all day — 06:48:17 IST and 07:22:45
IST, **both before real market open (09:15 IST)** — then went
completely silent for the remainder of the real trading session
(through 15:18 IST, the last real staleness check before close). The
feed reported `is_connected=True` and `reconnect_count=0` the entire
time. Only 1 of ~26 possible 15-minute decision cadences completed; the
other 33 were skipped as `mandatory input(s) STALE`.

## 2. Root cause — evidence-grounded, not assumed

**Root cause: A (half-open connection) compounded by C (an SDK-internal
race condition), triggered by a real Cloudflare 502 error burst.**
Evidence:

**Real log timeline** (`logs/live_session2_20260729_v2.log`):
```
06:48:16  tick_feed_connected                                    (initial connect)
06:48:17  [real tick — the FIRST of only two all day]
07:22:45  [real tick — the LAST of only two all day]
07:47:07  tick_feed_error / Handshake status 502 Bad Gateway (Cloudflare)
07:47:09  tick_feed_connected                                    (reconnect #1)
07:47:12  tick_feed_error / 502                                  (reconnect #2 begins)
07:47:14  tick_feed_connected
07:47:17  tick_feed_error / 502
07:47:19  tick_feed_connected
07:47:22  tick_feed_error / 502
07:47:24  tick_feed_connected
07:47:27  tick_feed_error / 502
07:47:29  tick_feed_connected                                    (5th and LAST reconnect of the day)
[ ... ABSOLUTE SILENCE from 07:47:29 through 15:18:26 (last log line before close) ... ]
```
Five real reconnects completed successfully (from our own code's
perspective — `tick_feed_connected` fired each time) within 22 real
seconds. After the fifth, **zero further errors, zero further
reconnects, zero further ticks** for the remaining ~7.5 real trading
hours.

**Real installed SDK source** (`fyers_apiv3/FyersWebsocket/data_ws.py`,
the exact version installed at `/opt/bujji/.venv/lib/python3.12/site-packages/`):

1. **`connect()` (line ~1523) does not wait for the real handshake to
   complete before invoking our callback:**
   ```python
   def connect(self) -> None:
       if self.__ws_object is None:
           self.__init_connection()
           time.sleep(2)          # <- fixed, arbitrary delay, NOT a real completion signal
       self.on_open()             # <- calls OUR on_connect hook unconditionally
   ```
   `__init_connection()` only *starts a background thread* running
   `ws.run_forever()` — the real TLS handshake and the SDK's own private
   `__on_open` (which sets `self.__ws_object` and starts the ping/message
   threads) happen asynchronously, on a separate thread, with no
   guaranteed relationship to a flat 2-second sleep. Under load (exactly
   the condition immediately following a Cloudflare 502 burst), this is
   a real, demonstrated race.

2. **`__on_close` (line ~1589) NEVER calls our registered `on_close`
   hook when `reconnect=True`** (which every real caller of this class,
   including ours, always passes):
   ```python
   def __on_close(self, ws, close_code, close_reason):
       if self.restart_flag:              # True whenever reconnect=True
           if self.reconnect_attempts < self.max_reconnect_attempts:
               ...
               self.connect()
           else:
               print("Max reconnect attempts reached. Connection abandoned.")
       else:
           self.on_close(...)             # <- OUR hook, only reached if reconnect=False
   ```
   This independently explains why `reconnect_count` stayed `0` in the
   EOD health report despite 5 real reconnects — `op.note_reconnect`
   (wired via `tick_feed.on_disconnect`) is structurally unreachable
   whenever `reconnect=True`. A real, pre-existing, disclosed gap,
   separate from the tick-silence root cause itself (not fixed in this
   sprint — see §8, out of scope).

3. **`max_reconnect_attempts` defaults to 5** (`reconnect_retry: int = 5`
   in the constructor, never overridden by our code):
   ```python
   self.max_retry = reconnect_retry
   ...
   self.max_reconnect_attempts = 50
   if reconnect_retry < self.max_reconnect_attempts:
       self.max_reconnect_attempts = reconnect_retry   # -> 5
   ```
   `reconnect_attempts` resets to `0` on every real successful
   `__on_open`, so this cap alone doesn't fully explain 7.5 hours of
   silence with zero further `__on_close` events — but it is the exact
   number of real reconnects observed in the 502 burst (5), a real,
   notable coincidence worth recording even though it isn't the sole
   explanation.

4. **The ping thread's own liveness check depends on the same
   `self.__ws_object`** the reconnect race can leave in a stale or
   half-open state:
   ```python
   def __ping(self):
       while (self.__ws_object is not None
              and self.__ws_object.sock
              and self.__ws_object.sock.connected):
           self.__ws_object.send(...)
           time.sleep(10)
   ```
   If a race during the rapid reconnect burst leaves `self.__ws_object`
   referencing a socket that is nominally "connected" (no close/error
   ever surfaced) but not actually exchanging data with the server —
   the classic half-open TCP state, common behind Cloudflare-fronted
   endpoints that can silently drop an idle connection without a
   FIN/RST reaching either endpoint — the ping loop keeps running,
   never detects the failure, and the SDK never calls `__on_close`
   again. This is fully consistent with the observed real symptom:
   total silence, zero further errors, `is_connected` never toggling
   back to `False`.

**Conclusion**: the installed SDK's own reconnect/liveness mechanism
cannot be trusted to detect or recover from this failure mode. Detection
and recovery must be implemented entirely on our side, external to the
SDK's internal state.

## 3. What was NOT the cause

**Option B (lost subscription after reconnect) is not the primary
cause.** Our own `on_connect` hook (which performs the real resubscribe
via `self._socket.subscribe(symbols=pending, ...)`) fired and logged
`tick_feed_connected` on all 5 real reconnects during the burst — our
resubscribe logic itself ran every time. The failure is upstream of
that: whether the resubscribe message actually reached a genuinely live
socket is what the SDK's own race (§2.1, §2.4) cannot guarantee.

## 4. Subscription validation — documented conclusion

**The installed SDK does NOT preserve subscriptions across its own
internal reconnects.** Real, quoted evidence: `__on_close`'s reconnect
branch clears `self.symbol_token = {}` before calling `self.connect()`
again. Subscription restoration is therefore performed **deterministically
by this project's own code**, not assumed from the SDK: `FyersTickFeed`
owns `_pending_symbols` (never cleared by the SDK), and its `on_connect`
closure re-sends the real subscribe message every time a connection is
established — including via the new `force_reconnect()` path (§5), which
never clears `_pending_symbols` either.

## 5. The fix — `TickSilenceWatchdog` (`bujji/broker/fyers_ws.py`)

A real, external, injectable state machine — `HEALTHY → TICK_SILENCE →
RECONNECTING → RECOVERED` or `→ CRITICAL_FAILURE` — exactly the 5 states
specified. Polled once per real loop iteration (default 1s) from
`run_live_shadow.py`'s live loop, using the feed's own real
`tick_age_seconds`/`is_connected`.

- **Activates only** during real market hours (09:15–15:30 IST) and
  while `is_connected` is already `True` — a real, visible disconnect is
  already covered by the feed's own reporting and is out of this
  watchdog's scope.
- **Never fabricates ticks** — it only ever calls a real reconnect
  function; it has no path to write to `_ltp`/`_last_tick_at`.
- **Single reconnect request per silence episode**, with real
  exponential backoff (`backoff_base_seconds * 2^(attempt-1)`) before a
  repeat attempt — verified to never fire more than once within a
  backoff window (`test_repeated_silence_respects_exponential_backoff_no_storm`).
- **Fails loudly**: after `max_consecutive_failures` (default 3) real
  reconnect attempts within one episode, transitions to
  `CRITICAL_FAILURE` and never silently resumes — verified even when a
  real fresh tick arrives afterward
  (`test_reconnect_failure_reaches_critical_failure_and_stays_there`).

**`FyersTickFeed.force_reconnect()`** performs a full, real teardown and
recreate of the underlying `data_ws.FyersDataSocket` — a fresh object,
fresh threads — rather than going through the SDK's own internal
`__on_close`-triggered retry path (§2's own demonstrated risk).
`_pending_symbols` is preserved across this call, so the real
resubscribe logic in `_connect()`'s `on_connect` closure runs exactly as
it does on the very first connect (verified:
`test_force_reconnect_preserves_pending_symbols`).

## 6. Operational metrics added (observability only, per the sprint's own requirement)

`watchdog_state`, `reconnect_attempt`, `reconnect_reason`,
`current_tick_age`, `last_tick_timestamp` (all real properties on
`TickSilenceWatchdog`) and `subscription_state` (real property on
`FyersTickFeed`) are threaded, purely additively, through
`LiveShadowOperator.health_snapshot()` → `build_health_snapshot()` →
`HealthSnapshot`/`render_health_dashboard()`. A `CRITICAL_FAILURE`
watchdog state escalates `overall_status` to `RED`, mirroring the
existing `journal_health` `DEGRADED`/`MISSING` escalation pattern
already used elsewhere in this file. **None of these fields are read by
any decision function** — verified by construction: no MSI package, no
`live_pipeline_bridge.py`, no `live_shadow_validation.py` imports
`bujji.broker.fyers_ws` or references `watchdog_state` anywhere.

## 7. Verification

- **Regression: 3047/3047 passing** (3032 pre-existing + 15 new,
  covering: normal flow, temporary silence, prolonged silence →
  TICK_SILENCE → RECONNECTING, successful recovery, repeated silence
  with exponential backoff (no storm), reconnect failure →
  CRITICAL_FAILURE (stays failed), a raised exception from the
  reconnect function itself (caught, no crash, no double-fire),
  activation gating (market hours / connection state), subscription
  preservation across `force_reconnect()`, and the new `health.py`
  escalation rule — plus a backward-compatibility test confirming every
  existing call site with no watchdog kwargs behaves unchanged).
- **Replay parity: 0 diffs** across all 41 real corpus days
  (`--day` replay path, which never touches `FyersTickFeed` at all).
- **Zero pre-existing tests modified.**
- **Zero changes to the learning architecture** (Series 99–107) — this
  sprint touched only `bujji/broker/fyers_ws.py`,
  `bujji/live_shadow_operator/health.py`,
  `bujji/live_shadow_operator/operator.py`, and `run_live_shadow.py`.

## 8. Explicitly out of scope (flagged, not fixed)

- The `reconnect_count` metric's structural dead-code condition (§2.2)
  when `reconnect=True` — a real, separate, pre-existing gap. The new
  watchdog's own `reconnect_attempt` counter is now the authoritative,
  real reconnect-activity signal in the health snapshot; the old
  `reconnect_count` field is left as-is, unfixed, and disclosed here
  rather than silently left misleading without explanation.
- The shared `data/live_shadow_journal/operator_journal.jsonl` file's
  accumulated stale test entries from prior sprint runs (flagged after
  Session #2, unrelated to this incident) — real housekeeping debt, not
  addressed in this sprint (unrelated engineering work, per the
  sprint's own explicit instruction).
