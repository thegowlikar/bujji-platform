# Data Acquisition Sprint A — Historical Option Chain Research & Integration

**BUJJI Options OS v3**

## Outcome: **A — Success (with one disclosed scope boundary)**

An authorized, free, self-serve historical option-chain data source was
identified: **NSE's own official daily Futures & Options Bhavcopy**.
A validated, real replay corpus was produced from it, and Series 58's
qualification framework consumed it without any modification. A
second, distinct dependency was discovered in the process — historical
MIC v2 classification — and is disclosed below rather than concealed
or worked around with fabricated values.

## Providers evaluated

| Provider | Historical option-chain data | Timestamp granularity | Strike/expiry coverage | Greeks | Retention | License (individual/retail) | Cost | API reliability |
|---|---|---|---|---|---|---|---|---|
| **NSE official Bhavcopy** (`nsearchives.nseindia.com`) | **Yes** — full daily settlement snapshot, every listed contract | End-of-day (one snapshot/session, ~15:30 IST settlement) | Complete — every strike/expiry NSE lists that day (1,584–1,628 NIFTY contracts/day in the sample fetched) | No | Publicly confirmed for the current period tested; NSE's own archive is known to extend back several years | **Free, public regulatory disclosure — no account, no login, no API key** | **Free** | Direct HTTPS download, confirmed reachable (HTTP 200) and format-stable in this test |
| FYERS (`fyers_historical`/option-chain API, already connected in this project) | **No** — confirmed via both official community reports and a direct capability check of the connected MCP tool set: no option-chain endpoint (historical or live) exists; historical OHLC is limited to *currently active* contracts, not expired ones | N/A | N/A | N/A | N/A | Already authorized (existing broker relationship) | Already paid for (existing account) | N/A — capability does not exist |
| TrueData | Yes, historical NIFTY/BANKNIFTY options data is offered | Reported as available down to minute resolution | Full exchange coverage claimed | Yes (live; historical Greeks unconfirmed) | Not confirmed without direct contact | **Explicitly barred from selling options data to individuals** per their own community statements — institutional/business licensing only | Not published; requires sales contact | Reported reliable, but inaccessible to this project's individual/retail context |
| Global Datafeeds | Yes — dedicated historical + option-chain + option-Greeks API | Tick/minute/day/week/month | Full NSE F&O coverage claimed | Yes | Not confirmed without direct contact | Requires signed subscription/licensing agreement; free trial offered but requires account creation | Paid (tiered; not published without contact) | Established authorized vendor (NSE/BSE/MCX/NCDEX) since 2010, but unverified for this project since I have no authority to create an account or accept a paid license on the user's behalf |
| NSE via a paid EOD subscription (`marketdata@nse.co.in`) | Yes, but the same data is already available for free through the public Bhavcopy archive above | EOD | Full | No | Extensive | Official, but this specific paid channel is redundant given the free public archive | Paid | N/A — superseded by the free option below |

## Why NSE's official Bhavcopy was selected

- **Authorized and free.** It is NSE's own regulatory end-of-day
  disclosure, published at a documented, stable, public URL
  (`nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_<YYYYMMDD>_F_0000.csv.zip`),
  requiring no login, no API key, and no scraping of any interactive,
  ToS-protected page — it is a direct file download of a public report,
  the same one every NSE member and data vendor already consumes.
- **Verified reachable and genuine, not assumed.** Two real trading
  days (2026-07-21, 2026-07-22) were fetched and inspected directly.
  Both returned real, well-formed CSVs (~7MB, ~38,000 rows covering
  every NSE F&O contract that day).
- **Cross-validated against an independent source.** The bhavcopy's own
  `UndrlygPric` field for NIFTY options on 2026-07-22 (`23996.25`)
  matches, to the last decimal, the close price independently fetched
  from the connected FYERS MCP's `fyers_historical` for the same date
  in the Series 60 campaign — strong evidence this is genuine market
  data, not a coincidence.
- **TrueData and Global Datafeeds were not selected** despite having
  broader technical capability (Greeks, finer granularity), because
  integrating either would require creating an account and/or
  accepting a paid licensing agreement on the user's behalf — both are
  actions this project's own safety rules prohibit performing without
  the user's own action, and neither was confirmed necessary once the
  free, official NSE source proved sufficient for Series 59's actual
  requirements (which do not need Greeks or intraday granularity).

## Compatibility assessment against Series 59

| Requirement | Result |
|---|---|
| Can it populate `HistoricalSessionRecord`? | **Yes** — confirmed by `bujji/replay/option_chain_ingestion.py`'s `build_session_record()`, which reads `TckrSymb`/`XpryDt`/`StrkPric`/`OptnTp`/`FinInstrmNm`/`UndrlygPric` directly, one bhavcopy row per option contract. |
| Can timestamps remain deterministic? | **Yes** — one timestamp per trading day (`<TradDt>T15:30:00`, NSE's own settlement time), never derived from a wall clock. |
| Can spot and option-chain snapshots be aligned? | **Yes** — `UndrlygPric` is reported by NSE on the *same row* as each option contract for the *same session*, so spot and chain are inherently aligned; no separate alignment step or join risk exists. |
| Are missing records explicitly detectable? | **Yes** — `build_session_record()` returns `None` (never a fabricated record) when a trading date has no usable rows for the requested underlying; `build_session_records_from_bhavcopy()` additionally reports the raw matched-row count so "zero rows" and "rows present but incomplete" are distinguishable. |
| Can the existing Series 59 validator operate unchanged? | **Yes, unmodified** — `bujji.replay.validator.validate_session`/`validate_corpus` were called directly against records built by this sprint's adapter with no changes to `validator.py`. |

## Validation results (real data)

```
total: 2   valid: 2   invalid: 0
  NIFTY-2026-07-21  True  ()
  NIFTY-2026-07-22  True  ()
```

## Sample corpus manifest (real data)

```
CorpusManifest(
  corpus_id='CORPUS-cb6803dc52552690',
  source_description='NSE official F&O Bhavcopy (nsearchives.nseindia.com),
    NIFTY index options, EOD settlement snapshot',
  trading_dates=('2026-07-21', '2026-07-22'),
  session_count=2,
  checksum='90000569fed284e9b15916c527c1d23a2bb1452cb19a12ad85370ec540b16ad0',
  generation_timestamp='2026-07-23T00:00:00',
  schema_version='1.0.0'
)
```

Each session's real option chain contained 1,584–1,628 NIFTY contracts
across 15+ real expiries (weekly through multi-year LEAPS), built and
validated from `build_corpus()` (Series 59, unmodified).

## Series 58 compatibility — confirmed, unmodified

`result.scenarios`/`result.timestamps` from this real corpus were
passed directly into
`HistoricalQualificationRunner.run_corpus(scenarios, timestamps)`
(Series 58) with no adapter and no code change to either module. Both
real sessions were admitted through Health → Circuit Breaker → Rate
Limiter (all `HEALTHY`/`CLOSED`/`PERMITTED`) and reached the Shadow
Runtime, dispatching (attempting to) into `PaperBroker` exactly as
designed.

## A second, distinct dependency discovered — disclosed, not concealed

Both real sessions came back `FAILED` at the Strategy Selector stage
(`NO_STRATEGY`), cascading to `EMPTY_SESSION`/`EMPTY_POSITION_PLAN`.
Diagnosis: `market_context`/`market_opinion`/`context_stability`/
`calibration`/`governance`/`lifecycle`/`contract` are `None` for every
session this adapter builds — because **no historical MIC v2
classification exists for these real dates**. MIC v2 classifications
are BUJJI's own derived intelligence output (Series 31's own
architecture — a separate process, `/opt/bujji-mic-v2/`), not raw
market data any external provider publishes. `evidence_interpreter`
(Series 32) correctly maps an absent classification to `UNKNOWN`
per-field (never fabricated), and `strategy_selector` (Series 34)
correctly declines to select a strategy from `UNKNOWN` market state —
this is existing, frozen, correct behavior, not a defect discovered in
this sprint.

This means: **the historical option-chain data blocker (Series 60) is
now removed**, but a second, previously-invisible dependency is now
exposed — reproducing MIC v2's own classification decisions for
historical dates, which requires running MIC v2 itself in a
replay/backtest mode against historical raw market data. That is a
substantially larger undertaking, squarely out of scope for a
data-*acquisition* sprint, and is not addressed here. It should not be
mistaken for a failure of this sprint's own objective (sourcing
historical option-chain data), which succeeded.

## Deliverables produced

1. Comparison matrix — above.
2. Recommendation with technical justification — above.
3. Minimal ingestion adapter: `bujji/replay/option_chain_ingestion.py`
   (new file; `bujji/replay/{validator.py, corpus_builder.py,
   manifest.py}` unmodified).
4. Sample corpus manifest from real historical option-chain data —
   above.
5. Validation results from the unmodified Series 59 validator — above.
6. Confirmation that Series 58's qualification framework consumes the
   corpus without modification — above.
7. `tests/test_option_chain_ingestion.py` (8 tests, all passing) —
   validates the adapter itself only, using inline fixtures shaped
   exactly like real bhavcopy rows; no runtime/trading/qualification
   module was touched.

## Whether Series 60 can now be rerun

**Partially.** A real, validated, authorized historical option-chain
corpus can now be built (removing Series 60's original blocker), and
it flows through Series 58 unmodified. However, a genuine Historical
Qualification Campaign — one that produces `COMPLETED` outcomes and
meaningful health/circuit/rate-limiter distributions, not uniform
`FAILED`-at-Strategy-Selector outcomes — additionally requires a
historical MIC v2 classification source, which does not yet exist.
Rerunning Series 60 today with only this sprint's corpus would produce
an honest but uninformative result (100% `FAILED`, `NO_STRATEGY`) —
technically a "campaign," but not the kind of operational evidence
Series 60 was designed to gather. Recommend scoping a follow-on
data-acquisition effort for historical MIC v2 classification (or a
documented, clearly-labeled placeholder classification policy, if the
user wants to proceed with option-chain-only qualification in the
interim) before re-running Series 60 for real.
