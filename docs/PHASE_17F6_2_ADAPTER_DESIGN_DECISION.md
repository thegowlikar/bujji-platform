# Phase 17F.6.2 — Adapter Design Decision: Observe the Watchdog, Don't Enrich the Hook

**Status: DECISION ONLY. No code.**

Closes the one open item Part 5 of
`docs/PHASE_17F6_2_WEBSOCKET_CAPTURE_LIFECYCLE_INTEGRATION_DESIGN.md` left
unresolved: should a future capture-lifecycle adapter observe
`TickSilenceWatchdog` state directly, or should `fyers_ws.py`'s hook
signature (and/or `force_reconnect()`/`stop()`'s internal ordering) be
enriched so `on_disconnect()` becomes a usable signal?

## One more empirical fact that makes this decision, not a preference

Before deciding, one more trace was needed: is `close_connection()` ever
called from anywhere other than `FyersTickFeed.force_reconnect()` and
`FyersTickFeed.stop()`? Grepped the installed `fyers_apiv3` SDK source —
**`close_connection` and the one `restart_flag = False` assignment appear
at exactly one place each, both inside `close_connection()` itself.** The
SDK never calls its own `close_connection()` internally.

Combined with Part 2A's trace (`restart_flag` is always `True` under this
codebase's usage, since `reconnect=True` is always passed) and the fact
that **both** of `FyersTickFeed`'s own call sites (`force_reconnect()`
and `stop()`) null `_current_handle` *before* calling `close_connection()`
— this generalizes Part 2A's finding beyond just `force_reconnect()`:

**`on_disconnect()` cannot fire under any code path this class currently
exercises, full stop.** Not "usually suppressed" — structurally
unreachable. Every route to `close_connection()` in this codebase already
invalidates the handle first, and the SDK's own internal auto-reconnect
path (which fires on a genuine, real disconnect while `restart_flag` is
`True`) never calls our `on_close` at all — it consumes the event
silently inside its own retry loop. This is, concretely, the exact Sprint
P1 incident this file's own docstring documents: `is_connected` stayed
`True` and `reconnect_count` stayed `0` through a 7.5-hour real outage,
precisely because no hook of any kind fired.

## Why this decides the question

**Enriching the hook signature alone (Q7's original framing — give
`on_disconnect` a reason/detail argument) fixes nothing.** A hook that
never fires is not made useful by giving it a richer, still-never-passed
argument. To make `on_disconnect()` genuinely usable, the fix would have
to be deeper: change `force_reconnect()`/`stop()` to fire the hooks
*before* nulling `_current_handle`, inverting the exact ordering the
P1-2 fix put in place specifically to guarantee a stale callback can
never act on current state.

That ordering is not incidental — it is the documented fix for a real,
previously-confirmed bug (`_ConnectionHandle`'s own docstring: a stale
callback could "silently corrupt the CURRENT generation's connection
state"). Reversing it to let a hook fire pre-invalidation would require:

- Re-reasoning about every one of `test_concurrency_lifetime_proof_p3.py`'s
  15+ proofs, several of which exist specifically to pin down "a stale
  callback does nothing, ever."
- Deciding what a hook is allowed to safely do while still holding
  `_lifecycle_lock` (firing it synchronously inside the lock is the only
  way to guarantee it observes truly-current state at the moment of
  firing) — introducing exactly the kind of "arbitrary external code
  running under our lock" risk this file's design has otherwise avoided
  throughout.
- Adding new proofs for the new ordering, on a file whose entire value is
  a hardened, incident-tested concurrency guarantee.

All of that risk, for a benefit — an event-driven push notification —
that a simple, already-established poll already delivers.

## Decision: observe `TickSilenceWatchdog` directly. No `fyers_ws.py` change.

**A future `FyersWebsocketLifecycleAdapter` polls `TickSilenceWatchdog.
watchdog_state` (and, for the coarser genuinely-still-connected case,
`FyersTickFeed.is_connected`) — the same pattern `HealthEngine` already
uses today for `is_connected`. `fyers_ws.py` is not modified.**

Concretely, once such an adapter is actually being built (not now — see
the still-open Part 5 item 1, whether a live `MarketDataProvider` is
even being built next):

- On `watchdog_state` transitioning into `TICK_SILENCE` or
  `RECONNECTING`: call `tracker.record_condition(reason=REASON_DISCONNECT,
  ...)`. This is the adapter's own decision about which of the closed
  `ALL_CAPTURE_REASONS` vocabulary applies — `TickSilenceWatchdog` itself
  carries no such vocabulary and shouldn't be asked to.
- On `watchdog_state` transitioning into `RECOVERED`, or on
  `is_connected` becoming `True` after having been `False`: call
  `tracker.record_recovery(...)`.
- `CaptureLifecycleTracker`'s own idempotence (17F.5) already absorbs the
  polling cadence mismatch for free — a poll loop calling
  `record_condition()` every cycle while still silent is exactly the
  "repeated call while open is a no-op" case that component was built to
  handle. No new deduplication logic needed in the adapter.
- The genuinely-visible-disconnect case that Part 2A separately proved
  `on_disconnect()` can't see (a `force_reconnect()`-driven reconnect)
  and the silent-decay case (`TickSilenceWatchdog`'s whole reason for
  existing) are now **both** covered by the same one poll loop, since
  a `force_reconnect()` is *triggered by* the watchdog leaving
  `HEALTHY`/`RECOVERED` in the first place — the adapter observing the
  watchdog sees the cause, not just (uselessly) trying to catch the
  effect via a hook that structurally cannot report it.

## Why not both (enrich AND observe the watchdog, for belt-and-suspenders)

Considered and rejected for this decision. Given `on_disconnect()` is
proven unreachable in every current code path, adding it as a second,
parallel signal source would add real surface area (a second event
consumer, a second place to reconcile against `CaptureLifecycleTracker`'s
single open-condition state) for zero additional coverage — everything
`on_disconnect()` could ever report, the watchdog/`is_connected` poll
already reports, and reports for cases `on_disconnect()` cannot. If a
live `MarketDataProvider` implementation later reveals a real gap the
watchdog-observation approach doesn't cover, that would be a new,
evidence-driven decision — not a hedge taken now against a hook that has
never fired.

## What remains open

The live-runtime question is now resolved (operator decision: prioritize
building a live `MarketDataProvider` next -- see the design doc's Part 5
item 1). That unblocks this adapter design from sitting behind "no real
caller yet," but does not itself authorize building the adapter --
`MarketDataProvider`'s own live implementation needs its own audit first
(proposed 17F.7), since it is the thing that will actually decide whether
`FyersTickFeed` is reused as-is or wrapped, which in turn is what this
adapter attaches to.

Session identity's concrete shape (Part 3) remains open, unchanged.
