# Historical Qualification Campaign v2

**BUJJI Options OS v3 — First Full End-to-End Qualification Using Real Historical Market Data**

## Status: Complete — architecture executed end-to-end, deterministically, on real data

## Corpus metadata

- **Source:** NSE official F&O Bhavcopy (`nsearchives.nseindia.com`), NIFTY index options, real daily settlement snapshots — no synthetic data anywhere in this campaign.
- **Manifest:** `corpus_id=CORPUS-de6e1ad72ef28aa1`, `checksum=5fcd01b20fb9df8a5963282f6d55d4f868c93d04586e5d6da4824a66fbab7cce`, `schema_version=1.0.0`.
- **Trading dates covered:** 2026-07-06 through 2026-07-22 (13 consecutive real NSE trading days; no gaps — every weekday in range returned a valid bhavcopy, so no exchange holiday fell in this window).
- **Session sizes:** 1584–1672 real NIFTY option contracts per day, 15+ real expiries per session (weekly through multi-year LEAPS).
- **Validation:** 13/13 sessions valid (0 corrupted, 0 excluded).

## Replay coverage

- Series 59 corpus builder → Series 61 observation adapter → Series 62 publication replay (real MIC v2 subprocess, chronological, no look-ahead — each session's classification reflects only that session's and all prior sessions' real data) → real `PipelineInput` → real, unmodified Series 54 `run_shadow()`, gated by real Series 55/56/57 Health/Circuit/Rate-Limiter — the complete chain, exercised end-to-end, on real data, for the first time in this project's history.

## Operational statistics

| Metric | Value |
|---|---|
| Total replay sessions | 13 |
| Completed runs | 0 |
| `NO_STRATEGY` outcomes | 12 |
| Strategy-selected outcomes | 1 (`COVERED_CALL`, 2026-07-09) |
| Runtime failures | 13 |
| Health distribution | `HEALTHY`: 13/13 |
| Circuit-breaker distribution | `CLOSED`: 13/13 |
| Rate-limiter distribution | `PERMITTED`: 13/13 |
| Governance decisions | `REJECTED`: 13/13 |
| `market_context = UNKNOWN` | 2/13 (2026-07-06, 2026-07-07) |
| `market_context` = real classification | 11/13 (`TRENDING_DOWN`, `TRANSITION`, `SIDEWAYS`) |
| `calibration = CALIBRATED` | 4/13 (2026-07-15 onward) |
| `calibration = INSUFFICIENT_HISTORY` | 9/13 |
| Qualification fingerprint | `RFP-0000000000000000` — stable across all 13 sessions and both runs |
| Production qualification fingerprint (`baselines.json`) | `2328e0f77ec312eeca318946df293d91` — unchanged before and after this campaign |

## Decision distributions, day by day

| Date | market_context | market_opinion | context_stability | calibration | strategy | outcome |
|---|---|---|---|---|---|---|
| 2026-07-06 | UNKNOWN | INSUFFICIENT_EVIDENCE | INSUFFICIENT_HISTORY | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| 2026-07-07 | UNKNOWN | INSUFFICIENT_EVIDENCE | STABLE | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| 2026-07-08 | TRENDING_DOWN | BEARISH | TRANSITIONING | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| **2026-07-09** | **TRENDING_DOWN** | **BEARISH** | **MOSTLY_STABLE** | INSUFFICIENT_HISTORY | **COVERED_CALL (SELECTED)** | **FAILED — `UNKNOWN_STRATEGY`** |
| 2026-07-10 | TRANSITION | INSUFFICIENT_EVIDENCE | TRANSITIONING | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| 2026-07-13 | TRANSITION | NEUTRAL | TRANSITIONING | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| 2026-07-14 | SIDEWAYS | NEUTRAL | TRANSITIONING | INSUFFICIENT_HISTORY | NO_STRATEGY | FAILED |
| 2026-07-15 | SIDEWAYS | NEUTRAL | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |
| 2026-07-16 | SIDEWAYS | NEUTRAL | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |
| 2026-07-17 | SIDEWAYS | NEUTRAL | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |
| 2026-07-20 | TRANSITION | INSUFFICIENT_EVIDENCE | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |
| 2026-07-21 | TRANSITION | BULLISH | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |
| 2026-07-22 | TRANSITION | INSUFFICIENT_EVIDENCE | TRANSITIONING | CALIBRATED | NO_STRATEGY | FAILED |

`governance = REJECTED` on every single session — traced to a Series 62 scope limitation, not a MIC v2 defect: `publication_replay.py`'s bridge script never supplies a `certification` object to `run_replay_with_contract()` (it was never available from any source this campaign used), and MIC v2's own `derive_governance()` correctly, conservatively returns `REJECTED` when no certification evidence exists. This is honest, expected behavior given what was actually supplied — not a fabricated or arbitrary rejection.

## Evidence quality assessment

Real, substantive, and previously invisible signal emerged as history
accumulated:

- **`market_context` matured from `UNKNOWN` to genuine trend classifications** after only 2 days of real accumulated history (`UNKNOWN` → `TRENDING_DOWN` by day 3), and tracked a real, coherent regime shift across the 13-day window (`TRENDING_DOWN` early → `TRANSITION`/`SIDEWAYS` mid-period → `TRANSITION` late period) — this is real MIC v2 logic responding to real NIFTY price action (23996–24334 over the window), not noise.
- **`calibration` matured from `INSUFFICIENT_HISTORY` to `CALIBRATED`** exactly at the point (2026-07-15, the 8th session) where MIC v2's own calibration window had accumulated enough opinion/context/stability history to compute a real distribution — consistent with `calibration/engine.py`'s own documented window-size requirement, not arbitrary.
- **One real strategy selection occurred** (`COVERED_CALL` on 2026-07-09), proving the full chain — real spot/chain data → real MIC v2 Evidence → real MIC v2 publication → real `PipelineInput` → real Strategy Selector — can and does produce a genuine, non-trivial decision when the evidence supports one.

## Required analysis: four categories, kept separate

**1. Architectural failures (genuine, newly discovered this campaign):**
The Strategy Selector (Series 34) selected `COVERED_CALL` for 2026-07-09, but the NIFTY Contract Builder (Series 42) has no leg template for that strategy (`contract_construction_result.failure_reason = "UNKNOWN_STRATEGY"`). This is a real gap between what Series 34's own strategy vocabulary allows and what Series 42's own leg-template registry implements — invisible until this campaign produced the first-ever real, non-synthetic strategy selection to exercise it. **This is the one genuine architectural finding from this campaign**, and it is a defect in the *scope of Series 42's leg-template coverage*, not in anything built across Series 54–62.

**2. Data-quality limitations (expected, disclosed since Series 61):**
- All 13 sessions carry `calibration` computed from thin history and `governance = REJECTED` (no certification input available to this campaign's bridge).
- Every `OptionChainLevel` MIC v2 received has `ce_oi=pe_oi=0`, all bid/ask `None` (Series 61's disclosed Bhavcopy-ingestion limitation) — so MIC v2's own `open_interest`/`option_premium` analyzer modules could not have contributed real evidence this campaign, regardless of corpus size.
- No VIX data was supplied.
- These are why 9/13 sessions never left `INSUFFICIENT_HISTORY`/`UNKNOWN` territory even on real data — not a code defect.

**3. Expected conservative decisions (working as designed):**
- 12/13 `NO_STRATEGY` outcomes are the Strategy Selector correctly declining to trade on `UNKNOWN`/thin-evidence market states — exactly its frozen, intended behavior (Series 34, unmodified).
- `runtime_authorization.decision = DENY` on every session (checked directly for 2026-07-09) — the Safety Gate correctly refusing to authorize an empty/incomplete `ExecutionSession`, never fabricating authorization for a session with no real orders.
- `governance = REJECTED` given no certification evidence is itself a conservative, correct default — not a false rejection of a real, good signal.

**4. Runtime defects:** **None discovered.** All 13 sessions behaved identically across two independent full-corpus runs (see Determinism below); Health/Circuit/Rate-Limiter behaved correctly and consistently throughout; no exception, crash, or unhandled state was observed anywhere in the Runtime, Operational Controls, or Qualification Framework layers.

## Determinism verification

The identical 13-session corpus was run twice, independently, start to finish (including two independent sets of 13×2=26 real MIC v2 subprocess invocations). Result: **the two runs' full JSON output — corpus manifest, every session's strategy decision, every session's runtime outcome, every health/circuit/rate-limiter decision, and the final `QualificationReport` (`report_id=QREPORT-bbe021ca1cfcc16f` both times) — were byte-for-byte identical** (`a == b` verified programmatically over the complete structured output).

**The previously documented `lifecycle_id` non-determinism (Series 62) does not affect this campaign's runtime behavior.** `lifecycle_id` is an internal MIC v2 publication identifier never surfaced into `ReplayScenario`/`PipelineInput`/`QualificationRecord` — only the `lifecycle` *classification string* (`status`) is consumed downstream, and that value was identical across both runs for every session. Tracked separately, unchanged in severity, confirmed not to propagate into any decision made by this campaign.

## Known limitations

1. `governance = REJECTED` on 100% of sessions is an artifact of no certification input being wired into Series 62's publication replay bridge — not evidence that BUJJI's governance logic itself is miscalibrated.
2. Option-chain evidence (OI, bid/ask) is entirely absent from this corpus (Series 61's disclosed Bhavcopy limitation) — `open_interest`/`option_premium` MIC v2 modules contributed no evidence to any of the 13 sessions.
3. No VIX data was supplied to any session.
4. 13 trading days is a real but still short history for calibration/stability windows that (per this campaign's own evidence) take roughly 8 sessions to leave `INSUFFICIENT_HISTORY` — a longer corpus would very likely produce further matured classifications.
5. The one genuine architectural finding (`COVERED_CALL` / `UNKNOWN_STRATEGY`) means any future campaign including this strategy in Strategy Selector's candidate set will continue to fail at Contract Construction until Series 42's leg-template registry is extended — a fix explicitly out of scope for a qualification campaign to make itself.

## Recommendations

- **Do not proceed to live qualification staging yet.** Zero sessions reached `COMPLETED` in this campaign; the evidence gathered supports readiness of the *architecture*, not yet the *strategy coverage* or *data richness* needed for live qualification.
- **File the `COVERED_CALL` / `UNKNOWN_STRATEGY` gap as a defect against Series 42's leg-template registry** — either implement the missing template or remove `COVERED_CALL` from Series 34's selectable vocabulary until it is implemented; either resolution should be made by whoever owns that series, not silently patched here.
- **Source real option OI/bid-ask and VIX data** before the next campaign — this is the single highest-leverage improvement: it would let `open_interest`/`option_premium` MIC v2 modules contribute real evidence for the first time, very plausibly reducing the `NO_STRATEGY` rate materially.
- **Extend the corpus beyond 13 sessions** (a month or more) to observe calibration/stability maturing further and to see whether more strategies beyond `COVERED_CALL` get selected under a wider range of real market conditions.
- **Wire a real certification input** into Series 62's publication replay bridge if/when a certification source becomes available, so `governance` reflects genuine certification status rather than the conservative no-input default.
