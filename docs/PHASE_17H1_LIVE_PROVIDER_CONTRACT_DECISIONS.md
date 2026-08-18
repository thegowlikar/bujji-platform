# Phase 17H.1 — Live Provider Contract Decisions

**Status: DECISIONS ONLY. No code.**

Locks the four decisions the 17H audit left open, per the operator's own
framing. Each decision below states the choice, the reasoning, and —
where the operator's proposal didn't quite match the real code — the
correction, verified against source rather than accepted at face value.

---

## Decision 1 — Market depth field mapping: Option A, normalize at the collector boundary

**Decided: normalize.** A future depth collector maps FYERS's real
`ask` (singular) onto Layer 0's existing `asks` (plural) key when
constructing the `MARKET_DEPTH` payload dict. `taxonomy.
REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH] = ("bids", "asks")` is
**not** changed.

**Reasoning, extending the operator's own analogy one step further, with
a verified fact:** this genuinely is the same shape as
`FyersBroker`'s existing symbol-construction discipline — but more
specifically, it already exists as a *pattern*, not just an analogy, in
this exact codebase. `bujji/market_reality/capture.py`'s
`build_raw_observation()` already performs an analogous translation for
every observation kind — `KIND_TO_MOC_TYPE`, `KIND_TO_VALUE_KIND`,
`KIND_TO_RESOLUTION` are all broker-agnostic-canonical mappings the
Layer 0 capture path already applies before a payload is ever validated.
A depth collector renaming `ask` → `asks` at construction time is not a
new architectural pattern — it is the same pattern this file already
uses for every other field, applied once more.

**Consequence, stated precisely:** the translation lives in the
**collector**, not in `market_reality` itself. `bujji/market_reality/`
must never contain a FYERS-specific field name (this is the same
boundary `test_market_reality_safety.py` already enforces mechanically
for imports — it should hold for vocabulary too, even though no test
currently checks that). The raw `ask` key, as FYERS actually sends it,
is preserved only in the **discovery artifact**
(`fyers_depth_discovery_20260813.json`) — Layer 0 stores the canonical
`asks` name, by design, once this collector is built.

---

## Decision 2 — REST-first live provider scope: confirmed, `get_spot()`/`get_option_chain()` only

**Decided: yes, REST-first, matching the operator's proposed scope
exactly.** `LiveMarketDataProvider` implements only the two methods
`MarketDataProvider` actually declares. No `get_ticks()`,
`get_depth_stream()`, or `subscribe()` method is added to this class.

**Reasoning, reconfirmed against the real runner one more time:** this
was 17H's own headline finding (Part 1.2) — `bujji_options_os_runner.py`
calls `get_option_chain()` exactly twice and `get_spot()` exactly once
per session, with **no loop**. Adding streaming methods to a class whose
only real caller never iterates would be building, in the operator's own
words from an earlier turn, "infrastructure without a consumer" — this
document adopts that framing directly because it is correct, not because
it was merely proposed.

**One boundary clarified, not previously stated as sharply:** this
decision governs `LiveMarketDataProvider` (the `MarketDataProvider`
implementation, Part 3A of the 17H audit) **only**. It says nothing about
whether a *separate* Layer 0 collector (Part 3B, e.g. a future spot/
futures poller) needs a loop — that component's shape is Decision 4's and
17H.3's concern, not this one's. Conflating the two was the exact
mistake the 17H audit's Part 3 open question was flagging; keeping the
scopes distinct here prevents re-introducing it.

---

## Decision 3 — `RawObservation` ownership: confirmed, no parallel schema

**Decided: `LiveMarketDataProvider` never defines its own observation
type.** It constructs `bujji.market_reality.models.RawObservation`
instances via the existing `build_raw_observation()`, exactly as every
other producer of Layer 0 records already does.

**Reasoning:** this was 17H's own central finding — `RawObservation`
already carries every field the operator's original
`SpotObservation`/`TickObservation`/`DepthObservation` sketch proposed,
via `Layer0Lineage` (`event_timestamp`, `capture_timestamp`, `source`,
`access_method`). Restated here as a locked decision, not a re-argued
point: **zero new observation types are authorized by this phase, ever,
for this purpose.**

**One nuance worth locking explicitly, since Decision 2 separates 3A
from 3B:** does `LiveMarketDataProvider` (3A, session-scoped, feeds the
Trading Brain) *also* write to `RawObservationStore` as a side effect of
serving a chain/spot request? **Decided: no, not in this phase.**
`ReplayChainProvider` — the one precedent this codebase already has for
implementing `MarketDataProvider` — never writes to Layer 0 either; it
only reads a bhavcopy file and returns rows. `LiveMarketDataProvider`
follows that same precedent: it is a **read path for the Trading Brain**,
not a Layer 0 writer. Layer 0 writing remains exclusively 3B's concern
(17H.3), via independent collectors. This keeps 17H.2 and 17H.3
genuinely separable, buildable, and testable in either order — exactly
as the 17H audit's own phase ordering already implied but did not state
this bluntly.

---

## Decision 4 — Capture lifecycle attachment point: confirmed structurally, reason vocabulary corrected

**Decided: attach `CaptureLifecycleTracker` to the active
collector/provider, not to `FyersTickFeed` hooks.** This restates and
locks 17F.6.2's own adapter decision — nothing new here, and correctly
so; that decision was already made on solid evidence (the proven-dead
`on_disconnect` hook) and this phase does not need to re-derive it.

**Correction to the proposed event vocabulary — verified against the
real, closed taxonomy before locking anything:**

The proposal listed `REST_TIMEOUT`, `AUTH_FAILURE`, `RATE_LIMIT`,
`COLLECTOR_FAILURE`, `RECOVERY`. The actual closed vocabulary
(`bujji.market_reality.taxonomy.ALL_CAPTURE_REASONS`) is:

```
DISCONNECT, RECONNECT_RECOVERED, QUEUE_OVERFLOW,
RATE_LIMIT_SKIP, AUTH_FAILURE, SHUTDOWN_DRAIN_INCOMPLETE,
COLLECTOR_RESTART
```

Two of the five proposed names map cleanly onto an existing reason;
three do not, and mapping them is not free — it requires a real design
decision the proposal's naming glossed over:

| Proposed | Maps to | Clean? |
|---|---|---|
| `AUTH_FAILURE` | `REASON_AUTH_FAILURE` | Yes, exact match |
| `RATE_LIMIT` | `REASON_RATE_LIMIT_SKIP` | Yes, close enough — decided to use the existing name verbatim, not a near-synonym |
| `RECOVERY` | `REASON_RECONNECT_RECOVERED` | Yes, but see below — the existing name is itself socket-shaped language ("reconnect") |
| `REST_TIMEOUT` | **No exact match** | Not decided — see below |
| `COLLECTOR_FAILURE` | **No exact match** | Not decided — see below |

**The real issue, not cosmetic:** `DISCONNECT`/`RECONNECT_RECOVERED` (the
`CaptureLifecycleTracker`'s `OPENING_REASONS` pairing) were designed
around a **persistent-connection** component — a websocket either is or
isn't connected, a real boolean state to open/close a condition against.
**A REST poller has no persistent connection to lose.** Every poll is an
independent call that succeeds or fails; there is no `is_connected`
boolean for a REST-based `LiveMarketDataProvider`/collector to report.

Applying `DISCONNECT`/`RECONNECT_RECOVERED` to REST failures would
require inventing a policy this codebase does not have yet — e.g. "N
consecutive failed polls = an open DISCONNECT condition, closed by the
next success" — and that threshold (`N=?`) is a real number someone has
to choose, not something to default silently.

**Decided, for this phase:**

- A single failed REST call (timeout, 5xx, malformed response) is
  recorded via `CaptureLifecycleTracker.record_point_event(reason=
  REASON_COLLECTOR_RESTART, ...)` if it triggers the collector process to
  actually restart, or is simply logged and retried without a capture
  event at all if it is a single, non-restarting, transient failure —
  **a single failed poll is not, by itself, evidence of a market-reality
  gap** the same way a websocket disconnect is; only a *sustained* run of
  failures spanning what should have been real observations is.
- `AUTH_FAILURE` maps exactly and is used as-is —
  `record_condition(reason=REASON_AUTH_FAILURE, ...)`, since an invalid
  token is a genuine ongoing condition (every subsequent call will also
  fail until the token is refreshed), correctly using the open/close
  pairing.
- `RATE_LIMIT_SKIP` maps exactly for a single skipped poll —
  `record_point_event(reason=REASON_RATE_LIMIT_SKIP, ...)`.
- **`REST_TIMEOUT`/`COLLECTOR_FAILURE` as sustained conditions (not
  single failures) are NOT decided by this document.** Whether a REST
  poller needs its own consecutive-failure threshold before opening a
  `DISCONNECT`-equivalent condition — and what that threshold is — is
  explicitly deferred to 17H.3 (the actual collector implementation
  phase), where a real polling cadence (Q5's already-decided 60s for
  depth) makes the threshold a concrete, answerable question instead of
  an abstract one. **No new `taxonomy.REASON_*` constant is authorized
  by this document** — if 17H.3 concludes the existing seven reasons
  are insufficient for REST-specific failure modes, that is itself a
  schema-change decision requiring its own explicit approval, not an
  assumption to fold in here.

**Websocket path, restated for completeness:** if/when a websocket
collector is built (17H.5, not authorized), it attaches its own
`CaptureLifecycleTracker` instance the same way — observing
`TickSilenceWatchdog` state directly (per 17F.6.2's adapter decision),
never `FyersTickFeed.on_disconnect`. Same tracker class, different
source, exactly as proposed. No change to this part of the proposal.

---

## Summary — what is locked

1. Depth field mapping: **normalize `ask` → `asks` at the collector
   boundary.** `taxonomy` unchanged.
2. `LiveMarketDataProvider` scope: **`get_spot()`/`get_option_chain()`
   only**, REST, no streaming methods.
3. Observation ownership: **`RawObservation`/`build_raw_observation()`
   only, no new types.** `LiveMarketDataProvider` (3A) does not write to
   Layer 0 — that stays exclusively 3B's job.
4. Capture lifecycle: **attach to the active collector/provider, not
   `FyersTickFeed` hooks** — confirmed. Event vocabulary: use the
   existing seven `taxonomy.REASON_*` constants only; the
   consecutive-REST-failure threshold question is deferred to 17H.3,
   not decided here, and no new reason constant is authorized without
   its own explicit decision.

## What remains open (explicitly, not silently dropped)

- The REST-failure-threshold question (Decision 4).
- 17H audit Part 4's original open items not touched by this document:
  whether `LiveMarketDataProvider` targets Path C only (assumed yes,
  unconfirmed), and whether `FyersTickFeed`/websocket infrastructure is
  ever reused for Path C or Path C stays REST-only permanently (Decision
  2 answers this for the CURRENT phase, not permanently).

Implementation (17H.2) may now begin against these four locked decisions.
This document does not itself authorize starting 17H.2 — that remains a
separate go-ahead.
