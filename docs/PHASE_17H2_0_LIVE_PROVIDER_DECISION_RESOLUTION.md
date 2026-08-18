# Phase 17H.2.0 — Resolve LiveMarketDataProvider Open Decisions

**Status: DECISIONS ONLY. No code. No schemas. No dataclasses. No
provider implementation.**

Resolves the four open items from `PHASE_17H2_LIVE_MARKET_DATA_PROVIDER_
IMPLEMENTATION_PLAN.md` §7, using the real code inspected this session
(not the proposal's sketches taken at face value where they diverged from
what actually exists).

---

## Decision 1 — Source-specific completeness contract: `ObservationSourceProfile`, confirmed correct, one real gap found in the proposal

**Decided: yes — a per-source capability profile, not a Bhavcopy-shaped
hack applied to live data.** The observation model
(`OptionObservation`) is unchanged; only which fields count toward
`completeness` becomes source-aware.

**What already exists and must be reused, not rebuilt:** `taxonomy.py`
already has the exact shape needed —

```python
MANDATORY_OPTIONS_OBSERVATION_FIELDS = (OPEN, HIGH, LOW, CLOSE,
    SETTLEMENT, VOLUME, OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST)
KNOWN_UNAVAILABLE_FROM_BHAVCOPY = (BID, ASK, BID_QUANTITY, ASK_QUANTITY)
```

This is **already** a source-capability profile — it just has one
source (Bhavcopy) hardcoded into a single pair of module-level tuples
instead of being keyed by source. The proposal's
`ObservationSourceProfile` concept is the right generalization of a
pattern that already exists once; it is not a new architectural idea
being introduced, it is naming and parameterizing an existing one.

**Concrete profile for the second source, using ONLY fields verified in
Gate B** (not the proposal's example list, which named `LTP`/`bid`/`ask`/
`OI`/`symbol` as "required" — `symbol` is identity, not a completeness
field, and `LTP` has no `taxonomy.FIELD_*` constant today; corrected
below):

```
FYERS_LIVE_OPTIONCHAIN:
  mandatory   = (FIELD_OPEN_INTEREST, FIELD_CHANGE_IN_OPEN_INTEREST,
                 FIELD_VOLUME, FIELD_BID, FIELD_ASK)
  known_unavailable = (FIELD_OPEN, FIELD_HIGH, FIELD_LOW, FIELD_CLOSE,
                        FIELD_SETTLEMENT)
```

**Two corrections to the proposal's own example, found by checking
against real capture data rather than accepting the sketch:**

1. **LTP has no home in `taxonomy.ALL_OPTIONS_OBSERVATION_FIELDS` at
   all.** The existing field list is `OPEN, HIGH, LOW, CLOSE, SETTLEMENT,
   VOLUME, OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST, UNDERLYING_PRICE, BID,
   ASK, BID_QUANTITY, ASK_QUANTITY` — no `FIELD_LTP`. `OptionObservation`
   has no slot for "last traded price" distinct from OHLC's `close`. This
   is a real, structural gap the proposal's example glossed over: live
   REST's primary price signal (`ltp`) has nowhere to go in the existing
   model as it stands. **Not resolved here** — flagged as a genuine
   follow-on question (does `ltp` map onto `FIELD_CLOSE` semantically for
   a live/intraday context, or does it need its own field?). Naming it is
   this decision's job; deciding it is not, since it edges toward a
   schema question this phase's constraints explicitly forbid deciding
   unilaterally.
2. **`bid_quantity`/`ask_quantity` are also absent from live REST**
   (confirmed, Gate B: `optionchain` rows carry scalar `bid`/`ask` price
   only, no size) — the proposal's profile sketch didn't mention them at
   all; they belong in `FYERS_LIVE_OPTIONCHAIN`'s `known_unavailable`
   set too, alongside OHLC/settlement.

**Design shape (documentation only — not implemented):**

```
ObservationSourceProfile:
    source_id: str
    mandatory_fields: Tuple[str, ...]
    known_unavailable_fields: Tuple[str, ...]

PROFILES = {
    "bhavcopy": ObservationSourceProfile(
        mandatory_fields=MANDATORY_OPTIONS_OBSERVATION_FIELDS,   # unchanged, reused
        known_unavailable_fields=KNOWN_UNAVAILABLE_FROM_BHAVCOPY,  # unchanged, reused
    ),
    "fyers_live_optionchain": ObservationSourceProfile(
        mandatory_fields=(OPEN_INTEREST, CHANGE_IN_OPEN_INTEREST, VOLUME, BID, ASK),
        known_unavailable_fields=(OPEN, HIGH, LOW, CLOSE, SETTLEMENT, BID_QUANTITY, ASK_QUANTITY),
    ),
}
```

`build_option_observation()`'s existing completeness math (`present_mandatory
/ len(MANDATORY_OPTIONS_OBSERVATION_FIELDS)`) would need to accept a
profile parameter instead of reading the two module-level constants
directly — a small, additive, backward-compatible signature change
(default profile = the existing Bhavcopy constants, so every current
caller is unaffected). **This is the one, minimal schema-adjacent
touchpoint this whole document identifies — not authorized for
implementation here, but named precisely so it isn't discovered as a
surprise during 17H.2.1.**

---

## Decision 2 — Spot failure semantics: `MarketDataUnavailableError`, confirmed, with the exact translation point identified

**Decided: `LiveMarketDataProvider` translates every `FyersBroker.
get_spot()` failure into `MarketDataUnavailableError`. It never returns
`None` from a caught exception.**

**Reasoning, matching the proposal's own — and consistent with what
`ReplayChainProvider` already does:** `ReplayChainProvider.get_spot()`
never has an exception to catch (it's a bare attribute return), but its
`_ensure_loaded()` already raises `MarketDataUnavailableError` for an
invalid spot (`spot is None or spot <= 0`) rather than letting a bad
value reach `get_spot()`'s return path at all. `LiveMarketDataProvider`
should mirror this exactly: validate/fetch spot **during the same
internal call that populates the cached chain** (§2 of the prior
document — `get_spot()` never independently hits the network), and raise
`MarketDataUnavailableError` right there if `FyersBroker.get_spot()`
raises `KeyError` or `AuthenticationError`, or returns a non-positive
value. `get_spot()` itself, called later, remains a bare attribute
return — exactly like `ReplayChainProvider`'s.

**This resolves §7 item 1 of the implementation plan** (spot failure
propagation timing) definitively: failure is detected and raised inside
the internal "ensure loaded" step, not inside `get_spot()` itself. The
`Optional[float]` in the ABC's signature is satisfied structurally
(the method's return type still matches), but in practice
`LiveMarketDataProvider` never returns `None` from a caught failure —
only `MarketDataUnavailableError`, exactly as `ReplayChainProvider`
already established as this codebase's real precedent for "provider
failure," ahead of the ABC's own (looser) declared contract.

**One thing worth stating precisely, per the "None could mean broker
failure/holiday/symbol unavailable/coding bug" reasoning:** this
provider **never uses `None` as a genuine return value at all** under
this decision. `Optional[float]` in the ABC stays technically true (the
implementation is allowed to return `None`) but `LiveMarketDataProvider`
specifically chooses to always raise instead — an implementation choice
within the contract, not a violation of it.

---

## Decision 3 — Expiry resolution: `InstrumentMaster`, confirmed correct AND confirmed already real, live-verified, and structurally ready

**Decided: `InstrumentMaster`, per the proposal's recommendation — and
this is stronger than a preference, because the audit found the actual
mechanism already exists and already carries exactly what's needed.**

`bujji/broker/instrument_master.py`'s `InstrumentMaster` class:
- Downloads FYERS's real, public, unauthenticated F&O symbol-master CSV
  (`https://public.fyers.in/sym_details/{exchange}_FO.csv`), 24h TTL
  cache, live-verified during a **prior, separate** 2026-07-19 audit
  (Capital Management Engine v2) — not a new, unverified dependency.
- Parses every row into an `OptionRow(symbol, underlying, strike,
  option_type, expiry_epoch, lot_size)` — **`symbol` here is the exact
  same FYERS symbol format Gate B's `optionchain` rows carry**
  (`NSE:NIFTY2681824100CE`-shaped), and `expiry_epoch` is a real,
  per-contract epoch timestamp, convertible via the existing
  `OptionRow.expiry_date` property.
- `_rows_for(underlying)` already builds and caches the full row list
  in memory, keyed by underlying — the data a symbol→expiry lookup needs
  is already loaded and structured for exactly this purpose.

**The one real gap: no `resolve_by_symbol()` (or equivalent) method
exists yet.** `resolve_atm()` is the only public method, and it searches
by `(strike, option_type, nearest-expiry)`, not by an exact known
symbol string. Adding a symbol-keyed lookup is a **small, additive
method on an existing, already-verified class** — not a new dependency,
not a new download mechanism, not a redesign. This is the one concrete
implementation item this decision authorizes naming (not building):
`InstrumentMaster` needs one new small method,
e.g. `resolve_expiry_for_symbol(symbol: str) -> Optional[date]`, doing a
linear scan of the already-cached `_rows_for(underlying)` list for a
matching `symbol`.

**Symbol parser as validation fallback only — confirmed, not a source of
truth.** Per the proposal: if `InstrumentMaster`'s cache is stale/
unreachable (network failure on the public CSV), a live provider could
fall back to parsing the FYERS symbol grammar's embedded date-code
directly from `optionchain`'s own `symbol` field — but this is
explicitly a degraded fallback, never authoritative, and any such
fallback path is itself a `PARTIAL`/`known_unavailable`-worthy condition
on the resulting `OptionObservation`, not a silent equivalent to the
real lookup. **Not designed further here** (no parser is specified) —
named as a fallback that may or may not be worth building, deferred to
17H.2.1.

---

## Decision 4 — REST failure lifecycle handling: defer, confirmed correct, restated precisely

**Decided: defer, exactly as proposed, and for the reason already
established in 17H.1 (not a new argument, a restatement of one already
locked).** `LiveMarketDataProvider` is called twice per session, with no
continuous polling loop (confirmed twice now — 17H audit Part 1.2, and
again in the implementation plan §2 item 1). There is no "connection" to
disconnect from, and no sustained polling cadence against which a
failure-threshold policy could even be meaningfully defined at this
phase.

**Concretely, for `LiveMarketDataProvider` specifically:**
`AUTHENTICATION`/`RATE_LIMIT`/`TIMEOUT` failures surface as
`MarketDataUnavailableError` (Decision 2's mechanism, generalized to
every failure mode, not just spot) — a **provider-level failure**, which
the Trading Brain's existing `ConfigurationError`/exception handling in
`bujji_options_os_runner.py` already knows how to receive (confirmed,
17H's own audit: the pre-market check already catches
`MarketDataUnavailableError`-shaped failures and maps them to
`ConfigurationError`). **No `CaptureLifecycleTracker` call is made from
`LiveMarketDataProvider` at all, in this phase.** Capture lifecycle
remains exclusively a collector-level concern (17H.3+), per the standing
"wrong integration point is worse than no integration point" discipline
already applied once to the websocket adapter question and now applied
again here, correctly, to this component.

**No fake disconnect semantics are introduced** — confirmed as the
correct call. A two-calls-per-session provider has no meaningful
"connected" state to fake.

---

## On the golden fixture package (`tests/fixtures/gate_b/`)

**Agreed as a good, low-risk idea — not built in this document.** The
explicit instruction for this phase was decisions + documentation only;
building the fixture directory is file creation, not a decision, and the
next authorized phase (17H.2.1, provider implementation + its required
tests per the implementation plan §6) is the natural place for it — the
implementation plan already specified that test fixtures "should be
lifted directly from `fyers_option_chain_discovery_20260813.json`'s real
rows," which is the same idea, just not yet materialized into a
`tests/fixtures/gate_b/` directory. Recorded here as confirmed-agreed
scope for 17H.2.1, not deferred indefinitely.

---

## Summary — all four decisions resolved

1. **Source-capability profiles** (`ObservationSourceProfile`), keyed by
   source, generalizing the existing Bhavcopy-only pattern. One real gap
   found: `ltp` has no field slot in the existing model at all — named,
   not resolved.
2. **`MarketDataUnavailableError` for every spot failure**, detected
   during the same internal step that populates the cached chain,
   mirroring `ReplayChainProvider`'s existing precedent exactly.
3. **`InstrumentMaster`** for expiry resolution — confirmed already real,
   live-verified (a *different*, prior audit), and one small additive
   method away from usable. Symbol-string parsing is fallback-only.
4. **Defer failure-threshold/capture-lifecycle wiring entirely** for this
   provider — it has no continuous connection to model a threshold
   against, and capture lifecycle stays a collector-level concern.

**17H.2.1 (`LiveMarketDataProvider` implementation) may now be scoped
against these four resolved decisions. This document does not itself
authorize starting it.**
