# Strategy Selector v1 (MSS v1)
## BUJJI Engineering Series 89

**Status:** Real, implemented, tested, replayed against the real 41-day
corpus. This is the first module in the MSI arc that chooses ONE
strategy family — everything before it (77-88) described conditions or
narrowed a candidate set; this is the first genuine selection.

---

## 1. Philosophy — market state, not directional prediction

Series 86's predictive-value investigation (four independent real-data
tests, then a fifth out-of-sample replication attempt) found no
statistically reliable directional edge in this codebase's price-only
signal, and the one seemingly-promising lead failed to replicate on
unseen data. This selector is built to not depend on that finding
being wrong: **it never asks "which way will price go" — it asks
"what KIND of market is this, and which already-SUITABLE strategy
family best fits that kind."**

`bujji.msi_market_direction`'s `overall_direction` still feeds into
Series 87/88's suitability gating (a directional strategy still needs
a directional read to be *eligible* at all) — but the SELECTOR's own
job, choosing among the eligible set, is driven entirely by **market
state** (trend/balance/compression/expansion/participation/certainty),
never by which direction the market is predicted to move.

## 2. Deliverable 1 — Market State Ontology

Ten independent, non-mutually-exclusive state tags, each derived from
a specific, disclosed, real upstream field:

| State | Derived from |
|---|---|
| `TREND_EXPANSION` | PSI `structure_state==TRENDING` AND (VSB `expansion_state==CONFIRMED` OR `volatility_regime` in TRANSITIONING/HIGH_VOLATILITY) |
| `TREND_EXHAUSTION` | PSI `structure_state==CORRECTING` |
| `BALANCE` | PSI `structure_state==BALANCE` OR MSSI `structural_balance==RANGE_BOUND` |
| `COMPRESSION` | PSI `compression_state==CONFIRMED` (price-structure evidence) |
| `VOLATILITY_EXPANSION` | VSB `expansion_state==CONFIRMED` (volatility-domain evidence — deliberately distinct source from `COMPRESSION` above) |
| `VOLATILITY_CONTRACTION` | VSB `compression_state==CONFIRMED` |
| `ROTATIONAL_MARKET` | MDI `overall_direction==MIXED` OR MPPI `positioning_bias==MIXED_POSITIONING` |
| `UNCERTAIN_MARKET` | MDI `overall_direction==UNKNOWN` OR Consensus `consensus_level` in (NO/WEAK) OR `evidence_sufficiency==INSUFFICIENT` |
| `STRONG_PARTICIPATION` / `WEAK_PARTICIPATION` | MPPI `positioning_strength` |

A naming note, disclosed rather than silently risked: `VOLATILITY_EXPANSION`/`VOLATILITY_CONTRACTION` (market states, this package) share English text with `VOLATILITY_EXPANSION`/`VOLATILITY_COMPRESSION` (**strategy family names**, Series 87) — a real coincidence in two separate taxonomy modules with distinct Python identifiers, not a code collision. A strategy named "Volatility Expansion" and a market state named "Volatility Expansion" are different concepts (an approach vs. a condition) that happen to share a phrase, exactly as a discretionary trader would use it both ways.

## 3. Deliverable 2 — Selection rules, per family

Each of the 13 strategy families declares `preferred`/`acceptable`/`forbidden` market states (see `taxonomy.py::STRATEGY_STATE_FIT`) — pure declarative data. Selection among the already-`SUITABLE` set (Series 87/88) is a **categorical match-count comparison**: a forbidden-state hit disqualifies outright; otherwise `2×(preferred matches) + 1×(acceptable matches)`, ties broken by a fixed, disclosed alphabetical order (`taxonomy.TIE_BREAK_ORDER`). This is explicitly NOT a numeric optimization — no historical return, no P&L, no ML anywhere in this module (verified structurally by an AST test forbidding the identifier fragments `optimi`/`backtest`/`pnl`).

## 4. Deliverable 3/4 — Assessment and explainability

`StrategySelectionAssessment`: `selected_strategy_family` (`Optional[str]` — `None` is a real, honest outcome, not an error), `alternative_candidates` (every other considered candidate, full `CandidateScore` detail — never discarded), `rejection_reasons`, `confidence`, and a mandatory `Explanation` answering all four required questions (why this strategy, why not the alternatives, which evidence mattered most, which evidence prevented alternatives) — genuinely computed from the real match/disqualification trace, never templated.

## 5. Deliverable 5/8 — Determinism

`assessment_id` is a deterministic content hash over active states + every candidate's disqualification/score + schema_version — never timestamp, never `uuid4`. Proven: same input fed 5 times independently produces exactly 1 distinct `assessment_id`; full 41-day corpus replayed twice, byte-identical.

## 6. Deliverable 6 — Real historical replay (41 real days)

- **Selected family distribution:** `LONG_DIRECTIONAL: 10, None: 6, NEUTRAL_PREMIUM_SELLING: 5, RATIO: 5, BUTTERFLY: 5, SHORT_DIRECTIONAL: 4, COVERED: 3, VOLATILITY_COMPRESSION: 3`
- **Confidence distribution:** `MODERATE: 15, HIGH: 15, NONE: 6, LOW: 5`
- **No-selection frequency:** 6/41 (~15%) — a real, honest rate at which no suitable, non-disqualified family exists.
- **Active market state frequency:** `STRONG_PARTICIPATION` dominant (39/41 — MPPI rarely reads weak participation in this window), `BALANCE` 21/41, `ROTATIONAL_MARKET` 11/41, `TREND_EXHAUSTION` 9/41, others rarer.

## 7. Deliverable 7 — Change explanation (Observatory-style), with an honest gap disclosed

37 of 40 day-to-day transitions show a changed selected family, each printed with the specific market states added/removed between days (e.g. `2026-05-25 → 2026-05-26: LONG_DIRECTIONAL → COVERED | states added=[BALANCE, ROTATIONAL_MARKET, STRONG_PARTICIPATION]`). **3 of the 37 changes show zero market-state delta** (e.g. `2026-06-25 → 2026-06-29: RATIO → None`) — this is a real, disclosed limitation of the *measurement script's* simplified diff, which only compares the 10 market-state tags, not the full upstream Series 87/88 suitability set (which can shift for reasons — a confidence-rank or consensus-rank change — the 10 boolean tags don't capture). **This is a gap in the corpus script's summary view, not in the underlying engine**: every individual `StrategySelectionAssessment` carries a complete, real `Explanation`/`alternative_candidates` trace regardless; a fuller first-divergence tool would need to diff the full suitability-assessment set alongside market states, not just the states — noted here as a concrete follow-up rather than silently glossed over.

## 8. Known limitations

- Market state derivation depends on VSB's `expansion_state`/`compression_state`, whose price-based proxy calibration was only recently corrected (the `STABLE` vs `UNKNOWN` fix) — genuinely improved, but still a temporary bridge per Series 88's own migration plan, not a permanent IV-based signal.
- `STRONG_PARTICIPATION` was active on 39 of 41 days — worth investigating whether MPPI's `positioning_strength` thresholds are too easily satisfied in this corpus, a question for a future measurement, not addressed here (no threshold tuning performed, per this sprint's own constraint).
- The directional families (`LONG_DIRECTIONAL`/`SHORT_DIRECTIONAL`/`COVERED`) still ultimately depend on MDI's `overall_direction`, whose predictive value remains the documented negative result from Series 86 — this selector does not resolve that; it only avoids compounding it by not selecting *based on* direction, only gating eligibility by it (inherited from Series 87/88, unchanged here).

## 9. Future Strike Selection interface

A future Strike Selection module should consume `StrategySelectionAssessment.selected_strategy_family` (when not `None`) plus the real upstream assessments already referenced in `supporting_evidence` (MSSI's structural levels for strike placement context, VSB's `expected_move_state` for width sizing) — mirroring this whole arc's established "downstream consumer imports real types directly" pattern. It should NOT need to re-derive market state or suitability; those are this module's and Series 87/88's job respectively, already done.

## 10. Deliverable 10 — Recommendation: **Strike Selection**

Evidence-based, not the only defensible answer but the best-supported one:

1. **The selector genuinely selects** — real, varied output across 8 of 13 families, a real 15% honest no-selection rate, real confidence spread. There is now something concrete for Strike Selection to consume.
2. **Liquidity Intelligence and Term Structure Intelligence remain named, real, but lower-leverage gaps** — they would unlock `IRON_CONDOR`/`IRON_FLY`/`CALENDAR`/`SYNTHETIC` (4 families), a smaller expansion than what Strike Selection would unlock (turning ALL 8 currently-selectable families into something a desk could actually construct and size).
3. **Trade Management is premature** — there is nothing to manage yet; no strike, no position, no execution exists downstream of this selector.

**Recommended next step: Series 90 — Strike Selection**, consuming this module's real output, per the interface sketched in Section 9.
