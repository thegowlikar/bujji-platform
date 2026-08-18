# Phase 17H.3 — Historical Reality Certification + Ingestion Contract

**Status: DESIGN ONLY. No code. No ingestion. No indicators. No
strategy logic. No live capture code modified.**

Supersedes one piece of [PHASE_17H2_HISTORICAL_REALITY_SCHEMA.md](PHASE_17H2_HISTORICAL_REALITY_SCHEMA.md):
its Part 2.1 (`HistoricalCandle`) is replaced by `HistoricalObservation`
below, per the corrected architecture —

```
Reality Layer
     |
     +-- Live Capture (FYERS realtime)     -> RawObservation
     +-- Historical Ingestion (FYERS hist) -> HistoricalObservation
     |
     Candle Builder (downstream of either)
     |
     Candle Model (existing, unified — bujji.market_timeseries.Candle)
```

`HistoricalObservation` is a raw-tier fact — *"FYERS told us this
existed historically"* — not a candle. It never carries `tick_count`/
`source_observation_ids`/`capture_event_overlap`, because those describe
ticks Bujji itself observed, which a 1998 bar never had. When a candle
is genuinely needed downstream, a Candle Builder (not scoped by this
document — a Phase 17H.5+ concern) constructs one from either a folded
tick series (live) or a `HistoricalObservation` (historical), landing in
the **same existing `Candle` model** either way. Parts 1, 3, 5, 6, 7, 8
of the 17H.2 document (storage layout, engine decision, timezone rules,
validation rules, futures identity, migration path) remain valid
unchanged and are not restated here.

---

## Part 1 — Certification model for `fyers_historical`

### 1.1 The real gap, stated precisely

`CertificationGate.status_for(access_method, instrument_type)` looks up
`(cert_key, access_method)` — a pair, not `instrument_type` alone (this
exact collision was found and fixed once already, Phase 17G.0's Gate B
audit, when REST and websocket certifications for the same instrument
shared one key and silently shadowed each other). **Historical must not
repeat that.**

Live quotes today certify under `access_method =
"direct_sdk_fyers_broker_py"`. Historical calls run through the exact
same `FyersBroker._call()` choke-point, on the exact same authenticated
connection — but asking `historical` for daily OHLC and asking `ltp`
for a live quote are different capabilities with different failure
modes (verified: `ltp` never returns `code=-50`; `historical` does, on
range violations). **If historical reused the live access_method
value, `gate.status_for("direct_sdk_fyers_broker_py", INSTRUMENT_SPOT)`
would already return `CERTIFIED_AVAILABLE` today — a false positive,
since historical access has never actually been certified.** This is
the same collision class already fixed once; the fix here is not to
repeat the mistake it fixed.

**Decision: a new, distinct access-method value —
`"direct_sdk_fyers_historical_rest"`.** Same broker, same SDK, same
connection; a different, separately-certified capability.

### 1.2 Cert-key mapping — reused unchanged

`INSTRUMENT_TYPE_TO_CERT_KEY` (`SPOT→"NIFTY_SPOT"`,
`FUTURE→"NIFTY_FUTURES"`, `INDEX→"INDIA_VIX"`) is instrument-type
vocabulary, not access-method-specific — reused verbatim, no change. The
new access-method value combined with this existing mapping means three
new artifacts are needed:

```
fyers_nifty_spot_historical_certification_<YYYYMMDD>.json
fyers_nifty_future_historical_certification_<YYYYMMDD>.json
fyers_india_vix_historical_certification_<YYYYMMDD>.json
```

Filename convention matches the existing dated-artifact pattern
(`fyers_india_vix_certification_20260813.json`) exactly — a re-run on a
later day produces a new artifact, `CertificationGate` resolves by
sorted-filename order as it already does, no gate-logic change required.

### 1.3 Artifact shape — reused, not reinvented

Same flat-JSON shape every existing certification artifact already
uses (`certify_vix_access.py`'s own `cert` dict), with fields relevant
to historical made explicit rather than left implicit:

```
{
  "timestamp": "...",
  "broker": "fyers",
  "access_method": "direct_sdk_fyers_historical_rest",
  "instrument": "NIFTY_SPOT",                    # from the existing cert-key mapping
  "symbol_requested": "NSE:NIFTY50-INDEX",
  "symbol_returned": "NSE:NIFTY50-INDEX",         # echoed, same integrity check as live certs
  "resolution_tested": "D",
  "range_tested": ["2025-08-13", "2026-08-13"],   # the ≤366-day window actually probed
  "historical_status": "OK",                       # OK | NO_DATA | ERROR
  "earliest_date_confirmed": "1998-01-05",          # a real bisected finding, not a query on every call
  "cont_flag_tested": true,                          # futures artifact only
  "volume_available": true,
  "oi_available": false,                              # structurally, always false for FYERS historical
  "integrity_checks_passed": true,
  "validation_result": "CERTIFIED_AVAILABLE",
  "limitations": [
    "OI is never available from FYERS historical candles, at any resolution — Bhavcopy only.",
    "Per-request range is capped at 366 days — see PHASE_17H1_HISTORICAL_SOURCE_AUDIT.md."
  ]
}
```

**`earliest_date_confirmed` is a one-time bisection result, cached on
the artifact** — re-running the full 1996→2026 bisection on every
certification refresh would be wasteful and market-hours-independent
(historical data doesn't change); a fresh cert run re-validates current
*access* (symbol echo, a live probe call succeeding), not the entire
depth bisection from 17H.1, which stays cited by reference.

### 1.4 Why certify at all — the stated principle, made concrete

*"No reality enters memory without provenance"* — restated precisely:
without this, `HistoricalObservation.certification_status` (§2.1) would
have nothing real to reference, and per the fail-closed discipline
already governing Layer 0 (`WRITE_PERMITTED_CERTIFICATION_STATES =
(CERTIFIED_AVAILABLE,)`), an uncertified source should be refused, not
silently accepted. This is not a new principle for historical data — it
is the existing principle, applied to a third access method, exactly as
17H.1's Part 3 already concluded no new architecture is needed here.

### 1.5 What this certification does NOT need to be

Not market-hours-gated. Every live/discovery script in this project
gates on market hours because a quote fetched when the market is closed
is stale/meaningless. Historical data has no such property — 1998's
data is exactly as valid to certify at 11pm as at 11am. **A
`certify_fyers_historical_access.py` script (not built by this
document) should explicitly NOT inherit the market-hours gate from its
siblings** — inheriting it by copy-paste habit would be a real,
avoidable mistake worth flagging now, before it's written.

---

## Part 2 — `HistoricalObservation` contract

### 2.1 Fields

```
observation_id         str    # content-hash, deterministic — see §2.2
instrument_identity    str    # canonical identity: "NSE:NIFTY50-INDEX" | "NSE:INDIAVIX-INDEX"
                               # | "NIFTY_FUT_CONTINUOUS" — NEVER the raw request symbol (§2.4)
instrument_type        str    # reuse market_reality.taxonomy: SPOT | FUTURE | INDEX
source                 str    # "fyers_historical"
access_method          str    # "direct_sdk_fyers_historical_rest" (Part 1)
resolution             str    # reuse market_observation.taxonomy RESOLUTION_*: DAILY, ...
timestamp              str    # window_start, ISO8601+IST, session-boundary-derived (17H.2 Part 5)
source_epoch           int    # raw epoch exactly as FYERS returned it — never discarded
source_symbol          str    # the literal symbol string used in the request (§2.4)
open                   float
high                   float
low                    float
close                  float
volume                 Optional[float]   # None for index structural zero, never coerced (17H.2 V6)
open_interest           None              # ALWAYS None from this source — present as a field so its
                                            # permanent absence is visible, not omitted from the shape
origin                  str    # ORIGIN_HISTORICAL_RECONSTRUCTION — existing constant, reused
continuity_method       Optional[str]     # "fyers_cont_flag_1" for continuous futures; else None
raw_artifact_ref        str    # pointer into raw_artifacts/ (17H.2 Part 1)
ingestion_run_id        str    # FK -> IngestionRun (17H.2 §2.3)
retrieved_at            str    # ISO8601+IST — when Bujji fetched it, distinct from `timestamp`
certification_status    str    # from Part 1's artifact, at ingestion time
certification_ref       str    # artifact filename + its own timestamp, same pattern as Layer0Lineage
schema_version          str
```

### 2.2 `observation_id` — minted the same way Layer 0 already does it

**Not reinvented.** Layer 0's `observation_id` is a deterministic
content hash over identity + value, minted in exactly one place
(`market_observation.engine.build_observation()`) so Layer 0 and MOC
can never disagree about identity. `HistoricalObservation` should mint
its id through the **same function**, keyed on
`(instrument_identity, resolution, timestamp, source, open, high, low,
close, volume)` — giving it the same free property Layer 0 already
has: re-ingesting the identical fact twice produces the identical id,
making duplicate detection structural rather than a separately-written
check (§3.1).

### 2.3 Why `open_interest` stays in the shape as an always-null field

Two options existed: omit the field entirely, or keep it always-`None`.
**Keeping it** was chosen so the shape is directly comparable to the
live `RawObservation` payload contract
(`taxonomy.REQUIRED_PAYLOAD_FIELDS[KIND_CANDLE] = ("open","high","low","close")`
— OI was never required there either, but it's a recognized concept in
the same domain). An always-null field with a documented reason (Part
1's `limitations`) is honest; a silently-omitted field invites a future
reader to wonder whether it was simply never implemented.

### 2.4 Futures identity — the corrected rule, made explicit and binding

**`instrument_identity` is never the request symbol.** 17H.1's own
probe requested `NSE:NIFTY26AUGFUT` for calendar-year 2025 — a contract
that did not exist yet — and got 249 real, continuous-series candles
back. Storing those under `NSE:NIFTY26AUGFUT` would assert the Aug-2026
contract traded in January 2025, which is false. The rule:

| `continuity_method` | `instrument_identity` | `source_symbol` |
|---|---|---|
| `"fyers_cont_flag_1"` | `"NIFTY_FUT_CONTINUOUS"` (fixed, one identity for the whole series) | whatever symbol the request happened to use (an API access detail, preserved for audit, never used to interpret the data) |
| `None` (specific-expiry request) | the real contract symbol, e.g. `"NSE:NIFTY26AUGFUT"` | same as `instrument_identity` |

A query joining historical futures reality never needs to know which
symbol string a given continuous-series row was fetched under — it
reads `instrument_identity="NIFTY_FUT_CONTINUOUS"` uniformly.
`FuturesIdentity` (17H.2 §2.2) remains the place `expiry_date` /
`expiry_epoch` live for `SPECIFIC_EXPIRY` rows, sourced from
`InstrumentMaster`, never parsed from a symbol.

---

## Part 3 — Ingestion rules

### 3.1 Append-only, no overwrite, source-immutable

Same three-outcome discipline `RawObservationStore.append()` and
`CandleStore.write_candle()` already implement, reused rather than
reinvented:

- **New fact** (`observation_id` not previously seen) → appended.
- **Identical fact re-ingested** (same `observation_id`, §2.2's
  content-hash guarantees this means identical content) → idempotent
  no-op. A re-run of the same ingestion request is safe by construction.
- **Different content under an existing natural key**
  (`instrument_identity`, `resolution`, `timestamp`, `source`) but a
  *different* `observation_id` (meaning FYERS actually returned a
  different OHLCV for a date already ingested — e.g. a source
  correction) → **REJECTED as a conflict**, never silently overwritten.
  Surfaced the same way `ConflictingCandleError`/`RawObservationStore`'s
  rejection path already surfaces one — a human reviews it, decides
  whether the new value supersedes the old via an explicit, recorded
  action, never an automatic overwrite.

### 3.2 No duplicate dates

Enforced structurally by §3.1's content-hash identity — not a
separately-maintained uniqueness rule to keep in sync with the hash
logic.

### 3.3 Missing candles are explicit, never fabricated

Governed by `IngestionRun.status` (17H.2 §2.3): `NO_DATA` (FYERS
genuinely has nothing for that window — e.g. pre-1997 spot, pre-2008
VIX), `ERROR` (a rejected request — e.g. >366-day range, code -50), and
a normal `OK` run that simply has no bar for a specific date (a market
holiday) are **three different facts**, never collapsed. No forward-fill,
no interpolation — same principle already enforced in
`CandleAggregator` and Layer 0.

### 3.4 Chunking is structural, not a retry parameter

Every ingestion request is ≤366 days (17H.1 §1.1's measured limit),
one `IngestionRun` per chunk. A 28-year spot backfill is ~28
`IngestionRun` rows, each independently re-runnable, each independently
inspectable in `IngestionRun`/`QualityReport` — not one giant opaque
job that either fully succeeds or leaves an unclear partial state.

### 3.5 Timezone rules

Reused from 17H.2 Part 5 unchanged: explicit IST offsets always; daily
bars reconstructed from the real trading session
(`09:15:00`–`market_close`), never the raw UTC-midnight epoch rendered
naively; market close is date-dependent (`15:30` before 2026-08-03,
`15:40` from that date, per the verified NSE circular).

### 3.6 Certification gate at write time

Every `HistoricalObservation.append()`-equivalent call resolves
`certification_status`/`certification_ref` from Part 1's gate at write
time (same pattern `RawObservationStore.append()` already uses,
including its own re-stamping behavior when a newer artifact supersedes
the one referenced at construction time) — an ingestion run started
before a certification lapses and finished after does not silently
retain a stale claim.

---

## Part 4 — Futures rules (restated as binding, not advisory)

1. `contract_type ∈ {CONTINUOUS, SPECIFIC_EXPIRY}` — always one or the
   other, never ambiguous.
2. Continuous rows always carry `instrument_identity =
   "NIFTY_FUT_CONTINUOUS"` and `continuity_method =
   "fyers_cont_flag_1"` — never the request symbol (§2.4).
3. Specific-expiry rows always carry a real `expiry_date`/`expiry_epoch`
   sourced from `InstrumentMaster`, never inferred from the symbol
   string.
4. OI is **never** populated on any `HistoricalObservation`, from
   either contract type — structurally permanent (17H.1 §1.2), not an
   engineering gap to close later. Bhavcopy remains the only path to
   historical futures OI.
5. **OPEN, carried forward from 17H.2, not resolved here**: whether
   `cont_flag=1` back-adjusts prices across rollovers is unverified.
   `continuity_method` on every affected row means this remains
   answerable and correctable later without a schema change.

---

## What Phase 17H.3 does not decide

- The Candle Builder itself (converts `HistoricalObservation` →
  `Candle` on demand) — a Phase 17H.5+ concern, not designed here.
- Whether/how `Candle.materializer_id`/`calc_version` get populated for
  a historical-sourced candle — depends on the Candle Builder's design.
- The actual `certify_fyers_historical_access.py` script and the actual
  ingestion script — both are Phase 17H.4 implementation, not this
  document.

## What is now settled and ready for Phase 17H.4

- Certification: new access-method value, reused artifact shape, reused
  cert-key mapping, no gate-logic change.
- Contract: `HistoricalObservation`, fields fixed, identity-minting
  reuses Layer 0's existing function.
- Futures identity: continuous vs. specific-expiry rule is binding.
- Ingestion discipline: append-only/conflict-not-overwrite,
  chunk-per-366-days, missing-data-is-explicit — all reused from
  existing, proven patterns, none invented new.

**Recommended next step: Phase 17H.4, "NIFTY Spot Daily First
Historical Ingestion"** — the smallest instrument (no futures-identity
complexity, no OI question) exercising this entire contract end-to-end
before Futures/VIX follow.
