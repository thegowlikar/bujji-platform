# Phase 17I — Market Reality Activation: Implementation Audit

**Status: AUDIT ONLY. No code. No wiring. No schema changes. No new
observation models.**

Goal: find the minimum real path to Bujji's first real Layer 0
observation — not a complete system, one real row.

---

## 1. Existing collectors

| Script | Writes to Layer 0? | Status |
|---|---|---|
| `scripts/run_futures_depth_poller.py` | **No** — gated `FIELD_MAPPING_VERIFIED = False`; only DISCOVERY logging | Prepared, blocked by an unresolved decision (§5) |
| `scripts/discover_depth_response_shape.py` | No — by design, discovery only | Complete, its job is done (real capture exists) |
| `scripts/discover_option_chain_premium_fields.py` | No — by design, discovery only | Complete, its job is done |
| `scripts/certify_vix_access.py` / `certify_websocket_access.py` | No — certification artifacts only | Complete |

**Zero collectors write to Layer 0 today.** The depth poller is the only
one with the write-path code present at all, and it's structurally
disabled.

## 2. Existing capture pipeline

**Fully built, fully tested, exercised only by tests:**
`bujji.market_reality.capture.build_raw_observation()` — constructs a
`RawObservation` from already-normalized scalars, no network, no
interpretation. `RawObservationStore.append(raw, now)` — validates,
routes to accepted/rejected, certification-gated, idempotent on
duplicate `observation_id`. Both have run thousands of times in tests
(5,395+ passing tests touch this path indirectly); **neither has ever
run against a live broker call.**

## 3. Existing Layer 0 storage

`RawObservationStore` auto-creates its directory
(`Path(directory).mkdir(parents=True, exist_ok=True)`, `store.py`
`__init__`) — **no pre-existing `layer0_data/` directory is required**;
the first real write creates it. Confirmed: no such directory exists on
the VPS today. This is not a setup blocker, it's a non-event — the store
is ready the moment something calls it.

## 4. Existing `RawObservation` builder

Already flexible enough for the minimum case with **zero changes**:
`taxonomy.REQUIRED_PAYLOAD_FIELDS[KIND_QUOTE] = ("ltp",)`,
`REQUIRED_IDENTITY_FIELDS[INSTRUMENT_SPOT] = ()` — **a spot quote
observation needs exactly one payload field and zero identity fields.**
This is the simplest possible construction this schema supports, and it
already exists.

## 5. Existing Market Timeseries storage

`CandleStore`/`FuturesStatsStore` — built, tested, zero real rows,
**out of scope for "first observation."** A materializer needs Layer 0
rows to read; it cannot be the first thing populated. Not blocking
Phase 17I, simply downstream of it.

## 6. Existing certification gates — the decisive finding

**Checked the real, current artifacts on disk, not assumed:**

| Artifact | Instrument | Access method | Result |
|---|---|---|---|
| `fyers_nifty_spot_certification.json` | `NIFTY_SPOT` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` |
| `fyers_nifty_future_certification.json` | `NIFTY_FUTURES` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` |
| `fyers_india_vix_certification_20260813.json` | `INDIA_VIX` | `direct_sdk_fyers_broker_py` | `CERTIFIED_AVAILABLE` |

**A spot or futures QUOTE write via `direct_sdk_fyers_broker_py` passes
the certification gate RIGHT NOW, with no further action.** This is not
a blocker — it's already cleared. (Depth remains blocked — separate
issue, §5 of Blockers.)

## 7. Existing replay patterns

`replay()`/`replay_stream()` (dual-bound, 17F.5) already correctly
handle a store with exactly one record, or zero — no minimum-volume
assumption anywhere in the replay path. **The first single real
observation is immediately replayable, provably, the moment it's
written.**

## 8. Existing tests and fixtures

Every construction step needed for a spot/futures observation is
already exercised, live-shaped, in `tests/test_market_reality_store.py`
/`test_capture_events.py`'s own `_obs()`/`_tick()` helpers — these ARE
the pattern a first real collector would follow, just with a real
`FyersBroker.get_spot()` call substituted for the test's literal value.

---

## What can be reused immediately (no new code)

- `FyersBroker.get_spot(underlying) -> float` — live-verified since
  2026-08-12, reconfirmed live again in Gate B (2026-08-13 websocket
  price-scaling cross-check).
- `build_raw_observation()`, `RawObservationStore`, `CertificationGate` —
  complete, tested, certification already `CERTIFIED_AVAILABLE` for the
  exact access path needed.
- The market-hours-gate + broker-connect + try/except skeleton already
  present in `run_futures_depth_poller.py` / every `certify_*.py`/
  `discover_*.py` script — the exact shape a new collector script would
  copy, not invent.

## Smallest collector that can produce the first real observation

**A spot LTP collector — not depth, not futures, not options.**
Concretely: `get_spot("NIFTY")` → one `RawObservation`
(`kind=KIND_QUOTE`, `instrument="NSE:NIFTY50-INDEX"`,
`instrument_type=INSTRUMENT_SPOT`, `payload={"ltp": <real value>}`,
`identity_fields={}`) → `RawObservationStore.append()`. **Zero required
identity fields, one required payload field, certification already
cleared.** This is strictly simpler than futures (`expiry` required
identity field) and options (three required identity fields, plus the
unresolved expiry-resolution question from 17H.2.0).

## Where should the first live write happen?

A new, small, market-hours-gated script (`scripts/`), mirroring
`certify_vix_access.py`'s exact shape — **not** inside
`run_futures_depth_poller.py` (that script's `live` path is entangled
with the unresolved depth field-mapping decision) and **not** inside
`LiveMarketDataProvider` (17H.1 Decision 3: that component never writes
to Layer 0 at all, by design). A standalone script is the correct,
smallest, least-coupled first mover.

## Minimum code change

One new script, roughly the shape of `certify_vix_access.py` minus the
certification-classification logic (certification already exists;
nothing new to classify) — construct `CertificationGate` +
`RawObservationStore`, call `get_spot()`, build one `RawObservation`,
append it, print the `AppendResult`. **No change to any existing file.**
No new dataclass, no new taxonomy entry, no new store method.

## What must NOT be built yet

- Depth writes (field-mapping + cert-granularity decisions still open).
- Option chain writes (expiry-resolution decision still open, 17H.2.0
  Decision 3).
- Any materializer run against the new data (nothing to materialize from
  one row; premature).
- Any continuous/looping collector — a single, manually-run,
  one-shot script proving the write path end-to-end is the correct
  first step, not a long-running service.
- `CaptureLifecycleTracker` wiring — no sustained polling exists yet to
  have lifecycle state about (consistent with 17H.1 Decision 4's own
  reasoning: no continuous connection, nothing to track yet).

## Blockers preventing first live observation

**None, for spot.** Every prerequisite is already real, tested, and
certified. The only "blocker" is that no one has run the (not yet
written) script.

For completeness, blockers that exist for the NEXT collectors, not this
one:
1. Depth: `ask`→`asks` mapping decided but not built; certification
   granularity gap (17F.5) still open.
2. Options: expiry resolution (`InstrumentMaster.resolve_by_symbol()`,
   17H.2.0 Decision 3) not built.
3. One real, unverified fact worth flagging before even the spot
   script is written: the `ltp` REST response's `v` dict has never been
   captured RAW for spot (only `lp` is ever extracted — same
   narrow-extraction pattern already found and fixed twice, for
   `get_option_chain()`→`_raw()` and `get_depth()`). **Whether the real
   spot quote response carries an exchange timestamp is UNKNOWN, not
   verified either way** — existing test fixtures show only `lp` because
   that's all the test needed, not because it's confirmed to be all
   FYERS sends. If a real timestamp exists, `event_timestamp` could be
   real instead of `None`; if not, `None` is correct. **Recommend a
   one-line addition to the first collector's own run: log the FULL raw
   `v` dict once, the same discovery discipline already used for depth
   and option chain, rather than assuming `lp`-only.** This is
   observation, not a code change to existing files.

---

## Summary

**"Bujji observes the market for the first time" requires exactly one
new, small, market-hours-gated script calling one already-verified
broker method, through an already-built, already-tested, already-certified
pipeline. No architecture is missing. The only actual blocker was that
nothing had been asked to run yet.**
