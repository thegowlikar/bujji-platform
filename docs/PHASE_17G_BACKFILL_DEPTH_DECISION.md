# Historical Backfill Depth — Decision

**Status: DECISION RECORDED. No implementation.**

This closes the open item first flagged in 17E's implementation plan
(§8, Q3), repeated unresolved in 17F.0.1, 17F.0.3 (§11 item 1), 17F.0.4
(§9), and 17G (§3, MUST HAVE). One canonical answer, referenced from
those docs rather than re-litigated in each.

## Decision

| Resolution | Backfill depth | Rationale |
|---|---|---|
| **Daily** (spot, futures continuous/near-month series, VIX, option series where available) | **3 years** | Domain A (17G §2.1) needs weekly/monthly structure; 3 years spans multiple full market cycles (trend, consolidation, high/low volatility regimes) — enough for Layer 2's level-memory sample sizes to reach `SUFFICIENT` (17F §4.4) on major levels without an unbounded, indefinitely-growing backfill job. |
| **5-minute / 15-minute** (spot, futures) | **6 months** | Supports short/medium-term structure and Layer 2 intraday level-interaction density (17F.4) — the timeframe most of Domains A–D actually operate on day-to-day. Six months is deliberately conservative: extendable later once the backfill pipeline itself is proven (17E's "narrowly-scoped, test-verified additions" discipline applies to backfill volume, not just code). |
| **1-minute** | **Not backfilled historically.** Captured only going forward, once live capture starts. | The most restrictive and highest-volume tier at most broker APIs, and not required by any of the seven Understanding domains as designed (§2.1–2.7) — Domain A's finer-timeframe structure benefits from live tick/5-min capture, not deep 1-minute history. Backfilling it would be exactly the "maximum data collection" this project has repeatedly rejected as the wrong goal. |
| **Option chain history** | **Not backfilled at all.** Captured only going forward. | FYERS's `historical` endpoint is confirmed (this engagement) to serve OHLC candles for specific option contracts, but full historical *chain snapshots* (every strike, every moment) are not a broker-provided historical product — they only exist from the moment a collector starts polling `optionchain()` live. This is a capture-forward-only reality, not a policy choice. |
| **Futures OI history** | **Not backfilled.** Captured only going forward. | Same reasoning as option chain — OI is only available via `depth()` (17F.0.4 §1), which has no historical form. OI history begins exactly when depth polling begins, and its depth-in-time is bounded by how long the collector has been running, permanently — no backfill can ever change this. |

## What this decision does NOT resolve

- **The exact maximum daily lookback FYERS actually serves.** This
  engagement confirmed `historical` works with 30-day windows (used
  throughout certification scripts); whether 3 years of daily NIFTY/VIX
  history is actually retrievable in practice (single call vs. paginated
  windowed calls) is an implementation detail for 17F.1/17F.2, not a
  policy question — the 3-year *target* is fixed here regardless of how
  many paginated calls it takes to reach it.
- **Whether FYERS retains 3 years of daily history for every instrument
  requested** (e.g., an expired past-contract futures series). Where it
  doesn't, the honest gap is recorded exactly as any other capture
  shortfall would be — via the Completeness Monitor (17E) — never
  padded or approximated to hit the target artificially.

## Consequence for the roadmap (17G §6)

This decision unblocks 17F.2 (Historical Backfill) from remaining an
open question. The backfill job's scope is now specified: 3y daily / 6mo
intraday (5m/15m) for spot, futures, VIX; forward-only for 1-minute,
option chain, and futures OI. No further decision is required before
implementation planning for 17F.2 begins.
