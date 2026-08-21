# Phase 17I.4 — Reality Layer v1.0 Pre-Freeze Gap Audit

**Status: AUDIT ONLY. No code changed, no files modified, no new
schemas introduced.** Every number below was queried live against the
real VPS stores on 2026-08-14, not carried over from memory of prior
audits (17H.0/17I.0), though this audit corroborates those where their
scope overlaps.

---

## 1. Historical Reality Coverage

Live-queried against `data/historical_reality/normalized/historical_observations.db`.

| Instrument | Resolution | Rows | Earliest | Latest |
|---|---|---|---|---|
| NSE:NIFTY50-INDEX (spot) | DAILY | 7,041 | 1998-05-04 | 2026-08-13 |
| NSE:NIFTY50-INDEX (spot) | FIVE_MINUTE | 168,119 | 2017-07-17 | 2026-08-13 |
| NIFTY_FUT_CONTINUOUS | DAILY | 2,131 | 2018-01-02 | 2026-08-13 |
| NIFTY_FUT_CONTINUOUS | FIVE_MINUTE | 157,889 | 2018-02-01 | 2026-08-13 |
| NSE:INDIAVIX-INDEX | DAILY | 4,524 | 2008-04-17 | 2026-08-13 |
| NSE:INDIAVIX-INDEX | FIVE_MINUTE | 168,082 | 2017-07-17 | 2026-08-13 |

**Total: 507,786 rows.** Unchanged from the 17I.0 baseline audit —
confirms stability, no silent drift or loss since then.

**Rejected/conflicted rows** (from `ingestion_runs`, all `RUN_STATUS_OK`,
zero `RUN_STATUS_ERROR` runs on record):

| Instrument/resolution | Rejected | Cause (established in 17H.6/17H.9) |
|---|---|---|
| Spot 5-min | 153 | Real-time revision drift on near-live candles, correctly refused, never silently overwritten |
| VIX 5-min | 44 | Same class |
| VIX daily | 3 | A real `-1.0` OHLC sentinel artifact from FYERS itself |
| Futures 5-min | 29 | Same revision-drift class |
| Spot daily / Futures daily | 0 | — |

**Lineage/identity/certification**: every row carries a `HistoricalLineage`
(source, access_method, source_epoch, source_symbol, ingestion_run_id,
certification_status/ref). Futures continuous rows correctly use
`instrument_identity="NIFTY_FUT_CONTINUOUS"`, never the literal
contract symbol — reconfirmed by direct query (§3 below), not just
recalled from 17H.3's design.

**Answer: yes, sufficient historical market facts exist for future
Understanding work.** Daily coverage spans 20–28 years per instrument;
5-minute coverage spans 8–9 years. Rejection rates are all well under
0.1% and every rejection has an established, understood cause — none
unexplained.

## 2. Live Reality Coverage

`layer0_data/raw_observations.jsonl`: **456 rows**, all `kind=QUOTE`,
evenly split 152/152/152 across spot/futures/VIX — this is a
session-scoped capture buffer (17I.7's most recent capture window),
not a multi-day archive; confirmed identical characterization to
17I.0's finding, unchanged. `layer0_data/rejected_observations.jsonl`
does not exist — zero rejections on record for this window.

**Certification files present** (`data_certification/`): live quote
(`direct_sdk_fyers_broker_py`, CERTIFIED_AVAILABLE for spot/futures),
historical daily and historical intraday (both CERTIFIED_AVAILABLE for
all three instruments) — all confirmed via direct file read, not
assumed.

**Restart survival / duplicate / conflict handling**: not re-tested in
this pass (already proven via passing regression tests
`test_duplicate_detection_survives_a_simulated_restart` and
equivalents in `RawObservationStore`/`HistoricalObservationStore`
suites, last run clean at 5,649 passed). Not re-verified live here
since nothing in the store-write code has changed since those tests
last ran.

### Special focus: Futures Depth

**Certification status: `CERTIFICATION_MISSING` for
`direct_sdk_fyers_broker_py_depth`, confirmed live via
`RealityCoverageIndex` (§3) at the moment this audit was written.**

**MARKET_DEPTH observations: zero exist anywhere in `layer0_data/raw_observations.jsonl`**
(confirmed by direct grep — the 456 rows are entirely `QUOTE`, none
`MARKET_DEPTH`).

**Today's live campaign status, checked live at time of writing
(2026-08-14T07:41 IST)**: a background campaign
(`run_17i3_campaign.sh`) is currently running, still in its
pre-9:10-IST sleep phase — it has not yet executed the certification
script or the poller. **No live depth evidence exists as of this
document.** Per this audit's own instruction — do not mark complete
without evidence — this is reported as genuinely incomplete/pending,
not assumed successful because a campaign happens to be scheduled.

## 3. Cross-Instrument Synchronization Audit

Live-tested via `RealityCoverageIndex.resolve('2026-08-13')` — a real,
fully-populated recent date:

- **Daily**: spot/futures/VIX all `available=True`,
  `completeness=COMPLETE`, full lineage and certification refs present.
- **Intraday bar counts**: spot 75, VIX 75, **futures 77** — a real,
  concrete, previously-undocumented-at-this-precision finding.
  Cross-checked directly: futures carries two additional 5-min bars
  (15:30, 15:35) that spot/VIX do not. **This is a genuine session-
  boundary difference, not a data defect**: the cash/index market
  (spot, VIX) closes at 15:30, while the F&O segment (futures) trades
  until the newer 15:40 close (the same NSE circular already
  documented in 17I.7/17H.9's own `MARKET_CLOSE` constant updates).
  Bujji's Reality layer correctly preserves this real asymmetry rather
  than truncating or padding either series to force alignment — an
  honest reflection of market structure, not a bug.
- **Microstructure**: correctly reports `futures_depth.available=False`,
  `observation_count=0` for this date — matching §2's finding exactly,
  no discrepancy between the coverage tool and direct inspection.
- **Epoch/timestamp handling**: all timestamps are explicit ISO8601+05:30
  strings (never naive/host-timezone-dependent) — confirmed by direct
  inspection of stored records, consistent with `epoch_to_ist()`'s
  usage throughout 17H.9/17I.2.

**Answer: yes, Reality can reconstruct "what the market looked like at
a moment in time,"** including honestly representing the one real
structural misalignment (index vs. derivatives close time) rather than
hiding it. Missing-instrument behavior (e.g., depth) is reported as
`available=False`, never fabricated or interpolated.

## 4. Data Completeness Audit

**No formal "completeness certificate" tool exists** — `RealityCoverageIndex`
reports per-date completeness (COMPLETE/PARTIAL/EMPTY) for a single
queried date, but nothing in the repository enumerates "every expected
trading day vs. every actually-present day" across the full historical
range. This is a real, honest gap in tooling, not in the underlying
data.

**Sample check performed** (not exhaustive, disclosed as such): distinct
daily-resolution calendar dates for spot, by year:

| Year | Distinct trading days recorded |
|---|---|
| 2022 | 248 |
| 2023 | 246 |
| 2024 | 249 |

All three fall within NSE's real historical range (typically 245–250
trading days/year after weekends and ~15–18 holidays) — no evidence of
gaps in these three sampled years. This is a spot-check across 3 of 28
years, not a certified, day-by-day cross-reference against NSE's
official holiday calendar.

**Classification: tooling gap, not a Reality defect.** The underlying
ingestion (17H.4/17H.6/17H.9) already has real, working duplicate/
conflict detection that would surface a genuinely missing or corrupted
day if queried for it — what's missing is a dedicated report that does
that querying systematically across the full range. Building this
report is explicitly not done here (audit-only), and is a legitimate,
narrowly-scoped future tooling task if ever needed — not a blocker to
freezing v1.0, since the underlying Reality mechanism to detect gaps
already exists (`HistoricalObservationStore.range()` returning fewer
rows than expected for a period), only the systematic "run it
everywhere and report" wrapper is absent.

## 5. Microstructure Reality Audit

**What exists today, confirmed by direct field inspection** (`bujji/reality_structure_bridge/bridge.py`'s
`_normalize_depth_payload()`, 17I.2): `bids`/`asks` (each a list of
`{price, volume, order_count}` — real order-book levels), `open_interest`,
`prior_day_open_interest`, `oi_change_flag`, `oi_change_percent` (a raw
field FROM the source, not Bujji-computed), `total_buy_quantity`,
`total_sell_quantity`, `last_price`, `last_traded_quantity`,
`last_traded_time`, `tick_size`, `lower_circuit`, `upper_circuit`. All
of these are literal, stored facts — none require classification
judgment to state.

**Genuinely missing, real observations** (per the instruction's own
allowed/not-allowed distinction — literal facts only):

| Missing fact | Allowed to add (literal)? | Notes |
|---|---|---|
| Bid-ask spread as its own stored field | Borderline — it IS trivially `ask.price - bid.price` over two already-stored facts, so storing it would not be a derived judgment the way a "liquidity score" is. Currently NOT stored as a first-class field, only computable on read. | Real, small gap — not present, but harmless-to-add if ever needed (arithmetic over two literal facts, not a modeling choice, per this project's own `oi_change_percent`-from-two-OI-snapshots precedent already accepted at Layer 0/17G §2.5). |
| Tick count / trade count | Not present anywhere | No field carries "how many trades occurred" — FYERS's depth/quote responses don't expose this; genuinely absent at the source, not a Bujji omission. |
| Trade intensity | Not present, and would require computing a rate (trades/time) — a derived metric, correctly not built. | — |
| Liquidity events (a depth level appearing/disappearing between polls) | Not present. 17G §2.3 (Domain C, unbuilt) explicitly names "liquidity withdrawal" as a genuinely new domain requiring comparison ACROSS consecutive depth polls — the raw per-poll facts exist (§ above), but no code currently diffs two polls to report a level's disappearance as its own fact. | Real, disclosed gap for a future domain, not a Reality-tier defect — the raw material (successive `MARKET_DEPTH` observations, once captured) is sufficient to build this later without new Reality-tier storage. |
| Auction information (opening/closing auction volumes, uncrossing price) | Not present anywhere in this codebase. | Never certified, never discovered as accessible from FYERS in any prior phase's audits — genuinely unknown whether the source even provides this, not just undecided. |

**Confirmed NOT present, correctly**: no bid/ask imbalance ratio, no
liquidity score, no market pressure metric — grepped the depth
normalization code directly; none of `taxonomy.FORBIDDEN_PAYLOAD_FIELDS`
(`iv`, `delta`, `gamma`, `theta`, `vega`, `vwap`, `regime`, `signal`,
`score`, `indicator`, `classification`, `sentiment`, ...) appear
anywhere in a stored payload — enforced structurally by the validator
(`market_reality/validator.py`'s `check_schema()`), not just by
convention.

## 6. Options Reality Boundary Audit

**Current status, checked precisely:**

- **Option chain access**: certified (`fyers_option_chain_certification.json`,
  `CERTIFIED_AVAILABLE`, live-verified strike/quote/historical fields).
- **Strike-wise OI**: certified as accessible (`oi_available: true` in
  the same cert), never ingested into the certified Historical Reality
  store (`HistoricalObservationStore`) or Layer 0
  (`RawObservationStore`).
- **IV / Greeks**: never computed or stored anywhere in the Reality
  layer — correctly forbidden by `FORBIDDEN_PAYLOAD_FIELDS` (§5).
- **Expiry structure**: resolvable live via `InstrumentMaster`, not
  persisted as a Reality-tier historical fact.
- **Option volume, bid/ask**: certified as accessible for the specific
  contract tested (`fyers_option_chain_certification.json`), never
  ingested at scale.

**A real, more nuanced finding than "simply not started"**: two
SEPARATE, non-Reality-layer option data pathways already exist
elsewhere in the repository, from an earlier engineering track (Series
59/64, predating this 17H–17J Reality audit arc):

1. `bujji/replay/option_chain_ingestion.py` — parses NSE's own free,
   official end-of-day Futures & Options Bhavcopy CSV into
   `HistoricalSessionRecord` objects, including real strike-wise OI
   and change-in-OI (read directly from bhavcopy columns, never
   fabricated). **Real, working code** — but feeds a completely
   different model (`HistoricalSessionRecord`, `bujji/replay/`), never
   passes through `CertificationGate`, and is not stored in
   `HistoricalObservationStore`. Only 4 real bhavcopy CSV files exist
   on disk (`data/bhavcopy/`, dated 2026-07-27 through 2026-07-30) —
   real data, but a tiny window, not a systematic backfill, and no
   evidence a persisted database of parsed records was ever built at
   scale.
2. `bujji/market_perception/option_chain_adapter.py` — live, per-cycle
   option chain snapshots (bid/ask/spread per leg, per-strike OI) for
   the Shadow Campaign, explicitly Intelligence-tier (Shadow Campaign
   v2 Phase 1), not Reality-tier, and not persisted as a historical
   corpus either.

**Determination: (B) — a deliberately deferred Reality domain, with
one caveat worth stating precisely.** Building full, certified options
Reality ingestion (matching the rigor of 17H.4/17H.6/17H.9's
spot/futures/VIX pipeline) has never been scoped or decided anywhere
in this Reality-layer audit thread — consistent with (B). The caveat:
this is not "nothing exists" the way, say, auction information is —
real, partially-working code and a small amount of real data already
exist for options, just architecturally outside the certified Reality
layer this audit is freezing. Whether to integrate, replace, or leave
these separate is a real, future architectural decision, not resolved
here, and not a blocker to freezing spot/futures/VIX Reality v1.0.

## 7. Reality → Memory Boundary Verification

Directly re-inspected (not assumed from memory of building them):

- `bujji/reality_memory/models.py` — `RealityMemoryEvent`'s own
  docstring still reads "REALITY-ONLY... no normalization of any
  kind," structurally enforced (no `trend_state`/`support_state`/etc.
  field exists on the dataclass — confirmed via
  `RealityMemoryEvent.__dataclass_fields__` in the regression suite,
  `test_reality_memory_event_has_no_structure_fields`, still passing).
- `bujji/market_understanding/structure.py`,
  `bujji/market_understanding/similarity.py`,
  `bujji/market_understanding/timeline.py` — all three explicitly
  named to live in a package distinct from `reality_memory`, each
  carrying a module docstring stating they are Understanding-tier
  (classified/derived), never Reality. `IntradayStructureRecord`,
  `SituationFeatureVector`, `EpisodeTimeline` all wrap or reference
  Reality-tier facts by id (lineage), never inline them as literal
  Reality data.
- **No Reality-tier file was modified by the recent similarity/timeline
  work** — `bujji/reality_memory/`, `bujji/historical_reality/`,
  `bujji/market_reality/`, `bujji/market_reality_snapshot/` are
  untouched since their respective completion phases (17J.1, 17H.4,
  17E, 17H.5); only new, separate packages were added
  (`market_understanding/*`, `reality_structure_bridge/`).

**Confirmed: no Understanding logic has leaked into Reality.** The
boundary drawn in 17I.0/17J.0 ("Memory begins the moment two or more
facts are retrieved together for comparison") remains intact —
Reality-tier stores still hold only single, literal, timestamped facts.

## 8. Final Classification

| Area | Status | Classification | Action |
|---|---|---|---|
| Historical data (spot/futures/VIX, daily+5min) | Populated, lineage-complete, low/explained rejection rate | ✅ Complete | None |
| Live feeds (spot/futures/VIX quotes) | Certified, session-buffer working as designed | ✅ Complete | None |
| Futures depth | Built, tested, validated in prior phases; **zero live evidence as of this audit** — campaign in progress | 🟡 Known limitation (pending, not failed) | Wait for today's already-running campaign to complete; re-audit with real evidence before final sign-off |
| Synchronization | Cross-instrument reconstruction works; one real, understood session-boundary asymmetry (futures trades 10 min past spot/VIX close) correctly preserved, not hidden | ✅ Complete | None |
| Completeness | Data itself shows no gaps in 3 sampled years; no systematic day-by-day certificate tool exists | 🟡 Known limitation (tooling gap) | Optional future tooling task, not a blocker |
| Microstructure | OHLCV + OI + depth ladders present; spread not first-class (trivially derivable); tick count/trade intensity/liquidity events/auction info absent, correctly so (either source-unavailable or a later domain's job) | 🟡 Known limitation | None required for v1.0 |
| Options reality | Not integrated into certified Reality layer; real but architecturally separate code/data exists elsewhere (bhavcopy ingestion, live adapter) | 🟡 Known limitation (deliberately deferred, per (B)) | None required for v1.0; future integration decision, not scoped here |
| Reality → Memory boundary | Verified intact by direct re-inspection; zero leakage found | ✅ Complete | None |

## Final Decision

**HOLD — pending today's already-in-progress futures depth campaign.**

This is not a newly-discovered blocking gap requiring new work — it is
the SAME, already-scoped, already-scheduled requirement from
`PHASE_17I_REALITY_LAYER_V1_0_COMPLETION_CHECKLIST.md`, whose own
sign-off criteria this audit is bound by. Everything else audited here
(historical coverage, live feeds, synchronization, completeness,
microstructure, options boundary, Reality/Memory boundary) is either
✅ Complete or a 🟡 known, non-blocking limitation with a clear
classification and no required action for v1.0.

**Required to close**: the currently-running live campaign
(`run_17i3_campaign.sh`, launched today, certification at 09:10 IST
then live capture through ~15:40 IST close) completing with real
`MARKET_DEPTH` observations captured and the
`direct_sdk_fyers_broker_py_depth` access_method reaching
`CERTIFIED_AVAILABLE`. No new feature, no new schema, no new capability
is being requested — only the evidence this audit's own instruction
("do not mark complete without evidence") requires before PASS can be
honestly declared.
