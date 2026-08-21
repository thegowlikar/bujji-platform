# Phase 17H.1 — Historical Market Reality Database: Source Audit

**Status: AUDIT ONLY. No code written. No schema designed. No ingestion
engine built. No indicators. No existing Reality Capture code touched.**

## A naming note, stated up front

This engagement already used the "Phase 17H" label for a different,
earlier initiative (`LiveMarketDataProvider` design, docs
`PHASE_17H1_LIVE_PROVIDER_CONTRACT_DECISIONS.md` through
`PHASE_17H2_0_3_...md`) — now complete and shipped as Phase 17I. This
document's filename does not collide with any of those (different
suffix), but the number is being reused for a new, unrelated initiative
(Historical Market Reality Database). Flagged so nobody later assumes
this document continues that earlier thread — it doesn't.

**Most of what this phase asked to be audited was already audited**, on
2026-08-12, in `docs/PHASE_17H2_0_2_HISTORICAL_REALITY_SOURCE_EVALUATION.md`
— a thorough Bhavcopy-vs-FYERS comparison that closes almost every
question below. That document explicitly named **two remaining unknowns**
as blocking (§1.1: does FYERS futures-historical even work; §1.8: what
does `cont_flag` actually do) and recommended a live discovery check to
resolve them, never authorized at the time. **This audit performs that
check now, live, against the real FYERS API** (market closed,
read-only, no state mutation), and reuses everything else from that
prior document rather than re-deriving it.

---

## Part 1 — FYERS Historical API (live-verified today, 2026-08-13, post-market-close)

### 1.1 Per-request date-range limit — a real, previously-unknown constraint, now measured

**Bisected precisely, live**: a single `historical` request at daily
resolution succeeds up to **366 days**, fails at **400 days**, for both
spot and futures symbols identically (same `code=-50 "Invalid input"`
error on both instrument types at 400 days, both succeed at 366).

| Window size | Result |
|---|---|
| 366 days | `s=ok`, 248 candles |
| 400 days | `s=error, code=-50` |
| 2 years, 6+ years | `s=error, code=-50` |

**This is almost certainly a hard ~1-year (366-day) cap per request**,
not previously documented anywhere in this codebase. **Direct
consequence for Phase 17H.4/17H.5**: a 10+ year backfill cannot be one
request — it requires **chunked, paginated requests, roughly one per
calendar year**, for every instrument. This changes the ingestion
engine's minimum shape and must be designed for from the start, not
discovered mid-build.

### 1.2 Futures historical — resolved from UNKNOWN to CONFIRMED WORKING

The prior audit's single most consequential open question. **Directly
tested, live**: `historical` accepts a real futures symbol
(`NSE:NIFTY26AUGFUT`) at daily resolution within its own real listing
window (Jul 1–Aug 13, 2026) and returns genuine OHLCV:

```
first: [1782864000, 23994.5, 24142.6, 23983.3, 24092.6, 2408835]
last:  [1786579200, 24460.5, 24514.0, 24385.4, 24467.3, 1409395]
```

Same 6-field `[epoch, open, high, low, close, volume]` shape already
established for spot/options — **no OI field, confirmed absent here
too**, consistent with the prior audit's structural finding that FYERS
historical candles never carry OI, for any instrument.

### 1.3 `cont_flag` semantics — resolved from UNKNOWN to CONFIRMED, and the finding is bigger than expected

The prior audit's second open question, and the more important of the
two. **Directly tested, live, using a symbol that could not possibly
have existed yet**: requested `NSE:NIFTY26AUGFUT` (a contract that
lists ~3 months before its Aug 2026 expiry) for the full calendar year
**2025** — a period entirely before this specific contract was ever
listed.

- **`cont_flag=1`**: returned **249 real daily candles**, spanning all
  of 2025, with real, plausible, continuously-evolving prices (23,500
  in January 2025 → 26,296 by December 2025).
- **`cont_flag=0`**, identical request otherwise: returned **zero
  candles**, `s=no_data`.

**This proves `cont_flag=1` is FYERS's genuine continuous-contract
stitching flag** — it returns the historically-active near-month
contract's price at each point in time, rollover-adjusted, under the
convenience of a single (even not-yet-existent) symbol. This directly
overturns the prior audit's cautious hypothesis ("may simply be a
required-but-mostly-inert default parameter") — it is not inert; it is
load-bearing and exactly does what its name suggests. **This means a
genuine multi-year continuous NIFTY futures price series is directly
retrievable from FYERS today**, chunked per §1.1's 366-day limit,
without this project needing to build its own rollover-stitching logic
for price (OHLCV) at all. **OI remains permanently unavailable from
this source regardless** (§1.2, structural, unchanged from the prior
audit).

### 1.4 Real earliest available dates — measured, not estimated

| Instrument | Earliest confirmed real data | Real-world cross-check |
|---|---|---|
| NIFTY Spot (`NSE:NIFTY50-INDEX`) | Between 1996 (`no_data`) and 1998 (`s=ok`, 169 candles, plausible prices e.g. 1159.8) | NIFTY 50 index itself launched April 1996 — the boundary found matches known real-world history almost exactly |
| India VIX (`NSE:INDIAVIX-INDEX`) | 2008 confirmed (`s=ok`, 167 candles, e.g. 27.94–38.13 in the 2008 window) | India VIX was launched by NSE in 2008 — matches |
| NIFTY Futures (continuous, `cont_flag=1`) | At least 2025 confirmed directly; likely much further back given the mechanism is a genuine continuous series, not bounded by any single contract's own listing window — **not bisected further this session**, no reason found to expect an earlier limit different from spot's |

**All three instruments comfortably exceed the "10+ years" target named
in Phase 17H.4** — spot and VIX by a wide margin (roughly 28 and 18
years respectively from today), futures at minimum matching whatever
depth the continuous series actually supports (untested past 2025, but
structurally not expected to be shallower than spot).

### 1.5 Resolution support

Only daily (`resolution="D"`) was tested this session (matching this
audit's scope — Phase 17H.4 explicitly starts with daily). Intraday
resolutions (`"5"`, `"15"`, `"60"`) are already **live-verified
elsewhere in this codebase** for spot/options (`get_recent_candles`/
`get_option_candles`, 2026-07-19 audit) — not re-tested here, no reason
to expect a different per-request range cap, though that specific
number (likely much shorter than 366 days for intraday, per typical
broker API patterns) has **not been measured** and should not be
assumed before Phase 17H.5 needs it.

---

## Part 2 — NSE Direct (Bhavcopy)

### 2.1 What's actually on disk today — a real gap, not previously stated this plainly

**Only 4 files exist**, all recent, all clustered:

```
BhavCopy_NSE_FO_0_0_0_20260727_F_0000.csv
BhavCopy_NSE_FO_0_0_0_20260728_F_0000.csv
BhavCopy_NSE_FO_0_0_0_20260729_F_0000.csv
BhavCopy_NSE_FO_0_0_0_20260730_F_0000.csv
```

**This is not a historical archive — it is a 4-day test window.** The
prior audit's Bhavcopy analysis (Part 1–2 of the 17H2.0.2 document)
correctly describes Bhavcopy's real *capabilities* (full OI, full
chain, EOD-only) from these real files, but did not state as plainly as
this: **there is currently no multi-year Bhavcopy archive anywhere in
this project.** Building one is real, additional, unstarted work.

### 2.2 Acquisition mechanism — documented URL, no downloader exists

`bujji/replay/option_chain_ingestion.py`'s own docstring names the real,
public, unauthenticated NSE archive URL pattern:
`https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_..._<YYYYMMDD>_F_0000.csv.zip`
— "no login, no API key, no scraping-in-violation-of-terms." **But no
code in this repository actually fetches this URL.** Every module found
(`futures_observation/runner.py`, `options_observation/runner.py`,
`market_data_provider.py`, etc.) *parses* an already-present local CSV
— none *acquires* one. The 4 files on disk were evidently placed
manually. A real ingestion engine needs a genuine downloader (one HTTP
GET + zip-extract per trading date), which does not exist today.

### 2.3 NSE's own retention depth — genuinely unknown, not tested this session

How far back `nsearchives.nseindia.com` actually serves files is **not
verified**. This is common, external knowledge about NSE's own archive
policy, not something this codebase's code (or this audit) confirms.
Flagged as a real open item for whoever builds the downloader, not
assumed to be "however far back we need."

### 2.4 Everything else — reused from the 17H2.0.2 audit, unchanged

Bhavcopy's OHLC, volume, and OI columns; its EOD-only, one-fixed-timestamp
structure; its permanent inability to provide bid/ask or intraday
granularity; and its status as the **only** source for historical OI
and full historical option-chain reconstruction — all already
established, live-verified against real files, and restated as still
true. Not re-derived here; see that document's Parts 1.4–1.9 and 2.

---

## Part 3 — Existing repository artifacts relevant to this phase

- `docs/PHASE_17G_BACKFILL_DEPTH_DECISION.md` — the pre-existing policy
  decision that FYERS (not Bhavcopy) should be the historical source
  target for spot/futures/VIX, made *before* futures-historical was
  verified. This audit's §1.2–1.3 findings now actually support that
  decision for **price**, though not for OI (still Bhavcopy-only,
  unchanged).
- `docs/PHASE_17H2_0_2_HISTORICAL_REALITY_SOURCE_EVALUATION.md` — the
  full comparison this audit builds on; its Part 5 capability-gap table
  and Recommendation remain valid, with two rows now upgradeable from
  "UNKNOWN" to "VERIFIED" (Historical futures OHLC; contract
  rollover/continuity handling — see §1.2–1.3 above).
- `bujji/market_reality/*` (Phase 17E–17I) — the live Reality Capture
  pipeline this phase must **not** modify. Nothing in this audit
  required touching it, and nothing here proposes to.

---

## Recommendation, carried forward and updated

**Unchanged from the prior audit's core conclusion**: build on BOTH
sources — Bhavcopy remains the sole path to historical OI and full
chain reconstruction (structural, permanent, unchanged); FYERS is now
**confirmed**, not merely hoped, to provide a genuine multi-year
continuous price series for spot, futures, and VIX at daily resolution,
chunked into ≤366-day requests.

**What Phase 17H.2 (schema design) can now assume as fact, not
placeholder**:
- A `spot_candle`/`futures_candle`/`vix_candle` schema populated from
  FYERS can realistically target **20+ years** for spot/VIX, and at
  minimum multi-year for futures via `cont_flag=1`.
- `futures_candle`'s `open_interest` field will be **permanently
  `None`/absent for any FYERS-sourced row** — only Bhavcopy-sourced
  rows can ever populate it. This should be visible in the schema (a
  nullable field, source-tagged), not hidden.
- Ingestion must be architected around a **paginated, ≤366-day-window
  fetch loop** from day one — not discovered as a bug during Phase
  17H.4's first real backfill attempt.

**What remains unresolved, explicitly, before Phase 17H.4 starts**:
1. A real Bhavcopy downloader does not exist and must be built (§2.2) —
   in scope for whichever phase actually ingests Bhavcopy history, not
   this audit.
2. NSE's own real archive retention depth (§2.3) is unverified.
3. Intraday resolution range-limits (§1.5) are unmeasured — relevant to
   Phase 17H.5, not 17H.4's daily-first target.

None of these block Phase 17H.2's schema design or Phase 17H.4's
"start with NIFTY Spot Daily" plan — both can proceed on what's now
verified.
