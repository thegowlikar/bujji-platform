# Phase 17I.8 — Options Reality Capture Strategy Design Audit

**Status: DESIGN AUDIT ONLY. No code, no storage, no ingestion.**
Synthesizes 17I.5/17I.6/17I.7's live-verified findings into a concrete
capture strategy recommendation — no new FYERS probes were needed for
this pass except one confirmation (`InstrumentMaster`'s option
lot-size support, checked directly), since the field/endpoint/limitation
evidence this audit reasons from was already gathered live in the
three prior phases.

---

## 1. Minimum viable Options Reality dataset

| Tier | Fields | Why |
|---|---|---|
| **MUST CAPTURE** | `underlying`, `expiry`, `strike`, `option_type` (identity — already `REQUIRED_IDENTITY_FIELDS`), `LTP`, `OI`, `volume`, capture `timestamp`, `source_symbol` (lineage) | Without these, "what did the options market look like" cannot be answered at all — this is the floor. `LTP` and `OI` are the two fields every one of this project's own prior audits (17I.5/17I.6/17I.7) confirmed as reliably present on every live probe run. |
| **SHOULD CAPTURE** | `bid`, `ask` (top-of-book, from `optionchain` — proven present on every strike in the live probe), `prior_day_OI` (`prev_oi`), `OI_change` (`oich`) | Real, literal, source-provided facts (§1 of 17I.7) that materially enrich the record at near-zero extra cost — `optionchain` already returns them in the SAME call as the MUST-CAPTURE fields, no additional API cost to include. |
| **OPTIONAL** | Full OHLC, 5-level depth ladder (`bids`/`ask` arrays), `total_buy_quantity`/`total_sell_quantity`, `tick_size`, circuit limits | All real and literal (confirmed live via `get_depth()`, 17I.1/17I.7 §1) but require a SEPARATE, per-symbol API call, not bundled into the cheap whole-chain `optionchain` call — a real cost/completeness trade-off, not a data-quality question. |

**Explicitly excluded, not merely deferred**: IV, Greeks (never
source-provided, confirmed absent from every live response across
three phases), PCR, max pain, OI-buildup narratives ("long buildup"/
"short covering" — a causal label over a raw OI+price correlation, the
same reasoning 17G §5.5 already applied to futures OI and extends here
unchanged).

## 2. FYERS capture approach comparison

| Approach | Data completeness | Storage requirements | Future intelligence usefulness | Limitations |
|---|---|---|---|---|
| **A) Full chain snapshot** (every strike, every expiry, periodic) | High breadth — `optionchain` genuinely returns the whole chain in one call (confirmed live, §1 of 17I.7) | Largest — captures far OTM strikes with near-zero information value most of the time | High for chain-shape questions (skew across strikes) later; most far-OTM rows will rarely matter | No OHLC, no depth, on this endpoint (confirmed) |
| **B) ATM-focused rolling window** (ATM ± N strikes, active expiries) | Lower breadth, but concentrated on where real trading activity and real future intelligence use-cases (premium selling near ATM) actually live | Smallest of the four | High per-byte usefulness, but loses far-OTM tail information a future volatility-smile/skew Understanding domain would want | Requires a live ATM-resolution step (`resolve_atm`, already exists) every snapshot, adding a dependency the pure chain-fetch doesn't need |
| **C) Full depth capture** (every symbol, 5-level book) | Highest field richness (OHLC + full ladder) but requires ONE API CALL PER SYMBOL — for a chain with even 60 strikes × 2 types, that is 120 calls per snapshot, vs. Approach A's 1 call | Largest of the four, by far, if applied to the full chain | Highest possible fidelity, but mostly wasted on far-OTM strikes that rarely trade | Real, proven rate/cost concern — untested at this call volume in any phase to date |
| **D) Hybrid** (full chain via `optionchain` + selective depth via `get_depth()` for a bounded near-ATM subset) | High breadth (A's coverage) + high richness where it matters (C's fidelity, bounded to ATM±N) | Moderate — breadth from the cheap call, richness only where the expensive call is worth its cost | Best coverage-to-cost ratio of the four, directly informed by §1's real field-availability table | Requires two separate polling loops (chain-wide + symbol-specific), real but modest added complexity vs. A alone |

## 3. Reality granularity

| Option | Analysis |
|---|---|
| A) Tick-by-tick | **No live tick feed for options has been certified or tested anywhere in this codebase** (17I.7 §6) — not a granularity choice available today, only a theoretical one. |
| B) 1-minute | No evidence this resolution has ever been tested against `optionchain`/`depth` specifically; would need its own rate-limit verification before being trustworthy. |
| C) 5-minute | **The only resolution with a directly proven, working precedent in this exact codebase** — `market_episode.config`'s `DEFAULT_PROXIMITY_WINDOW_SECONDS = 300.0` was found to be an exact drop-in fit for 5-min spot/futures/VIX data (17G.A/17GA3/17GA4, already built and validated), and 17H.9's chunking/ingestion pattern already works at this cadence for three other instrument classes. Adopting it for options reuses proven machinery rather than re-deriving new timing assumptions. |
| D) Event-based (only when OI/price changes) | Would under-capture a genuinely informative fact: an OPTION WITH NO CHANGE for a stretch is itself a real fact (no trading interest), and Reality-tier discipline elsewhere in this project (17H.1's "structural volume=0 for an index is a true fact, never omitted") argues against silently skipping unchanged snapshots. |
| E) Market-open/periodic only | **Already directly disproven as sufficient** by this project's own, hard-won, real finding: 17J.2's single-end-of-session-snapshot similarity engine measurably failed to discriminate crash days from calm days, and 17J.3/17J.4 traced the cause specifically to snapshot-only (vs. path) representation losing exactly the information that mattered. Nothing about options makes this reasoning not apply — if anything, options' own information content (OI shifts, premium moves) is at least as path-dependent as the price structure that already proved this. |

**Recommendation: (C) 5-minute snapshots** — the only option with both
a directly proven precedent in this codebase and a clear, evidenced
reason (17J's own crash-discrimination finding) to reject the cheaper
alternatives (D, E). Not chosen for convenience — chosen because the
alternatives have either no proof of feasibility (A, B) or a directly
measured failure mode already discovered in this project (E), and (D)'s
event-based framing contradicts an already-established Reality
discipline (structural zero/no-change is still a fact).

## 4. Resolving the `options_observation` vs. `option_chain_ingestion` duplication

Re-audited directly (not just re-cited from 17I.7):

| | `bujji/options_observation/` | `bujji/replay/option_chain_ingestion.py` |
|---|---|---|
| Represents | A single option contract's observed fact, wrapping MOC's canonical `Observation` | A full day's session record (`HistoricalSessionRecord`) — broader scope, one record per SESSION, not per contract-observation |
| Identity mechanism | `market_observation.engine.build_observation()` — same minting function every other Reality-tier store uses | Its own model, no MOC involvement |
| Source | Designed to accept any feed (bhavcopy today; live `optionchain`/`depth` tomorrow, nothing in its design is bhavcopy-specific) | Bhavcopy-specific by construction — the module docstring itself is written entirely in terms of "NSE's own... Bhavcopy CSV" |
| Certification | None wired, but MOC's certification hooks are compatible (17I.7 §3) | None wired, no compatible hook exists in `HistoricalSessionRecord`'s own shape |

**Are they representing different concepts? Yes, genuinely** —
`options_observation` is a per-contract FACT record (Reality-tier
shape); `HistoricalSessionRecord` is a per-SESSION aggregate/rollup
concept (closer to a session-summary shape, not a single immutable
fact). This is not the same kind of duplication as, say, two competing
observation_id-minting schemes — it's closer to two different LAYERS
that happen to share one raw source (bhavcopy).

**Which aligns with the Reality Layer? `options_observation`,
unambiguously** — it already uses the exact identity/certification-
compatible pattern every other Reality-tier store in this project
uses. `HistoricalSessionRecord` does not, and was never designed to.

**Should one be deprecated? No — not on this evidence.**
`HistoricalSessionRecord` appears to serve a different purpose
(session-level replay/backtesting scaffolding, per its own package
name `bujji/replay/`) that this audit has not fully characterized and
should not casually recommend removing. **Should both coexist?
Yes, for now** — but any future options Reality ingestion should build
on `options_observation`'s pattern, never `HistoricalSessionRecord`'s,
and this document records that decision explicitly so it is not
re-litigated by accident later.

## 5. Options Reality identity design

**Underlying + Expiry + Strike + Option Type is sufficient for
IDENTITY** — already enforced by `REQUIRED_IDENTITY_FIELDS[INSTRUMENT_OPTION]`,
confirmed unchanged since 17E, and this is the correct, minimal set: no
two real option contracts can share all four values.

**Checked, and correctly classified as METADATA, not identity:**

| Field | Identity or metadata? | Evidence |
|---|---|---|
| Exchange | Metadata — always `NSE` for every instrument this project touches; carries no distinguishing information among options themselves | Confirmed constant across every live probe in 17I.1–17I.8 |
| Instrument token (`fyToken`) | Metadata (lineage-worthy, not identity) — it's FYERS's own internal id for the CURRENT listed contract, directly analogous to `source_symbol` for futures (17H.3 Part 2.4's binding rule: the literal broker-side symbol/token is lineage, never identity) | Confirmed present in the live `optionchain` response (§1) |
| Lot size | Metadata — real, live-resolvable via `InstrumentMaster` (`resolve_atm`'s `effective_lot_size`, confirmed by direct code read this pass, reading the real symbol-master CSV column, same mechanism already proven for futures) — describes the contract, does not distinguish it from another contract |
| Contract multiplier | **Not found anywhere in this codebase.** No field, no code path computes or stores one. Genuinely absent, not merely unchecked — worth stating plainly rather than assuming it exists implicitly via lot_size. |
| Settlement type | **Not found anywhere in this codebase either.** NSE index options are cash-settled by regulation (a real, external fact), but nothing in this repository currently records that as a stored field for any instrument. |

**Separation, stated explicitly**: identity = the 4-tuple (never
changes for a given contract's life); metadata = exchange, token, lot
size, and (if ever added) multiplier/settlement type — all
describe-the-contract facts that could change how it's traded but never
change WHICH contract is being referred to.

## 6. Realistic storage scale estimate

**Stated assumptions, disclosed as assumptions, not verified at full
scale in this audit** (the live probe used `strike_count=2`, a narrow
window, not the full chain):

- ~60 strikes per expiry side is a reasonable estimate for NIFTY's real
  listed range (not independently re-measured at max width this pass)
  → ~120 contracts/expiry (60 strikes × CE+PE).
- ~6 actively-traded expiries at any time (near-term weeklies + a
  monthly + a quarterly) — a stated estimate, not measured.
- 5-minute cadence, ~75 bars/session (the same real, established
  constant used throughout 17H.9/17I.7/17J's own work).
- 250 trading days/year (matches the real, sampled trading-day counts
  from 17I.4's own completeness check).

```
Per snapshot:      120 contracts/expiry × 6 expiries  = ~720 records
Per session:        720 records × 75 bars              = ~54,000 records/day (NIFTY alone)
Per year:            54,000 × 250 trading days           = ~13.5 million records/year (NIFTY alone)
```

Adding a second major index (BANKNIFTY, or any additional underlying)
roughly scales this further — order-of-magnitude, not precisely, since
BANKNIFTY's own real expiry/strike-count profile was not independently
measured in this audit.

**Storage impact**: at roughly the same per-record size as the
already-built futures depth payload (~500B–1KB JSON, 17I.2), ~13.5M
records/year for one index is on the order of **single-digit-to-low-
double-digit gigabytes per year** — a real but entirely manageable
scale for SQLite (the same conclusion already reached for the much
larger 507K-row spot/futures/VIX corpus, 17H.9), not remotely a storage
blocker.

**This is an order-of-magnitude estimate with disclosed, unverified
assumptions (strike count, expiry count) — not a measured number.** A
real measurement would require actually running an unrestricted
`optionchain` call and counting, which this audit did not do (would
have required a broader live probe than needed to answer the design
question).

## 7. Final Recommendation

**D — Capture complete chain + selective depth.**

Directly evidenced, not chosen for convenience:

- **§2's comparison** shows (D) has the best coverage-to-cost ratio:
  full chain breadth from one cheap `optionchain` call, richer
  OHLC+ladder depth only for a bounded near-ATM subset via the more
  expensive per-symbol `get_depth()` call — reusing exactly the
  precedent 17I.2 already built and validated for futures depth,
  applied to a bounded strike window instead of every strike.
- **§3's granularity analysis** independently supports pairing this
  with 5-minute cadence — the only resolution with both a proven
  precedent and a clear reason (17J's own measured finding) to reject
  cheaper alternatives.
- **§1's MUST/SHOULD/OPTIONAL split** maps directly onto the two-call
  design: MUST+SHOULD fields all come from the cheap `optionchain`
  call; OPTIONAL fields come from the selective `get_depth()` calls.
- **§4/§5** confirm no new identity system or model redesign is needed
  — `options_observation`'s existing identity/value shape already fits
  this recommendation without modification.

**Not (A) alone** — full-chain-only would permanently forgo OHLC/depth
for every strike, discarding real, available information for no
evidenced reason. **Not (B) alone** — ATM-only would discard the
far-OTM tail a future volatility-smile Understanding domain might
genuinely need, and (D) already gets ATM's richness while keeping A's
breadth. **Not (C) alone** — full depth on every strike is the most
expensive option with the least evidence of being necessary (most
far-OTM depth ladders are rarely informative). **Not "do nothing" (A
in the choice list, "do nothing until later")** — 17I.6's own final
rule already established that permanent forward capture should begin
given historical backfill is proven unreliable; this recommendation is
that capture's shape, not a reason to defer it further.

**Not implemented here.** This document defines what Bujji should
remember, not the code that remembers it — matching every prior phase
in this thread's own audit-then-approve-then-build discipline.
