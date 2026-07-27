# Market Intelligence Observatory — Demonstration

**BUJJI Options OS v3 — Engineering Series 66**

Compares two already-saved qualification reports (Series 65's
41-session governance rerun vs. the 81-session broadened-window
campaign) via `bujji.observatory.report.build_demo_report()`. Nothing
was recomputed — both inputs are read-only.

## Corpus-wide comparison

- 41-day corpus vs 81-day corpus, matched on the 41 shared session IDs.
- **36/41 (88%) sessions changed** at least one classification/strategy/outcome field between the two windows.
- Confirms the finding from the broadened-corpus campaign: window length materially changes MIC v2 intelligence for most dates, not a handful of edge cases.

## Focus case: why did 2026-07-09 change?

```
market_context: SIDEWAYS -> TRANSITION
strategy:       PREMIUM_VWAP_STRADDLE -> None
outcome:        COMPLETED -> FAILED
first_divergence: market_context
```

**Timeline, 41-day corpus:**
1. Evidence — not recorded in the saved qualification report (disclosed limitation, not fabricated).
2. MIC classification — `market_context=SIDEWAYS`, `governance=APPROVED`.
3. Trading Brain interpretation — Strategy Selector evaluated `SIDEWAYS`.
4. Strategy selection — `PREMIUM_VWAP_STRADDLE` selected.
5. Runtime outcome — `COMPLETED`.

**Timeline, 81-day corpus:**
1. Evidence — not recorded (same disclosed limitation).
2. MIC classification — `market_context=TRANSITION`, `governance=APPROVED`.
3. Trading Brain interpretation — Strategy Selector evaluated `TRANSITION`.
4. Strategy selection — `NO_STRATEGY`.
5. Runtime outcome — `FAILED`.

**Root cause, honestly reported:** `market_context` is `mic_v2.context.engine.derive_context`'s own output, itself dependent on a rolling window of prior `MarketContext`/Evidence/quality objects (Series 62's documented composition chain). Extending the corpus from 41 to 81 sessions changed how much history precedes 2026-07-09, and MIC v2's own deterministic logic classified the regime differently as a result. **The specific numeric threshold that flipped is not exposed as data by MIC v2 today** — the Observatory reports this as `UNKNOWN` rather than guessing (see `ThresholdCrossing`).

**Downstream impact:** the `market_context` change is the first divergence; `strategy` and `outcome` both changed as a direct, already-established consequence (Strategy Selector, frozen, declines on `TRANSITION`).

Full machine-readable output: `reports/observatory_demo.json`.
