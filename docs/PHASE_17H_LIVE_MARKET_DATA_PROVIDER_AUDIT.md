# Phase 17H — Live Market Data Provider: Architecture Audit & Design

**Status: AUDIT + DESIGN ONLY. No code.**

Uses Gate B's completed evidence (`docs/FYERS_REALITY_PAYLOAD_CONTRACT.md`,
`docs/PHASE_17G_GATE_B_REALITY_CERTIFICATION_REVIEW.md`) as the source of
truth. Audits `MarketDataProvider`'s real interface, the real (not
sketched) architecture it sits in, and designs the minimum
`LiveMarketDataProvider` needed to convert real FYERS observations into
usable data — without inventing a parallel observation model where one
already exists.

**Headline finding, ahead of the detail: the proposed architecture in the
originating prompt reinvents a schema Layer 0 already has.** Part 2 below
explains why `SpotObservation`/`TickObservation`/`DepthObservation` should
not be built as new types — `bujji.market_reality.RawObservation` already
does this job, tested, certification-gated, and lineage-complete.

---

## Part 1 — Architecture audit (verified against the real code)

### 1.1 `MarketDataProvider`'s real interface (unchanged since 17F.7)

```python
class MarketDataProvider(ABC):
    def get_option_chain(self, as_of_date: str) -> Sequence: ...
    def get_spot(self) -> Optional[float]: ...
```

Two methods. No futures, no depth, no VIX, no breadth. `ReplayChainProvider`
is the only concrete implementation, loading one historical bhavcopy file.

### 1.2 Real consumption pattern (reconfirmed from `bujji_options_os_runner.py`)

`get_option_chain()` is called **exactly twice per session** (a
fail-closed pre-market probe, then once for real — cached in between by
`ReplayChainProvider`'s own `_ensure_loaded` guard). `get_spot()` is
called **exactly once**. There is **no polling loop, no continuous
consumption** anywhere in the runner — it is a single-pass-per-day batch
process. This was established in 17F.7 and nothing since has changed it.

### 1.3 THREE separate runtime paths exist, not one — a finding the
### originating prompt's single-arrow diagram (`FYERS → MarketDataProvider
### → Observation Layer → Market Reality`) does not capture

| Path | Entrypoint | Live data today? | Uses `MarketDataProvider`? |
|---|---|---|---|
| **A — Deprecated legacy bot** | `bujji/app.py` | Yes — the only live path with `FyersTickFeed`/websocket wired at all | No — pre-dates the abstraction |
| **B — Live shadow operator** | `run_live_shadow.py` / `bujji.live_shadow_operator` | Partially — a real, separate live system, explicitly using "the legacy exit_engine, not D.4" (per `bujji_options_os_runner.py`'s own docstring) | No — pre-dates the abstraction, different object graph entirely |
| **C — Active Trading Brain (Bujji Options OS)** | `bujji_options_os_runner.py` / `production_runtime/` | No — Phase-1, `ReplayChainProvider` only, explicitly "NO BROKER, NO WEBSOCKET DEPENDENCY HERE, DELIBERATELY" | **Yes — this is the only path that uses `MarketDataProvider` at all** |

**Consequence:** a `LiveMarketDataProvider` satisfying the
`MarketDataProvider` ABC plugs into **Path C only**. It does not
automatically feed Path A or Path B — those are architecturally separate
systems this document does not touch. If the goal is "give the ACTIVE
Trading Brain live eyes," Path C is correctly the target. If the goal was
instead "make the legacy live systems better," that is a different,
unstated scope this audit does not assume.

### 1.4 Neither proposed seam component is wired anywhere live today

- `FyersTickFeed` — constructed only in Path A (deprecated).
- `TickSilenceWatchdog` — **constructed nowhere in live code at all**,
  not even in the deprecated bot. Grep confirms: one comment reference in
  `live_shadow_operator/health.py`, otherwise test-only. The "correct
  seam" identified in 17F.6.2/the adapter decision (`TickSilenceWatchdog`
  → `CaptureLifecycleTracker`) is itself sitting on a component with no
  current live owner.
- `CaptureLifecycleTracker` — built, tested (17F.5), zero live callers.

**This means Phase 17H is not "wire two existing live things together."**
It is "decide whether Path A's `FyersTickFeed`/`TickSilenceWatchdog` pair
is revived as Path C's live data source, or whether Path C gets a
independent, possibly simpler, REST-first live path" — a real design
choice, addressed in Part 3.

---

## Part 2 — Interface mapping: reuse Layer 0's existing model, do not invent a new one

The originating prompt proposes three new types:

```
SpotObservation  { symbol, ltp, event_time, source }
TickObservation  { symbol, ltp, exchange_time, received_time }
DepthObservation { symbol, bids[], ask[], oi, ltp, exchange_time }
```

**Audit finding: this is a parallel re-implementation of
`bujji.market_reality.RawObservation`, which already has every one of
these fields, tested, certification-gated, and lineage-complete.**

`RawObservation` (via `Layer0Lineage`) already carries:

| Proposed field | Already exists as |
|---|---|
| `symbol` | `RawObservation.instrument` (via the wrapped MOC `Observation.identity`) |
| `ltp` / price payload | `RawObservation.payload` (kind-specific: `KIND_MARKET_TICK`/`KIND_QUOTE` require `ltp`; `KIND_MARKET_DEPTH` requires `bids`/`asks` — see Part 4's finding on the exact field-name mismatch) |
| `event_time` / `exchange_time` | `Layer0Lineage.event_timestamp` — already `Optional[str]`, already distinct from capture time, already what the dual-bound `replay()` (17F.5) bounds against |
| `received_time` | `Layer0Lineage.capture_timestamp` — already mandatory, already what `knowledge_time` bounds against |
| `source` | `Layer0Lineage.source` + `Layer0Lineage.access_method` — already two separate fields (which broker, which access path), richer than a single `source` string |

**Building `SpotObservation`/`TickObservation`/`DepthObservation` as new
dataclasses would create a second, parallel observation model** —
exactly the kind of duplication this entire engagement's "reuse, not
rebuild" discipline (restated explicitly by the operator at the start of
17F.1.2) exists to prevent. There is no missing capability here; there is
an unnecessary proposal to rebuild one.

**Recommendation: `LiveMarketDataProvider`'s job is to call the EXISTING
`bujji.market_reality.capture.build_raw_observation()`**, exactly the
function `RawObservationStore` and every existing test already use, not
to define new record types.

---

## Part 3 — Proposed observation flow (design, not implementation)

Two genuinely separate concerns the originating prompt's single diagram
conflates — kept separate here, consistent with the project's own
Reality → Memory → Understanding layering:

### 3A. Feeding `MarketDataProvider` (Trading Brain, Path C, session-scoped)

```
FYERS REST (optionchain, ltp)
        |
        v
LiveMarketDataProvider.get_option_chain()/.get_spot()
        |
        v
TradingSessionGovernor (existing, unmodified)
```

This path is **ephemeral** — exactly like `ReplayChainProvider`, it
serves the two calls-per-session pattern (Part 1.2) and does NOT persist
anything to Layer 0. `ReplayChainProvider` itself sets this precedent: it
never writes to `RawObservationStore` either.

### 3B. Feeding Layer 0 (permanent market memory, independent of any trading session)

```
FYERS (REST poll / websocket / depth poll)
        |
        v
[a collector, e.g. the existing gated depth poller, or a new
 spot/futures REST poller]
        |
        v
build_raw_observation()  (existing, unmodified)
        |
        v
RawObservationStore.append()  (existing, certification-gated)
        |
        v
Layer 0 (immutable, replayable)
```

This path already exists in skeleton (the depth poller, 17F.1.2) and is
independent of whether any trading session is running.

**Open design question, not resolved here:** should `LiveMarketDataProvider`
(3A) internally ALSO call into 3B's collector path as a side effect —
i.e., every time it fetches a chain for the Trading Brain, does that same
fetch also get recorded to Layer 0? Arguments for: avoids a second,
separate poll of the same endpoint. Arguments against: conflates a
session-scoped, ephemeral data source with a permanent, certification-gated
memory write — exactly the kind of concern-mixing the "provider should not
make decisions" instruction seems to want to avoid, extended one step
further to "the provider should not decide what becomes permanent memory
either." **This document does not resolve this — it is Part 5's first
implementation-phase decision.**

### 3C. Capture lifecycle (per the adapter decision, 17F.6.2 — unchanged, restated)

```
TickSilenceWatchdog (or an equivalent liveness signal for whichever
                      live source 3B ends up using)
        |
        v
CaptureLifecycleTracker.record_condition()/.record_recovery()
        |
        v
Layer 0 CaptureEvent stream
```

Confirmed correct per the earlier design (Part 2A of 17F.6.2's own
document): the adapter observes liveness state directly rather than
relying on `FyersTickFeed`'s `on_disconnect` hook, which was proven
structurally unable to fire for a watchdog-driven reconnect.

---

## Part 4 — Missing contracts (decisions required before implementation, not made here)

1. **The `bids`/`asks` field-name mismatch.** `taxonomy.
   REQUIRED_PAYLOAD_FIELDS[KIND_MARKET_DEPTH] = ("bids", "asks")` — plural
   `asks`. Gate B's real capture (`fyers_depth_discovery_20260813.json`)
   confirms FYERS's actual field is **`ask`, singular**. A depth
   collector cannot pass the existing validator's required-field check
   without either (a) the collector renaming `ask` → `asks` when building
   the Layer 0 payload dict (a translation, not a schema change), or
   (b) changing `REQUIRED_PAYLOAD_FIELDS` itself to match FYERS's real
   name (a schema change, however small). **Undecided; both are viable;
   this document takes no position**, beyond insisting it not be
   guessed silently inside a future collector's code.
2. **`ord` (order count per depth level)** — a real, observed field with
   no home in `taxonomy.REQUIRED_PAYLOAD_FIELDS` or anywhere else in
   Layer 0. Not required to be added (payloads may carry extra,
   non-required keys), but worth an explicit decision on whether it's
   worth preserving given it exists for free in every real capture.
3. **Option chain has no timestamp at all** (confirmed, Part 1 of the
   payload contract). `build_raw_observation()` requires
   `capture_timestamp` (always available — the poll time) but
   `event_timestamp` is `Optional` — for option chain rows, it must be
   `None`, explicitly, never backfilled from capture time. This is
   already supported by the existing `Layer0Lineage` design (17E's own
   "never backfilled" rule) — no contract gap here, just a reminder that
   a chain collector must pass `event_timestamp=None`, not guess one.
4. **Depth's `ltt` (epoch seconds) needs conversion** to the ISO8601
   string format `Layer0Lineage.event_timestamp` expects — a translation
   detail for whichever collector consumes it, not a schema question.
5. **Which live path (A/B/C, Part 1.3) `LiveMarketDataProvider` actually
   targets** — this document assumes Path C (the only `MarketDataProvider`
   consumer) is correct, but that assumption itself is worth one explicit
   confirmation before implementation starts, given two other live
   systems already exist in this codebase.
6. **Whether `FyersTickFeed`/`TickSilenceWatchdog` (Path A's components)
   are reused for Path C, or Path C gets independent, simpler,
   REST-first sourcing** given Part 1.2's finding that `MarketDataProvider`
   itself needs no continuous stream at all — only two point-in-time
   calls per session. A websocket may be more machinery than Path C's
   actual consumption pattern requires, mirroring exactly the finding
   that reframed 17F.7's own premise.

---

## Part 5 — Implementation phases (proposed, not started)

**Phase 17H.1 — Resolve Part 4's six open decisions.** Design-only,
operator decisions, same posture as the Q1–Q5 decisions doc pattern
already used this engagement.

**Phase 17H.2 — `LiveMarketDataProvider` (3A only), REST-first.** Given
Part 1.2's finding (two calls per session, no stream needed), the
minimum viable implementation needs only:
- A real per-strike premium source (17F.7 Part 5's own finding: the
  existing `FyersBroker.get_option_chain()` discards everything but OI;
  a live provider needs the raw `optionchain` `ltp` field at minimum,
  confirmed available by Gate B).
- Full-chain certification (17F.7 Part 3 — the existing cert only covers
  one ATM contract).
- No websocket dependency required for this phase alone.

**Phase 17H.3 — Layer 0 collectors (3B), reusing `build_raw_observation()`
unmodified.** Extends the existing, gated depth poller pattern to spot
and futures REST polling. Independent of 17H.2 — could be built first,
after, or in parallel.

**Phase 17H.4 — Capture lifecycle wiring (3C).** Only once 17H.2 or
17H.3 produces a real, live-running collector for the adapter to attach
to — per the standing "wrong integration point is worse than no
integration point" discipline (the operator's own words, from the
adapter-design decision).

**Phase 17H.5 — Websocket path, only if Part 4 item 6 concludes it's
needed.** Not assumed necessary by this audit.

None of these five phases are authorized to begin by this document.

---

## Part 6 — Constraints honored

- No code written.
- No schema expansion — Part 4's field-name questions are named as
  decisions, not silently resolved.
- No Greeks, no option intelligence, no regime model, no MSI, no market
  narrative — none referenced except to note they remain out of scope.
- No strategy logic anywhere in this document.
- Every claim traced to real code (grep'd, read, or Gate B's dated
  artifacts) — including the two findings (three runtime paths;
  `RawObservation` already satisfies the proposed new types) that revise
  the originating prompt's own framing rather than accepting it as given.
