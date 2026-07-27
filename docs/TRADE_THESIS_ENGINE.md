# Trade Thesis Engine v1 (TTPI v1)
## BUJJI Engineering Series 92

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus. Inserted as a new layer BETWEEN MSI and Strategy Selection
Foundation:

```
Observation -> Events -> Episodes -> MSI -> Trade Thesis
    -> Strategy Selection Foundation -> Trade Construction -> Portfolio Construction
```

This layer articulates a market thesis in plain language before any
implementation choice is made. It never picks strikes, never
constructs trades, never scores profitability, and **never modifies
Strategy Selection Foundation, which remains completely unchanged**
(verified: SSF's own function signature and logic are untouched;
`grep`-confirmed zero diffs to `bujji/msi_strategy_selection_foundation/`).

---

## 1. Deliverable 1 — Capability Audit

Every input is a real, already-computed MSI output -- nothing here re-derives upstream reasoning:

| Domain | Fields consumed | What they contribute |
|---|---|---|
| Price Structure (Series 78) | `structure_state`, `trend_state`, `compression_state`, `expansion_state`, `structure_integrity` | Trend/range/reversal votes; the `EVENT_RISK` gate (`structure_integrity == CONFLICTED`). |
| Market Structure (Series 79) | `breakout_state`, `breakdown_state`, `structural_balance`, `structure_location` | The highest-priority votes: breakout/failed-breakout/mean-reversion/range-persistence. |
| Market Direction (Series 85) | `overall_direction` | `directional_expectation` (real pass-through); supporting/conflicting sign-agreement check for directional theses. |
| Participant Positioning (Series 86) | `positioning_bias` | Supporting/conflicting sign-agreement check alongside Market Direction (never votes for a thesis TYPE on its own). |
| Volatility Structure (Series 88) | `expansion_state`, `compression_state`, `volatility_regime`, `expected_move_pct` | Volatility votes; `volatility_expectation` and `expected_move` (always reported, regardless of thesis type). |
| Consensus (Series 81) | `consensus_level` | One of three conviction-rubric inputs. |

Strategy Selection Foundation itself was investigated too: its 13 strategy-family definitions were reviewed to confirm this package never needs to reference any of them (Deliverable 5) -- confirmed, and enforced structurally by an AST test that fails if any strategy-family identifier fragment (`LONGSTRADDLE`, `IRONCONDOR`, `BULLCALL`, etc.) ever appears in this package's source.

## 2. Deliverable 2 — TradeThesisAssessment

Immutable, frozen. All spec-required fields present: `thesis_type`, `market_expectation` (a real, deterministic plain-language sentence), `expected_move`, `expected_time_horizon`, `volatility_expectation`, `directional_expectation`, `conviction`, `invalidation_conditions`, `supporting_domains`, `conflicting_domains`, `explanation`, `provenance`, `assessment_id`, `schema_version`.

## 3. Deliverable 3 — Thesis Taxonomy

All 10 required thesis types implemented: `TREND_CONTINUATION`, `TREND_REVERSAL`, `RANGE_PERSISTENCE`, `VOLATILITY_EXPANSION`, `VOLATILITY_COMPRESSION`, `BREAKOUT`, `FAILED_BREAKOUT`, `MEAN_REVERSION`, `EVENT_RISK`, `NO_TRADE`. These are MARKET THESES -- the taxonomy module contains no reference to any strategy family, strike, or leg.

## 4. Deliverable 4 — Thesis Engine

Each contributing domain "votes" for at most one thesis type from its own already-computed state (a real reapplication of the same lens-reconciliation idea Series 85/89 already use elsewhere -- not duplicated code). A fixed, disclosed priority order resolves ties (never a numeric score, never historical-return-driven):

1. **`EVENT_RISK`** -- gated first: Price Structure's own `structure_integrity == CONFLICTED` AND at least 2 domains produce genuinely distinct votes. A disclosed proxy for "anticipated event risk," since no real economic-calendar feed exists anywhere in this codebase (the same kind of disclosed limitation as Series 90's OI-based liquidity proxy).
2. **Market Structure's vote** (breakout/failed-breakout/mean-reversion/range-persistence) -- most information-dense when present.
3. **Volatility Structure's vote** (expansion/compression).
4. **Price Structure's vote** (trend continuation/reversal/range-persistence/volatility, as a fallback when Market Structure and Volatility Structure are both silent).
5. **`NO_TRADE`** -- the genuine, honest fallback when no domain votes at all.

Exactly one thesis is always produced. The engine never ranks strategies, never constructs positions, never calls a broker.

## 5. Deliverable 5 — Strategy Mapping (thesis independence)

Demonstrated structurally, not just by example: this package's source contains **zero references to any strategy family name** (enforced by an AST vocabulary test). A `VOLATILITY_EXPANSION` thesis is equally compatible with a long straddle, long strangle, long call, long put, or debit spread -- the thesis textually says "the market is likely to experience a volatility expansion," nothing about implementation. That choice remains entirely Strategy Selection Foundation's job downstream, unmodified.

## 6. Deliverable 6 — Real 41-day corpus replay

Unlike Series 90/91 (which only ran on the 35 days Strategy Selection actually chose a family), **the Thesis Engine runs on all 41 days** -- it needs only MSI outputs, which exist for every real day in the corpus. This is itself a real, useful finding: thesis formation is broader in applicability than strike-level construction.

- **Thesis type distribution**: `RANGE_PERSISTENCE 14, TREND_CONTINUATION 9, VOLATILITY_EXPANSION 6, BREAKOUT 6, TREND_REVERSAL 3, VOLATILITY_COMPRESSION 2, NO_TRADE 1`.
- **Conviction distribution**: `MODERATE 19, HIGH 14, LOW 7, NONE 1` (the one `NONE` is the single `NO_TRADE` day).
- **No-thesis (`NO_TRADE`) frequency**: 1/41 (~2%) -- BUJJI forms SOME market thesis on nearly every real day in this corpus.
- **Conflicting-evidence frequency**: 11/41 (~27%) -- a real, disclosed rate at which at least one domain's own vote or directional lean genuinely disagreed with the winning thesis; never suppressed or averaged away.
- **Directional expectation distribution**: spans the full real MDI vocabulary (`NEUTRAL 7, BULLISH 7, STRONG_BULLISH 6, WEAK_BULLISH 5, STRONG_BEARISH 4, BEARISH 4, MIXED 3, WEAK_BEARISH 3, UNKNOWN 2`).
- **Volatility expectation distribution**: `STABLE 37, EXPANSION 4` -- matches VSB's own real regime distribution reported in Series 88/89/90/91's replays.
- **`MEAN_REVERSION`, `FAILED_BREAKOUT`, and `EVENT_RISK` were never triggered in this corpus** -- their logic exists and is unit-tested directly, but has no real-corpus exercise yet, disclosed rather than hidden (mirrors Series 90's identical disclosure for its own never-selected families).
- Replayed twice: **thesis `assessment_id`s were byte-identical** across both runs.

No tuning was performed against these numbers -- the vote priority order and conviction rubric were fixed before this replay ran.

## 7. Deliverable 7 — Explainability

Every `TradeThesisAssessment.explanation` answers:
- **Why this thesis?** `why_this_thesis` -- which domain's vote won, or the `EVENT_RISK`/`NO_TRADE` gate condition.
- **Which evidence supports it?** `supporting_evidence` -- every domain whose real reading agrees.
- **Which evidence argues against it?** `conflicting_evidence` -- every domain whose real reading disagrees, never suppressed.
- **What would invalidate it?** `invalidation_conditions` -- a concrete, thesis-type-specific statement (e.g. *"invalidated if realised volatility contracts and consensus weakens"* for `VOLATILITY_EXPANSION`, matching the spec's own example verbatim), plus an additional disclosed caveat whenever a directional thesis coincides with mixed/unknown directional evidence.

## 8. Deliverable 8 — Integration

Demonstrated end-to-end on real data, no live broker calls, no strikes, no execution anywhere:

```
Observation (real Bhavcopy + intraday) -> Events -> Episodes -> MSI (PSI/MSSI/MDI/MPPI/VSB/Consensus)
  -> Trade Thesis -> Strategy Selection Foundation (unmodified)
```

Sample real day: `2026-05-25: MSI complete -> Thesis=TREND_CONTINUATION (conviction=HIGH, directional=STRONG_BULLISH, volatility=STABLE) -> SSF suitability computed independently, unmodified, for 13 families.` Thesis is computed immediately adjacent to SSF's own invocation in the replay script, demonstrating the pipeline's real sequencing -- SSF's function signature and internal logic were never touched, per the explicit constraint.

## 9. Relationship to strategy families

The Thesis Engine and Strategy Selection Foundation are deliberately decoupled: SSF's 13 families each declare their own suitability rules against MDI/MSSI/Consensus/VSB directly (unchanged since Series 87/88), and this package's thesis is NOT wired into that gating in this series -- doing so would itself be a modification of SSF, explicitly out of scope. The relationship today is sequential and observational, not a data dependency; a future series could explore having SSF (or its successor) consume `TradeThesisAssessment` as an additional, optional input, without changing what this package produces.

## 10. Future role in Position Construction

A future consumer (e.g. a redesigned Strategy Selector) could read `TradeThesisAssessment.thesis_type` + `volatility_expectation` + `directional_expectation` to narrow the SUITABLE family set down to families whose `STRATEGY_STATE_FIT` (Series 89's own taxonomy) semantically matches the thesis -- e.g. only offering `IRON_CONDOR`/`IRON_FLY` when the thesis is `RANGE_PERSISTENCE`, not `TREND_CONTINUATION`. This package does not do that wiring itself (out of scope, would modify SSF/MSS); it only produces the thesis for a future consumer to use.

## 11. Known limitations

- `MEAN_REVERSION`, `FAILED_BREAKOUT`, `EVENT_RISK` have no real-corpus exercise yet (Section 6).
- `EVENT_RISK` is a structural-conflict proxy, not a real economic-calendar-driven read -- no such feed exists anywhere in this codebase.
- `expected_time_horizon` is a single fixed structural convention (`NEXT_SESSION`), since no upstream domain supplies an actual duration estimate -- not derived per-thesis.
- Conviction is a simple, disclosed 0-3 point rubric (Section 4/Deliverable 4), not a calibrated probability.

## 12. Deliverable 10 — Recommendation

Evidence-based, from the real replay measurements above:

1. **Thesis formation is broad and reliable** (39/41 days produce a real, non-`NO_TRADE` thesis; determinism confirmed) -- there is now a real, plain-language market view for almost every day, ready for a downstream consumer.
2. **Conflicting evidence occurs on a meaningful 27% of days** -- a real signal that Consensus/thesis disagreement is common enough to matter, but this series intentionally stops short of resolving it (that remains Strategy Selection's or a future arbitration layer's job).
3. **The thesis-to-strategy-family wiring itself (Section 9/10) is the single largest unrealized value** from this series: right now, SSF and MSS still reason independently of the thesis this package produces.

**Recommended next step: Series 93 — Thesis-Aware Strategy Selection**, wiring `TradeThesisAssessment` as a NEW, additive, optional input into a strategy-selection layer (either extending MSS or a successor), narrowing the already-SUITABLE family set to those whose declared market-state fit matches the real thesis -- without modifying SSF's or MSS's existing suitability/scoring logic, exactly mirroring how Series 88 additively wired Volatility Structure into SSF without breaking anything that came before it.
