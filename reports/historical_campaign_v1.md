# Historical Qualification Campaign v1

**BUJJI Options OS v3 — Engineering Series 60**

## Status: **BLOCKED — awaiting validated historical option-chain data**

This campaign did not run against a complete, valid historical replay
corpus, and no qualification report claiming completed/rejected/failed
session counts against real market conditions has been produced. Per
the specification's own explicit instruction, this is documented as a
qualification dependency, not a software deficiency, and no synthetic
data was substituted to manufacture the appearance of a completed
campaign.

## What was checked before declaring this blocked

1. **Searched for an existing real historical corpus.** No NIFTY
   spot/option-chain historical dataset exists anywhere on the
   production host (`/opt/bujji/`, `/opt/bujji-mic-v2/`) — only
   contract-metadata (`data/instrument_master/fyers_fo_NSE.csv`) and
   unrelated operational journals.
2. **Checked whether a real data source was reachable from this
   environment.** A connected, already-authorized FYERS MCP tool set
   is available in this session. `fyers_historical` successfully
   returned 8 real trading days of NIFTY 50 index daily OHLCV candles
   (2026-07-13 through 2026-07-22) — genuine market data, not
   synthetic.
3. **Checked whether historical option-chain data was reachable.**
   The connected FYERS MCP tool set exposes `fyers_quote`, `fyers_ohlc`,
   `fyers_ltp`, `fyers_instruments` (symbol master), and order/position
   endpoints — **no historical or even live option-chain endpoint
   exists in this tool set.** This is a known, disclosed limitation of
   retail broker APIs generally: option chains are a live/current-state
   product, not a queryable historical time series, and FYERS's own
   API (and this MCP's surface over it) does not expose one.
4. **Ran the real spot data through the Series 59 ingestion pipeline**
   (`bujji.replay.validator.validate_corpus`,
   `bujji.replay.corpus_builder.build_corpus`) to confirm the pipeline
   itself works correctly against genuine, non-synthetic input, and to
   observe how it handles the resulting gap.

## Ingestion pipeline validation result

8 real `HistoricalSessionRecord`s were built from the fetched NIFTY 50
index candles (one per trading day, `spot` = real daily close,
`option_chain_entries` = empty, since no historical chain source
exists). Running these through `validate_corpus()`:

```
total: 8   valid: 0   invalid: 8
  NIFTY-2026-07-13  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-14  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-15  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-16  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-17  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-20  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-21  False  ('missing option chain (no entries)',)
  NIFTY-2026-07-22  False  ('missing option chain (no entries)',)
```

`build_corpus()` on the same 8 records produced a valid, immutable
`CorpusManifest` with `session_count=0`:

```
CorpusManifest(
  corpus_id='CORPUS-7663f6705edcd405',
  source_description='NIFTY 50 index daily candles via FYERS MCP
    fyers_historical (spot only; no historical option-chain source
    available)',
  trading_dates=(),
  session_count=0,
  checksum='af57c15a4abec64b5e4db71a6b147a217ff95b117499d84566387ded2ecad493',
  generation_timestamp='2026-07-23T00:00:00',
  schema_version='1.0.0'
)
```

This is the correct, designed behavior, not a bug: Series 59's
validator never repairs or fabricates a missing option chain, and
Series 58's runner never receives a session it cannot classify
honestly. The ingestion pipeline is confirmed working against real
market data; it is the *evidence*, not the *pipeline*, that is
missing.

## Why the campaign cannot proceed past this point

Every stage from the Production Composition Root through the Shadow
Runtime requires a `NiftyOptionChainSnapshot` to build a strategy
contract (Series 42's NIFTY Contract Builder fails immediately on an
empty chain — `INVALID_SPOT`/insufficient-strikes outcomes, as
established in Series 42/46's own tests). Running the runtime against
8 sessions that are guaranteed to fail contract construction would not
produce genuine operational evidence about health/circuit/rate-limiter
behavior under *realistic* conditions — it would only reproduce the
same synthetic-data failure mode already demonstrated in Series 58's
own smoke tests. Per this sprint's explicit instruction, that outcome
must not be dressed up as a completed qualification campaign.

## What is still missing

A source of **historical, point-in-time NIFTY option-chain snapshots**
(strikes, expiries, and either LTP or at least valid contract symbols,
for each session's timestamp) — not obtainable from the FYERS MCP tool
set connected in this environment, and not present anywhere on the
production host. This is an external data-sourcing dependency, not a
BUJJI Options OS defect.

## Multi-configuration comparison

Not performed. Comparing `RateLimiterConfig` behavior across two
intervals over a corpus with zero valid sessions would produce no
operational evidence (every session is rejected identically at the
Contract Builder stage regardless of admission-control configuration)
— running it would manufacture the appearance of a comparison without
any real signal behind it.

## Recommendation

Do not proceed with Engineering Series 61+ as further runtime/
qualification infrastructure. The system (Trading Brain, Runtime,
operational controls, qualification framework, corpus pipeline) is
complete and already proven against synthetic fixtures across Series
31–59. The single remaining blocker to a genuine Historical
Qualification Campaign v1 is sourcing real, point-in-time NIFTY
option-chain history from an authorized data provider. Once that
source exists, this campaign (Series 60) should be re-run exactly as
designed here — the ingestion pipeline and campaign tooling built and
verified in this sprint require no further changes to consume it.
