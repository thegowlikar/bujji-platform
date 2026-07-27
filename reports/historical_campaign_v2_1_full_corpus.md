# Historical Qualification Campaign v2.1 — Full Corpus Rerun

**BUJJI Options OS v3 — Rerun with Series 65 Governance Certification Evidence**

## Status: Complete — governance now evidence-driven, capability leap confirmed and deterministic

## Corpus

Full 41-session real corpus (2026-05-25 to 2026-07-22, NSE official
Bhavcopy), unchanged from Qualification Data Enrichment Sprint B /
Series 64's regression run. Not regenerated.

```
corpus_id=CORPUS-28a6ba13849f9608
checksum=09673a3dc182a3677df14ec3c610b427154243a9676f3c79facd4ba563b3729e
```

Identical to every prior run of this corpus — confirming Series 65
changed only intelligence evidence, never the underlying market data.

## Governance and certification, before vs after Series 65

| | Before (Sprint B / v2.1 13-day) | After (Series 65) |
|---|---|---|
| Certification | Never computed (always `None` supplied) | **41/41 `CERTIFIED`** |
| Governance | 41/41 `REJECTED` | **34/41 `APPROVED`, 7/41 `APPROVED_WITH_WARNINGS`, 0 `REJECTED`** |

## Qualification outcome

```
report_id=QREPORT-1f479265179511b0   (unchanged identity — same replay set)
total_replay_sessions=41
completed_runs=29        (was 2)
runtime_failures=12      (was 39)
health: HEALTHY 41/41, circuit: CLOSED 41/41, rate_limit: PERMITTED 41/41
```

**29/41 sessions (71%) now reach `COMPLETED`** — real, full Contract
Builder → Order Construction → `DISPATCHED` execution into
`PaperBroker`, up from 2/41. Strategy diversity emerged for the first
time: `DIRECTIONAL_PUT_SPREAD` (dominant), `DIRECTIONAL_CALL_SPREAD`,
`PREMIUM_VWAP_STRADDLE`, `IRON_CONDOR`.

## Remaining 12 non-completed sessions

- 4 sessions: `COVERED_CALL` selected, `STRATEGY_OUT_OF_V1_SCOPE`
  (Series 63's explicit, documented boundary — unchanged, expected).
- 8 sessions: `NO_STRATEGY` — Strategy Selector correctly declined on
  thin/`UNKNOWN`/`TRANSITION` evidence (e.g. 2026-07-17, `market_context=TRANSITION`)
  — expected conservative behavior, not a defect.

No unexplained failure anywhere in the corpus.

## Determinism

Run twice, independently (41 × 2 real MIC v2 publication-replay
sessions, each including a real certification pass). **Identical**
`report_id`, **identical** `completed_runs=29`, and **identical**
per-session outcome for all 41 sessions across both runs.

## Qualification fingerprint

`2328e0f77ec312eeca318946df293d91` (production `baselines.json`) —
unchanged.

## Recommendation

This is the strongest evidence yet gathered for this project: real
governance, real certification, real strategy diversity, real
completions, on real market data, fully deterministic. The single
remaining architectural gap is the `COVERED_CALL`/`CASH_SECURED_PUT`-class
v1 scope boundary (Series 63) — everything else in this corpus now
behaves exactly as designed. Recommend broadening the historical
window further (per Sprint B's own prior recommendation) before
considering controlled live-paper qualification staging.
