# Historical Data Enrichment

**BUJJI Options OS v3 — Qualification Data Enrichment Sprint B**

## Purpose

Improve the evidence the qualification framework runs against, without
changing the system that consumes it. This sprint is data acquisition
and validation only — no line of `bujji/trading_brain`, `bujji/production_runtime`,
`bujji/replay`, `bujji/mic_replay`, `bujji/qualification`, or any MIC v2
file was touched.

## What changed

The replay corpus grew from 13 real trading days (Campaigns v2/v2.1)
to **41 real trading days** (2026-05-25 through 2026-07-22), sourced
identically to Data Acquisition Sprint A — NSE's official, free,
public F&O Bhavcopy archive. Two genuine NSE exchange holidays
(2026-05-28, 2026-06-26) were detected honestly via real HTTP 404
responses from NSE's own archive, not assumed or guessed.

## Two new sourceable-but-not-yet-integrated data sources

This sprint discovered, and proved with real requests, two data
sources that materially exceed what the current corpus carries:

1. **India VIX**, via the already-connected, already-authorized FYERS
   MCP (`fyers_historical`, symbol `NSE:INDIAVIX-INDEX`) — real daily
   OHLC data, confirmed for the full campaign window.
2. **Option open interest**, already present in every NSE Bhavcopy row
   this project has been fetching since Data Acquisition Sprint A
   (`OpnIntrst`/`ChngInOpnIntrst` columns) — it was never missing from
   the source, only never carried through the ingestion pipeline.

**Neither was integrated this sprint.** `bujji.replay.validator.HistoricalSessionRecord`
(Series 59) has a closed, frozen schema — `spot`, `spot_as_of`,
`option_chain_entries: Tuple[(strike, option_type, expiry, symbol), ...]`,
`option_chain_expiries` — with no field for VIX, OI, or bid/ask.
Adding one means modifying a frozen Replay Framework file, which this
sprint's own scope explicitly excludes ("Only: Historical data
acquisition, Dataset validation, Corpus enrichment, Replay
compatibility verification... Do not modify... Replay Framework").
Both are disclosed here as proven, ready-to-integrate follow-on work
for whoever owns that decision — not silently added.

## Why bid/ask and certification remain unsourced

- **Bid/ask**: NSE's Bhavcopy is a settlement-price disclosure, not a
  quote-book snapshot — it was never going to carry bid/ask, and no
  other authorized, individually-licensable source was found
  (TrueData explicitly bars individual licensing for options data;
  Global Datafeeds requires creating a paid account, an action this
  project has no authority to take on the user's behalf). Unchanged
  conclusion from Data Acquisition Sprint A.
- **Certification/governance input**: this is not market data at all —
  it is BUJJI's own internal concept (`mic_v2.certification`), and no
  external provider publishes anything resembling it. The gap is in
  `bujji/mic_replay/publication_replay.py`'s bridge script, which
  never supplies a `certification` object to MIC v2's
  `run_replay_with_contract()` — a wiring gap, not a sourcing gap,
  already disclosed in Series 62 and reconfirmed unchanged by
  Campaign v2, v2.1, and this sprint (`governance=REJECTED` on 100% of
  sessions in every one of the three campaigns run so far).

## What the richer corpus actually proved

Running the unchanged Series 58/61/62/63 pipeline over 41 real
sessions instead of 13 produced genuinely different, real evidence:

- **2 sessions reached `COMPLETED`** (`IRON_CONDOR`, both real 4-leg
  contracts, real `CONSTRUCTED` order construction, real `DISPATCHED`
  execution into `PaperBroker`) — zero such sessions existed in either
  prior campaign.
- **`calibration=CALIBRATED` frequency rose from 31% to 83%** — MIC
  v2's own calibration window genuinely matures with more real history,
  exactly as its own documented window-size logic predicts.
- **`market_context=UNKNOWN` frequency fell from 15% to 5%.**

None of this required any code change — it is purely the effect of
more real evidence flowing through an unmodified system, which is
precisely this sprint's stated principle: improve the evidence, not
the system.

## Validation

`bujji.replay.validator.validate_corpus()` (Series 59, unmodified) ran
against all 41 sessions: **41/41 valid, 0 invalid.** No repair, no
interpolation, no backfill.

## Determinism and fingerprint

No code was modified this sprint, so no determinism risk was
introduced. Qualification fingerprint (`/opt/bujji/qualification/baselines.json`,
md5 `2328e0f77ec312eeca318946df293d91`, mtime `Jul 20 23:43`)
unchanged; full regression suite (1950 tests) unaffected, since no
source file changed.

## Recommendation

See `reports/qualification_data_enrichment.md` §9 for the specific,
evidence-backed next engineering steps this sprint surfaced — chiefly,
extending `HistoricalSessionRecord`'s schema to carry the OI and VIX
data that are now proven sourceable but currently invisible to the
Trading Brain.
