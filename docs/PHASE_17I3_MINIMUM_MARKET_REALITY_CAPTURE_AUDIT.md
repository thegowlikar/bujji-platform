# Phase 17I.3 — Minimum Market Reality Capture: Implementation Plan

**Status: AUDIT + PLAN ONLY. No code in this phase.**

Every fact below is read directly off the VPS this session: `FyersBroker`
methods, real `data_certification/*.json` artifacts, `taxonomy.py`'s
actual constants, `validator.py`'s actual enforcement logic, and
`InstrumentMaster`'s actual CSV-parsing code — not carried over from
memory. Builds directly on
[docs/PHASE_17I1_FIRST_OBSERVATION_STORAGE_AUDIT.md](PHASE_17I1_FIRST_OBSERVATION_STORAGE_AUDIT.md)
and the shipped [scripts/capture_first_spot_observation.py](../scripts/capture_first_spot_observation.py).

---

## Headline finding

**Futures and VIX are the same shape of problem spot already solved.
Option chain is not — it hits a genuine, unresolved structural question
that no amount of "just write the collector" resolves.** The three are
NOT equally minimal. Plan accordingly: two are ready now, one needs a
decision first.

---

## 1. NIFTY Futures

**Broker method**: `FyersBroker.get_futures_quote(underlying) -> Optional[dict]`
(fyers.py:508) — real, returns `{"symbol", "ltp", "volume", "oi"}`.
`oi` is best-effort via a secondary `depth` call; `ltp`/`volume` come
from the primary `ltp` quote call. Returns `None` on missing/invalid
`ltp`, never raises for a "no data" case (only a genuine broker/auth
error raises).

**Certification**: `fyers_nifty_future_certification.json` — verified on
disk: `instrument=NIFTY_FUTURES`, `access_method=direct_sdk_fyers_broker_py`,
`validation_result=CERTIFIED_AVAILABLE`. Already cleared, no new
certification run needed.

**Taxonomy fit**: `KIND_QUOTE`/`INSTRUMENT_FUTURE`. `REQUIRED_PAYLOAD_FIELDS[KIND_QUOTE] = ("ltp",)`
— satisfied directly by `get_futures_quote()["ltp"]`. `volume`/`oi` are
neither required nor forbidden (`FORBIDDEN_PAYLOAD_FIELDS` covers only
derived analytics like `iv`/`delta`/`vwap` — raw `volume`/`oi` are fine
to include).

**The one real blocker — expiry identity, unresolved**:
`REQUIRED_IDENTITY_FIELDS[INSTRUMENT_FUTURE] = ("expiry",)`, and the
validator (`validator.py:78-84`) rejects a FUTURE record with no
non-empty `expiry` string. There is **no authoritative source for this
value today**:
- `_futures_symbol(underlying, now)` (fyers.py:89) constructs the FYERS
  *symbol string* (`NSE:NIFTY26AUGFUT`) from the wall clock — its own
  docstring calls this "best-effort... has NOT been live-verified...
  provisional." It returns a symbol, not an expiry date, and cannot be
  parsed back into one without duplicating FYERS's own month-code
  convention informally.
- `InstrumentMaster._rows_for()` (instrument_master.py:113-136), the
  project's one authoritative expiry source, explicitly **discards every
  non-CE/PE row**: `if opt_type not in ("CE", "PE"): continue  # Skip
  futures`. Futures expiry is not merely un-exposed by a missing method
  — the parser throws the row away before it could be exposed at all.

**Missing decision**: where does a real, dated futures expiry come from?
Either (a) stop discarding futures rows in `InstrumentMaster`'s CSV
parse and add a real resolver, or (b) accept `_futures_symbol()`'s
provisional month-code as a stand-in identity value pending live
verification, explicitly flagged as unverified. This is a decision, not
an implementation detail — do not silently pick (b) inside a collector
script.

## 2. India VIX

**Broker method**: `FyersBroker.get_vix() -> Optional[dict]`
(fyers.py:422) — real, live-verified 2026-07-20 and again in Gate B
(2026-08-13). Returns `{"level": float, "prev_close": float | absent}`.

**Certification**: `fyers_india_vix_certification_20260813.json` —
verified on disk: `instrument=INDIA_VIX`,
`access_method=direct_sdk_fyers_broker_py`,
`validation_result=CERTIFIED_AVAILABLE`. Already cleared.

**Taxonomy fit**: `KIND_QUOTE`/`INSTRUMENT_INDEX`.
`REQUIRED_IDENTITY_FIELDS[INSTRUMENT_INDEX] = ()` — **zero identity
fields, same as SPOT.** `REQUIRED_PAYLOAD_FIELDS[KIND_QUOTE] = ("ltp",)`
— the only translation needed is the payload key: `get_vix()` returns
`level`, the schema wants `ltp`. This is the exact same "normalize at
the collector boundary, never change the taxonomy" pattern already
locked for `ask`→`asks` (17H.1 Decision 1) — `payload={"ltp": result["level"]}`,
optionally also keeping `prev_close` (not forbidden, not required).

**Blockers**: **none.** This is structurally as simple as spot — the
only difference from `capture_first_spot_observation.py` is the broker
call and the `level`→`ltp` key rename. No open decision required.

## 3. NIFTY Option Chain snapshots

**Broker method**: `FyersBroker.get_option_chain_raw(underlying,
strike_count) -> Optional[dict]` — real, raw pass-through of the
`optionchain` endpoint. Real per-strike fields (Gate B,
`fyers_option_chain_discovery_20260813.json`): `ask, bid, fyToken, ltp,
ltpch, ltpchp, oi, oich, oichp, option_type, prev_oi, strike_price,
symbol, volume`. **No expiry field on any strike row.**

**Certification**: `fyers_option_chain_certification.json` — verified on
disk: `instrument=NIFTY_OPTION_CE`,
`access_method=direct_sdk_fyers_broker_py`,
`validation_result=CERTIFIED_AVAILABLE`. Cleared for `INSTRUMENT_OPTION`
specifically — **not** for any other instrument type.

**The real structural problem — not a missing field, a missing decision**:

`REQUIRED_PAYLOAD_FIELDS[KIND_OPTION_CHAIN] = ("strikes",)` implies one
record per whole-chain snapshot. But `KIND_OPTION_CHAIN` has **never
been used anywhere in this codebase** (confirmed by repo-wide grep —
zero test coverage, zero real callers) and its instrument-type pairing
is genuinely ambiguous between two options, both structurally valid,
neither yet chosen:

- **(a) One `KIND_QUOTE`/`INSTRUMENT_OPTION` record per strike/side**
  (reusing exactly the spot/futures/VIX pattern, not `KIND_OPTION_CHAIN`
  at all). Correctly gated by the real `NIFTY_OPTION_CE` certification.
  But `REQUIRED_IDENTITY_FIELDS[INSTRUMENT_OPTION] = ("expiry", "strike",
  "option_type")` needs a real expiry **per contract**, and:
  - the chain response carries no expiry field to read it from directly;
  - `InstrumentMaster` exposes only `resolve_atm()` (search by
    spot+side+interval) — **no `resolve_by_symbol()` or
    `resolve_by_strike()`** exists to map a raw chain row's
    `strike_price`/`option_type` (or its `symbol`, e.g.
    `NSE:NIFTY2681824100CE`) back to a real, authoritative expiry. This
    is the same gap named in 17H.2.0 Decision 3, still open.
- **(b) One `KIND_OPTION_CHAIN`/`INSTRUMENT_OPTION` record per snapshot**,
  packing every strike into `payload["strikes"]`. Technically satisfies
  the validator (payload has a `strikes` key; identity fields could
  reference one anchor contract), but was never designed or tested for,
  and conflates "one record = one identifiable contract" (true
  everywhere else in Layer 0) with "one record = many contracts."
  Whether this is even the intended reading of `KIND_OPTION_CHAIN` is
  itself unconfirmed — no design doc defines it.

Using `INSTRUMENT_SPOT` (zero identity fields, avoiding the expiry
problem entirely) is **not a safe workaround**: `CertificationGate.status_for()`
would then look up the `NIFTY_SPOT` certification, not `NIFTY_OPTION_CE`
— a chain write would ride on a certification that never tested chain
access at all. Certification lineage would be false.

**Missing decisions, plural, genuinely open**:
1. Per-contract records (a) vs. whole-snapshot record (b) — an actual
   design choice, not yet made anywhere in this project's docs.
2. If (a): where does per-contract expiry come from — a new
   `InstrumentMaster` method resolving by strike/symbol (the same gap
   17H.2.0 already named and deferred)?
3. If (b): what does "identity" mean for a multi-contract record, and
   has `KIND_OPTION_CHAIN`'s `("strikes",)` payload contract actually
   been designed for real use, or only stubbed?

---

## Implementation plan

Ordered by actual readiness, not by the order requested:

### Step 1 — India VIX (ready now, zero open decisions)
New script, `scripts/capture_first_vix_observation.py`, mirroring
`capture_first_spot_observation.py` exactly: same market-hours gate,
same `CertificationGate`/`RawObservationStore` construction against the
same shared `data_certification/`/`layer0_data/` directories, swap
`get_spot()` for `get_vix()`, swap `INSTRUMENT_SPOT` for
`INSTRUMENT_INDEX`, map `result["level"]` → `payload["ltp"]`. No
taxonomy change, no new decision needed. This is the next smallest
extension, full stop.

### Step 2 — NIFTY Futures (ready after ONE decision: futures expiry source)
Once the expiry-source decision above is made (not before — writing a
futures collector today means either fabricating an unverified value or
building against a nonexistent method), the collector itself is the
same three-call shape again: `get_futures_quote()` →
`build_raw_observation(kind=KIND_QUOTE, instrument_type=INSTRUMENT_FUTURE,
identity_fields={"expiry": <resolved>})` → `store.append()`. Certification
is already cleared; only the identity-field source is missing.

### Step 3 — NIFTY Option Chain (blocked on a real design decision, largest of the three)
Do not implement until decisions 1–3 above are made. This is
categorically different work from Steps 1–2 — it is not "write one more
thin script," it is "decide what an option chain observation even is at
Layer 0," which the taxonomy has left open since it was defined. Once
decided:
- If (a): needs the same `InstrumentMaster` extension already flagged in
  17H.2.0 (a `resolve_by_symbol()`/`resolve_by_strike()` method), then N
  per-strike `KIND_QUOTE`/`INSTRUMENT_OPTION` writes per snapshot —
  reusing everything else unchanged.
- If (b): needs a first real definition of what `KIND_OPTION_CHAIN`'s
  identity fields mean for a multi-contract record before any code is
  written, since the validator's current per-field check assumes one
  scalar identity per record.

## What must NOT be built yet (unchanged from 17I/17I.1/17I.2)

Materializers, Market Memory, any intelligence layer, any continuous
collector loop, any new taxonomy entry, any new dataclass — none of the
three extensions above require or justify any of that.

## Files/classes to reuse (all three)

`RawObservationStore`, `CertificationGate`, `build_raw_observation()`,
`taxonomy.KIND_QUOTE`/`INSTRUMENT_INDEX`/`INSTRUMENT_FUTURE`, and the
`capture_first_spot_observation.py` script itself as the literal
template — no existing file needs modification for VIX or (once
decided) futures.

## Files that should not be touched

Same list as 17I.1: `market_reality/*.py`, `state_persistence/*.py`,
`run_futures_depth_poller.py`. Additionally for this phase:
`instrument_master.py` should not be modified as a side effect of
writing the futures or options collector — if Decision (futures expiry
source) or Decision 2 (option expiry resolution) concludes it needs a
new method, that is its own scoped change, decided and reviewed on its
own, not bundled into a collector script.
