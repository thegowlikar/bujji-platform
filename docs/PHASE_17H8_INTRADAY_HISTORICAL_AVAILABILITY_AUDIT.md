# Phase 17H.8 — FYERS Historical Intraday Availability Audit

**Status: AUDIT ONLY. No code. No ingestion. No new models.**

Every finding below is live-verified against the real FYERS `historical`
endpoint (2026-08-13, market closed — irrelevant, historical data has
no market-hours dependency), not inferred from the daily-resolution
findings already established in Phase 17H.1/17H.6.

---

## 1. Per-request range limit — uniform across all intraday resolutions

Bisected precisely for **1-min, 5-min, 15-min, and 60-min**, on spot,
futures, and VIX:

| Resolution | Max days/request | Boundary confirmed |
|---|---|---|
| 1 min | **100 days** | 100d = ok, 101d = error (code -50) |
| 5 min | **100 days** | 100d = ok, 101d = error |
| 15 min | **100 days** | 100d = ok, 101d = error |
| 60 min | **100 days** | 100d = ok, 101d = error |
| Daily (17H.1 baseline) | 366 days | unchanged, restated for contrast |

**Every intraday resolution shares the identical 100-day cap**,
regardless of granularity — a single platform-wide rule, not a
per-resolution one. This is real and measured, not assumed uniform
because one resolution was tested.

## 2. Real depth — a genuine, resolution-dependent structural boundary, not previously known

This is the headline finding. **Intraday history does not reach nearly
as far back as daily history, for any instrument — including VIX, whose
daily depth (2008-04-17) gave no indication of this.**

| Instrument | Daily depth (17H.1/17H.6, already ingested) | Intraday depth (this audit, live-bisected) |
|---|---|---|
| Spot | 1998-05-04 | **2017-07-17** (confirmed identical at both 5-min and 1-min resolution) |
| VIX | 2008-04-17 | **2017-07-17** (same exact date as spot) |
| Futures (continuous) | 2018-01-02 | ~2018-01-02, bounded by the continuous series' own existence (intraday can't precede a series that doesn't exist yet at any resolution) |

**Spot and VIX intraday data start on the exact same real date**
(1500262800 epoch, confirmed via two independent resolutions) — strong
evidence this is a single platform-wide intraday-data cutoff FYERS
applies uniformly, not an instrument-specific limitation. VIX's daily
depth (2008) is real and already ingested, but VIX intraday users
should not assume anything close to that depth — the two are
genuinely different boundaries for the same instrument, discovered here
for the first time.

## 3. What this means for ingestion mechanics, if pursued

- **~100-day chunks instead of ~366-day chunks** — roughly 3.7x more
  `IngestionRun`s per instrument for the same calendar span, using the
  exact same chunking logic already built in 17H.4/17H.6's scripts
  (`_chunks()`), just a smaller `CHUNK_DAYS` constant.
- **Real row-volume estimate, not hand-waved**: 5-min spot from
  2017-07-17 to today (~9 years) at ~5,300 candles per 100-day chunk ×
  ~33 chunks ≈ **~175,000 rows for spot 5-min alone**. 1-min would be
  roughly 5x that (~875,000 rows). Multiplied across three instruments,
  a full 1-min backfill could reach several million rows. **SQLite/WAL
  handles this scale without difficulty** (well-established for
  databases far larger than this) — this is a volume fact worth
  planning around, not a blocker.
- **OI remains permanently absent** at every intraday resolution too —
  same structural fact already established for daily (17H.1 §1.6),
  re-confirmed here rather than assumed to carry over.
- **No new models needed.** `HistoricalObservation`/`HistoricalObservationStore`/
  the certification pattern are resolution-agnostic already (`resolution`
  is a plain field, `moc_taxonomy.RESOLUTION_FIVE_MINUTE` etc. already
  exist in the taxonomy, unused until now). An intraday ingestion script
  would be a close variant of the existing daily ones, not new
  architecture.

---

## Answer to the question asked

**Yes — Bujji can build an intraday Reality database before starting
Market Memory, and doing so remains squarely inside the Reality tier**,
for the same reason the daily ingestion did: it is raw OHLCV
acquisition with real, certified provenance, zero indicators, zero
computation. Nothing found in this audit blocks it structurally.

**What should inform the decision, stated plainly:**
- Real depth is far shallower than daily (~2017 vs ~1998/2008) — an
  intraday database would NOT extend Bujji's memory further back in
  time, only make the post-2017 window higher-resolution.
- Row volume is real but manageable at 5-min; 1-min is meaningfully
  larger and should be a deliberate choice, not a default.
- This is a genuinely separate, additive phase from 17H.4/17H.6 (chunk
  size differs, depth differs, volume differs) — not a trivial
  parameter change to the existing daily scripts, even though the
  underlying mechanism is the same.

**Not decided here**: whether to build it now, which resolution(s) to
target, and whether it should precede or follow the start of Market
Memory work. Those are sequencing decisions for you to make, not
audit findings.
