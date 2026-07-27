# Historical Qualification Campaign v1

**BUJJI Options OS v3 — Engineering Series 60**

## Status

**BLOCKED — awaiting validated historical option-chain data.** See
`reports/historical_campaign_v1.md` and
`reports/historical_campaign_v1.json` for the full campaign record.

## What this sprint is, and is not

This was an operational validation exercise, not a software
development sprint. No production, runtime, replay, qualification, or
trading module was modified. No test file was added, since the
specification only requires tests "if any are required" to "validate
campaign tooling" — no new campaign-specific tooling was written
beyond directly invoking the already-tested Series 58/59 modules
(`bujji.replay.validator`, `bujji.replay.corpus_builder`), so no new
test file was needed; existing coverage for those modules already
verifies the behavior this campaign relied on.

## Why the campaign is blocked

Series 59's replay corpus pipeline requires a real, point-in-time
NIFTY option-chain snapshot for every session — this is not a
Series 60 requirement, it is Series 42's Contract Builder's own,
already-frozen requirement (a strategy contract cannot be built from
spot price alone). No source of historical option-chain data exists
anywhere on the production host, and the FYERS MCP tool set connected
in this environment (`fyers_historical`, `fyers_quote`, `fyers_ohlc`,
`fyers_ltp`, `fyers_instruments`, order/position endpoints) has no
option-chain endpoint at all, historical or live. This was confirmed,
not assumed — see the full investigation trail in
`reports/historical_campaign_v1.md`.

Real NIFTY spot (index) historical data **was** successfully retrieved
(`fyers_historical`, 8 real trading days, 2026-07-13 through
2026-07-22) and run through the Series 59 ingestion pipeline, which
correctly classified every session as invalid (`missing option chain`)
rather than fabricating, interpolating, or silently dropping the gap.
This confirms the pipeline itself works correctly against genuine
market data — the missing piece is the data, not the code.

## Explicit compliance with the specification's own contingency

The specification states: *"If a suitable corpus is not yet available,
stop after validating the ingestion pipeline and report that the
campaign cannot proceed due to missing evidence. Do not substitute
synthetic data while claiming the objectives of this series have been
met."* That is exactly what happened: the ingestion pipeline was
validated against real spot data, the resulting gap was documented
honestly, and no synthetic option-chain data was substituted to
produce a superficially complete campaign report.

## What was deliberately not done, and why

- **No multi-configuration `RateLimiterConfig` comparison.** With zero
  valid sessions, every configuration would reject identically at the
  Contract Builder stage — comparing them would produce no real
  operational signal, only the appearance of one.
- **No health/circuit/rate-limiter distribution statistics beyond
  empty ones.** Reporting non-trivial-looking distributions from zero
  admitted sessions would misrepresent the state of the system.
- **No live qualification readiness recommendation.** No operational
  evidence was gathered this campaign; recommending progression toward
  Milestone D on the basis of an empty campaign would be exactly the
  kind of unsupported conclusion this project's own discipline (every
  conclusion traceable to recorded evidence) forbids.

## Verification

- Qualification fingerprint: `2328e0f77ec312eeca318946df293d91`
  (`/opt/bujji/qualification/baselines.json`, mtime `Jul 20 23:43`) —
  unchanged; this sprint made no code change capable of affecting it.
- No live broker authentication occurred — the FYERS MCP calls made in
  this sprint (`fyers_historical`) are read-only historical market-data
  queries, entirely separate from BUJJI's own `FyersBroker`/
  authentication modules, which were never invoked.
- No live orders were placed.
- No prior Engineering Series file was modified.

## Recommendation

Source a historical, point-in-time NIFTY option-chain dataset from an
authorized provider, then re-run this exact campaign design — the
ingestion pipeline (Series 59) and the investigation approach used
here require no changes to consume it. Until that data exists, further
engineering effort should not go toward more runtime or qualification
infrastructure (Series 31–59 already cover that completely) but toward
resolving this specific, external data-sourcing dependency.
