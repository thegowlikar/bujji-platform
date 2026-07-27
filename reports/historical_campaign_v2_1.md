# Historical Qualification Campaign v2.1

**BUJJI Options OS v3 — Regression Qualification After Series 63 Failure-Taxonomy Refinement**

## Status: Complete — behavioral equivalence confirmed, no regressions

## Corpus manifest (identical to Campaign v2 — not regenerated)

```
corpus_id=CORPUS-de6e1ad72ef28aa1
source_description="NSE official F&O Bhavcopy, NIFTY index options, 13 real trading days 2026-07-06 to 2026-07-22"
trading_dates=(2026-07-06 .. 2026-07-22, 13 consecutive real NSE trading days)
checksum=5fcd01b20fb9df8a5963282f6d55d4f868c93d04586e5d6da4824a66fbab7cce
```

The corpus was **not regenerated**. The same 13 trading dates were
re-fetched from NSE's official, immutable historical archive
(`nsearchives.nseindia.com`) and confirmed byte-identical to Campaign
v2's original files (extracted CSV sizes match exactly, e.g.
2026-07-21: 7,007,317 bytes; 2026-07-22: 7,055,024 bytes) before
replay. The resulting corpus manifest checksum is **identical** to
Campaign v2's (`5fcd01b20fb9df8a5963282f6d55d4f868c93d04586e5d6da4824a66fbab7cce`),
confirming the input evidence is unchanged.

## Qualification fingerprint

| | Value |
|---|---|
| Campaign v2 | `RFP-0000000000000000` (per-session, unchanged since Series 54) |
| Campaign v2.1 | `RFP-0000000000000000` — identical |
| Production `baselines.json` (md5) | `2328e0f77ec312eeca318946df293d91`, mtime `Jul 20 23:43` — unchanged before and after this campaign |

## Runtime version

Series 54 (Composition Root) → Series 63 (Contract Builder strategy
coverage). All modules frozen except the disclosed, documented Series
63 taxonomy refinement.

## Total replay sessions

13 (identical to Campaign v2 — same corpus, same count).

## Replay coverage

Identical replay path to Campaign v2: real NSE Bhavcopy → Series 59
corpus → Series 61 MIC Evidence replay → Series 62 MIC publication
replay → real `PipelineInput` → Series 63 Contract Builder (now with
the refined taxonomy) → Runtime → Health/Circuit/Rate-Limiter →
Qualification Recorder.

## Campaign v2 vs v2.1 comparison table

| Session | Outcome v2 | Outcome v2.1 | Strategy v2 | Strategy v2.1 | Health/Circuit/Rate-Limit identical | Classification |
|---|---|---|---|---|---|---|
| 2026-07-06 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-07 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-08 | FAILED | FAILED | None | None | Yes | 1 — No change |
| **2026-07-09** | FAILED | FAILED | **COVERED_CALL** | **COVERED_CALL** | Yes | **2 — Expected diagnostic refinement** |
| 2026-07-10 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-13 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-14 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-15 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-16 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-17 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-20 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-21 | FAILED | FAILED | None | None | Yes | 1 — No change |
| 2026-07-22 | FAILED | FAILED | None | None | Yes | 1 — No change |

**0 sessions in category 3 (unexpected behavioral change).**
**0 sessions in category 4 (newly discovered defect).**
**12 sessions in category 1 (no change) — their contract-construction
failure never touched the strategy-template branch Series 63 modified
(all resolved via `INSUFFICIENT_DATA`, unaffected).**
**1 session in category 2 (expected diagnostic refinement) — 2026-07-09.**

## Strategy-selection statistics

Identical to Campaign v2: 1/13 sessions selected a real strategy
(`COVERED_CALL`, 2026-07-09); 12/13 remained `NO_STRATEGY`. Series 63
does not touch Strategy Selector — this was never expected to change,
and it did not.

## Contract-construction statistics

| | Campaign v2 | Campaign v2.1 |
|---|---|---|
| Sessions reaching Contract Builder with a real strategy | 1 (2026-07-09) | 1 (2026-07-09) |
| `CONSTRUCTED` | 0 | 0 |
| `FAILED` | 1 | 1 |

## Failure-reason distribution, before and after Series 63

| Failure reason | Campaign v2 count | Campaign v2.1 count |
|---|---|---|
| `INSUFFICIENT_DATA` (no strategy selected, `NO_STRATEGY`) | 12 | 12 |
| `UNKNOWN_STRATEGY` | 1 (2026-07-09) | **0** |
| `STRATEGY_OUT_OF_V1_SCOPE` | 0 | **1 (2026-07-09)** |

Exactly one occurrence moved from `UNKNOWN_STRATEGY` to
`STRATEGY_OUT_OF_V1_SCOPE`. No other failure-reason count changed.

## Verification of the 2026-07-09 scenario

Re-run directly against real historical data (real NSE Bhavcopy, real
MIC v2-derived classification `market_context=TRENDING_DOWN`,
`market_opinion=BEARISH`, `context_stability=MOSTLY_STABLE`):

```
strategy: COVERED_CALL SELECTED                  (unchanged from Campaign v2)
contract construction: FAILED STRATEGY_OUT_OF_V1_SCOPE   (was: FAILED UNKNOWN_STRATEGY)
execution_state: FAILED_VALIDATION               (unchanged from Campaign v2)
```

- 2026-07-09 no longer reports `UNKNOWN_STRATEGY`. **Confirmed.**
- The Strategy Selector still chooses `COVERED_CALL`. **Confirmed —
  identical.**
- Runtime outcome is otherwise unchanged (`FAILED`,
  `FAILED_VALIDATION`). **Confirmed.**
- Report identifiers remain deterministic (`QREPORT-bbe021ca1cfcc16f`,
  identical to Campaign v2). **Confirmed.**
- Qualification fingerprint remains unchanged. **Confirmed.**

## Determinism verification

The complete 13-session campaign was run twice, independently
(including 13 × 2 = 26 real MIC v2 subprocess invocations per run, 52
total). Result: **the two runs' full JSON output — corpus manifest,
every session's strategy decision, every session's runtime outcome,
every health/circuit/rate-limit decision, and the final
`QualificationReport` (`report_id=QREPORT-bbe021ca1cfcc16f` both
times) — were byte-for-byte identical.**

The previously disclosed `lifecycle_id` exception (Series 62) remains
tracked separately and was reconfirmed **not** to affect this
campaign: `lifecycle_id` is never surfaced into `ReplayScenario`/
`PipelineInput`/`QualificationRecord`; only the `lifecycle`
classification string is consumed downstream, and it was identical
across both runs for every session, exactly as in Campaign v2.

## Unexpected behavioral differences

**None.** Every session's outcome, strategy decision, and
admission-control decision matches Campaign v2 exactly. The only
observed difference across the entire corpus is the single, expected,
documented failure-reason refinement on 2026-07-09.

## Recommendation

**Series 63 is confirmed to be a pure diagnostic refinement with zero
behavioral regression.** The Contract Builder, Trading Brain, Runtime,
and operational controls all produce identical decisions before and
after Series 63; only the human-readable/auditable classification of
one already-failing case improved.

**On live-paper qualification readiness:** Not yet. This campaign
confirms Series 63 introduced no regression — it does not, by itself,
add new evidence of trading readiness. The underlying findings from
Campaign v2 remain unresolved and still gate progress: 0/13 sessions
`COMPLETED`, 12/13 `NO_STRATEGY` due to thin real evidence (no VIX, no
real option OI/bid-ask), and `governance=REJECTED` on 100% of sessions
due to no certification input being wired into the publication replay
bridge. Further evidence-backed engineering work is required before
live-paper qualification: source real option OI/bid-ask and VIX data,
extend the corpus beyond 13 sessions, and wire a real certification
input — exactly the recommendations already on record from Campaign
v2, still outstanding and unaffected by this regression campaign.
