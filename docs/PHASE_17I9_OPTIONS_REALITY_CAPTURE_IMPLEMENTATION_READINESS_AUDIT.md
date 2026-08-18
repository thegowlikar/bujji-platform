# Phase 17I.9 — Options Reality Capture Implementation Readiness Audit

**Status: AUDIT ONLY. No code, no ingestion, no storage created.**
Builds directly on 17I.5–17I.8's live-verified findings, plus one new
live check run today (token/symbol stability across two polls) and one
direct schema re-read (`HistoricalObservationStore`).

---

## 1. Is the existing options identity model sufficient?

| Field | Role | Sufficient as-is? |
|---|---|---|
| `underlying` | Identity | Yes — already `REQUIRED_IDENTITY_FIELDS`-adjacent via `expiry`/`strike`/`option_type`; underlying itself is the instrument's own base symbol, no ambiguity |
| `expiry` | Identity | Yes — already mandatory, enforced by the existing validator |
| `strike` | Identity | Yes — already mandatory |
| `option_type` | Identity | Yes — already mandatory, constrained to `ALL_OPTION_TYPES` |
| `instrument_token` (`fyToken`) | **Metadata, not identity** (17I.8 §5's own conclusion, reconfirmed) | Real, live, present on every response; correctly excluded from identity |
| `source_symbol` (the literal contract symbol, e.g. `NSE:NIFTY2681824350CE`) | **Lineage, not identity** — same binding rule as futures (17H.3 Part 2.4) | Correctly modeled this way already in `options_observation` (17I.5/17I.7) |

**The four-field identity tuple (underlying, expiry, strike, option_type)
is sufficient**, unchanged from 17I.8's conclusion — nothing new found
in this pass to revise it.

## 2. Contract lifecycle behaviour — live-verified today

**Token/symbol stability within a session**: tested live — two
`optionchain` polls, 5 seconds apart, same 10 real strikes returned
both times, and `fyToken` was **byte-identical** for every common
symbol checked (3 sampled directly, all matched). Confirms a listed
contract's token is stable across a session, not reissued per-poll.

**Expiry transition / weekly rollover**: **not independently tested
this session** (would require observing an actual rollover in
progress, which this audit's timing does not allow) — but the
already-established finding from 17I.6/17I.7 (an expired contract's
symbol becomes entirely `"Invalid symbol provided"` via the
`historical` endpoint) strongly implies tokens/symbols are scoped to a
contract's listed lifetime and not reused for a different contract
later — **inferred, not directly observed, and stated as an
inference, not a verified fact.**

**Weekly expiry rollover, structurally**: unlike futures (which have a
`cont_flag=1` continuity mechanism, 17H.3), **options have no
continuity concept at all** — each (strike, expiry, type) combination
is a genuinely distinct, terminal contract. This was already the
conclusion in 17I.7 §4 and is not revised here; it directly simplifies
identity design (no "continuous" identity variant is ever needed for
options, unlike futures).

## 3. Minimum Reality-tier `OptionObservation` schema (design, not code)

Directly reusing 17I.8 §1's MUST/SHOULD classification, expressed as a
field list (no dataclass written, per this phase's restriction):

```
IDENTITY:
  underlying          (str)
  expiry               (str, ISO date)
  strike                 (float)
  option_type              (str, CE|PE)

VALUE (MUST):
  ltp                        (float)
  open_interest                (float)
  volume                         (float)
  captured_at                      (str, ISO8601+05:30)

VALUE (SHOULD):
  bid, ask                          (float)
  prior_day_open_interest              (float)
  open_interest_change                    (float)

VALUE (OPTIONAL, only if the depth-tier call is made — 17I.8 Option D):
  open, high, low, close                    (float)
  bids, asks                                   (5-level ladders, list of {price, volume, order_count})
  total_buy_quantity, total_sell_quantity          (float)

LINEAGE (not Reality content, but mandatory per every other store in
this project):
  source_symbol                                       (str -- the literal, rollover-prone contract symbol)
  instrument_token                                        (str -- fyToken, metadata per §1)
  access_method, certification_status, certification_ref     (per §5)
```

**Explicitly excluded, structurally, not just by convention**: `iv`,
`delta`, `gamma`, `theta`, `vega`, `rho`, `pcr`, `max_pain`, `signal`,
`score`, `bias`, `sentiment` — the same `FORBIDDEN_PAYLOAD_FIELDS` list
already enforced everywhere else in this project (`market_reality/taxonomy.py`,
unchanged since 17E) would apply unmodified here, since nothing about
this schema requires extending that list.

## 4. Storage architecture — reuse vs. new store, justified

**Re-read `HistoricalObservationStore`'s actual SQLite schema directly
(not assumed from its name)**: the table's uniqueness/lookup key is a
single `natural_key` column, itself
`instrument_identity|resolution|timestamp|source` — `payload` and
`record` are both generic JSON-blob TEXT columns. **The schema itself
has no strike/expiry/option_type columns of its own** — it is already
completely instrument-agnostic in storage shape (this is exactly how
spot/futures/VIX all already share the one table without needing three
separate ones).

**Conclusion: reuse `HistoricalObservationStore`, do not create a new
`OptionRealityStore`.** The ONLY real design gap this reuse requires:
a **canonical, deterministic string encoding** of the 4-part option
identity into the single `instrument_identity` column — e.g. a fixed
format like `"{underlying}|{expiry}|{strike}|{option_type}"` — since
today's `instrument_identity` values (`NSE:NIFTY50-INDEX`,
`NIFTY_FUT_CONTINUOUS`, `NSE:INDIAVIX-INDEX`) are all single, unstructured
strings, never composite-encoded ones. **This specific encoding
convention is not decided here** — a real, small, future design
choice, not a schema change.

**A genuinely new store would be unjustified**: no incompatibility was
found (§4's own re-read confirms genericity), and this project's
standing discipline throughout every phase (17H.4 onward) has been
"reuse unless a real incompatibility is found" — none was found here.

## 5. `CertificationGate` requirements

Already-existing, reconfirmed by direct code re-read (not assumed):

- `INSTRUMENT_TYPE_TO_CERT_KEY[INSTRUMENT_OPTION] = "NIFTY_OPTION_CE"` —
  **already present**, unchanged since 17E. Note the naming
  (`NIFTY_OPTION_CE`, not distinguishing CE from PE at the cert-key
  level) — worth flagging: if PE and CE ever need independently
  certified access (unlikely, since both come from the same
  `optionchain`/`get_depth()` calls), this single cert-key would need
  revisiting; not a blocker today since both option types are fetched
  identically.
- **Required NEW access_method value(s)**, per this project's own
  recurring collision-avoidance discipline (applied identically for
  live-vs-historical daily-vs-intraday-vs-depth across 17H.3/17H.9/17I.2):
  a capture built on `optionchain` needs a distinct access_method from
  both the already-certified `direct_sdk_fyers_broker_py` (live
  quote) and any future `get_depth()`-based capture would need ANOTHER
  distinct value from the futures depth one
  (`direct_sdk_fyers_broker_py_depth`) — reusing either would create
  exactly the false-positive certification collision this project has
  caught and fixed three times already. **Not named here** — a real,
  small, future naming decision, following the same pattern as every
  prior access_method decision in this thread.
- **Identity fields**: `REQUIRED_IDENTITY_FIELDS[INSTRUMENT_OPTION] =
  ("expiry", "strike", "option_type")` — already correct and complete,
  confirmed unchanged. No revision needed.

## 6. Ingestion reliability requirements

Not new problems — every one of these already has a proven, reusable
answer elsewhere in this project; none needs a new mechanism designed
for options specifically:

| Requirement | Existing mechanism, reusable as-is |
|---|---|
| Restart recovery | `HistoricalObservationStore`'s natural-key-based idempotency already survives restart (proven for spot/futures/VIX, 17H.9) — reading the store fresh on construction, no separate options-specific recovery needed |
| Duplicate handling | Same `observation_id`-based idempotent no-op already in place — a re-poll of an unchanged option row would mint the identical content-hash id, handled automatically |
| Conflict handling | Same `ConflictingHistoricalObservationError` discipline — a genuinely revised OI/LTP value under the same natural key would correctly raise, never silently overwrite, exactly as already proven for the 226 real conflicts caught during 17H.9's live backfill |
| Partial failures (one strike's fetch fails mid-chain) | The `optionchain` call returns the WHOLE chain in one response (17I.7 §1) — a single-call failure is all-or-nothing per snapshot, structurally simpler than per-symbol `get_depth()` calls, which would need the same per-chunk `IngestionRun`-status discipline (`OK`/`NO_DATA`/`ERROR`) already used for historical daily/intraday ingestion |
| Broker outage recovery | Already-proven pattern: `AuthenticationError` propagates and stops the run cleanly (17H.9/17I.2's ingestion scripts), never silently continuing on a dead token — directly reusable |

**No new reliability mechanism needs to be invented.** Every piece
already exists, tested, and proven at real scale elsewhere in this
project.

## 7. First controlled validation capture — defined, not run

Per this project's own mandatory-validation discipline (17H.9, 17I.2,
17J.2–17J.4 all required a real, bounded proof run before trusting
anything at scale):

```
instruments:            NIFTY only, single underlying (matches this
                         project's existing spot/futures/VIX scope)
strikes:                 ATM ± 2 (the same narrow window already used
                         in every live probe across 17I.6/17I.7/17I.8/
                         this phase -- proven to return real, clean
                         10-row responses every time)
expiries:                 the nearest weekly only (one expiry, not the
                          full 6-expiry estimate from 17I.8 -- narrowest
                          possible first proof)
frequency:                 a small, bounded number of manual polls
                           during one live session (NOT the full 5-min-
                           all-session cadence yet -- this is a
                           correctness proof, not a volume test)
fields captured:            MUST + SHOULD tier only (§3) -- OPTIONAL/
                            depth-tier fields deliberately excluded from
                            this first validation, to isolate whether
                            the cheap optionchain-based path alone works
                            before adding the more expensive depth path
expected observations:       10 real option rows (5 strikes x 2 types)
                             per poll, matching the exact shape already
                             observed live in every probe this phase
validation criteria:
  - every row's minted observation_id is unique and deterministic
    (re-polling identical data produces the identical id -- the same
    check already proven for spot/futures/VIX in 17H.9)
  - identity fields (expiry/strike/option_type) round-trip correctly
    through whatever encoding convention is chosen (§4)
  - a certification gate check correctly REFUSES the write if run
    before its access_method is certified (fail-closed, proven
    pattern from every prior Reality-tier write path)
  - zero forbidden fields (iv/delta/gamma/etc.) appear anywhere in a
    written record, checked structurally the same way 17H.9/17I.2
    verified it for their own domains
```

**Not run in this audit** — this section defines the shape of that
first validation, per the instruction's own scope limit, not the
validation itself.

---

## Summary

No architectural blocker was found. Every one of the seven questions
resolves to "reuse what already exists" rather than "design something
new": identity model (sufficient, unchanged), lifecycle (token-stable
within a session, no continuity concept needed, unlike futures),
schema (a field list, not a new dataclass, cleanly separable from
forbidden derived fields), storage (`HistoricalObservationStore` reused
as-is, one small encoding-convention decision remaining), certification
(gate infrastructure already complete, only new access_method value
names remain to be minted), and reliability (every mechanism already
proven at real scale for other instruments). The only genuinely open,
undecided items are: the exact `instrument_identity` string encoding
convention, and the new access_method value name(s) — both small,
narrow, naming-level decisions consistent with how every prior phase in
this thread has resolved the same class of question, not open
architectural questions.
