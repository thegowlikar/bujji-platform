# Market Phenomena Classifier (MPC) — Series 103 Architecture

## What this is

MPC answers exactly one question: "what objectively happened in the
market?" No trading evaluation, no strategy recommendation, no
optimisation — purely a common, causal, disclosed vocabulary that Series
100+ can share instead of each module inventing its own description of
market behaviour.

## Honesty over completeness — the central design decision

The mission's example list names 21 phenomena. **This package classifies
10 of them in v1.0.** The remaining 11 (False Breakout, Liquidity Vacuum,
Price Acceptance/Rejection, Premium Expansion/Collapse, Strike Rotation,
Delta Migration, Theta Dominance, Gamma Acceleration, Trend Reversal)
would require real options-chain Greeks time-series or tick-level
order-book data that is not currently exposed as a single, real,
already-computed Intelligence-layer field this package can read causally.
Building classifiers for these would mean either fabricating a signal or
quietly re-deriving one from raw data outside this package's scope —
both violate this project's "never fabricate" discipline (the same
discipline that produced "NOT DEMONSTRATED" verdicts throughout Sprints
115–121 rather than forced conclusions).

Instead: **every real `MarketPhenomenaReport` discloses the full
`not_classifiable` list and the real reason, every time** — never a
silent gap. This is a design choice, not a shortfall to hide.

## The 10 real, classifiable phenomena and their real backing fields

| Phenomenon | Real condition |
|---|---|
| `TREND_EXPANSION` | PSI `structure_state == TRENDING` AND VSB `expansion_state == CONFIRMED` |
| `TREND_FAILURE` | PSI `structure_state == CORRECTING` |
| `RANGE_COMPRESSION` | PSI `compression_state == CONFIRMED` OR MSSI `structural_balance == RANGE_BOUND` |
| `VOLATILITY_EXPANSION` | VSB `expansion_state == CONFIRMED` |
| `VOLATILITY_COMPRESSION` | VSB `compression_state == CONFIRMED` |
| `MOMENTUM_PERSISTENCE` | PSI `structure_state == TRENDING` AND MDI `overall_direction` is a real directional lean (not UNKNOWN/MIXED/NEUTRAL) |
| `MOMENTUM_EXHAUSTION` | PSI `structure_state == CORRECTING` AND MSSI `structure_location == AT_RETEST` |
| `MEAN_REVERSION` | MSSI `structural_balance == RANGE_BOUND` AND PSI `structure_state == BALANCE` |
| `GAP_CONTINUATION` | real `open_price` vs. real `previous_close_price` gap, direction agrees with real MDI `overall_direction` |
| `GAP_FAILURE` | same real gap, direction disagrees with real MDI `overall_direction` |

Every rule is a plain, declarative boolean condition in `engine.py`'s
`_PHENOMENON_RULES` table — never fit to replay outcomes, mirroring this
project's established MSI rule-table convention (e.g.
`msi_strategy_selection_foundation.STRATEGY_DEFINITIONS`).

**Verified against the real corpus, not assumed**: a 12-day scan found
real, varied matches — `TREND_FAILURE` (3 days), `RANGE_COMPRESSION`
(3 days), `MOMENTUM_PERSISTENCE` (2 days), `VOLATILITY_EXPANSION`
(1 day) — and honest empty results on days where conditions genuinely
didn't match (e.g. 2026-07-20, the real `RANGE_PERSISTENCE`/`BUTTERFLY`-
approved day, whose real PSI/MSSI fields that day were `BALANCE`/
`EARLY`/`UNBOUNDED` — none of this package's rules fire on that
combination, and the report honestly returns zero phenomena rather than
forcing a match).

## Package structure

`bujji/msi_market_phenomena/`, same 9-file-family house convention:

| File | Responsibility |
|---|---|
| `taxonomy.py` | The 10 classifiable phenomenon types, plus the disclosed `ALL_NOT_CLASSIFIABLE_V1` list and its real reasoning string. |
| `models.py` | `MarketSnapshot` (plain, caller-supplied translation), `Phenomenon`, `MarketPhenomenaReport`. |
| `engine.py` | Pure: `validate_causal_order`, `classify_day`, the declarative `_PHENOMENON_RULES` table. |
| `translate.py` | The one, disclosed, isolated file permitted to import real Intelligence-layer **types** (never engine/decision modules) to build a `MarketSnapshot`. |
| `serialization.py`, `journal.py`, `query.py` | Standard house conventions. |

## Causality (mission requirement)

One rule: real snapshot timestamps must be strictly non-decreasing.
`validate_causal_order` checks this explicitly; `classify_day` raises
`ValueError` rather than classifying against an invalid sequence
(test-verified: `test_classify_day_raises_on_noncausal_snapshot_
sequence`). Each phenomenon's `earliest_detection_timestamp`/
`latest_confirmation_timestamp` are the real first/last snapshot
timestamps where its condition held — never inferred, never hindsight-
labeled backward past when the evidence actually existed.

## Isolation — the translate.py exception, scoped even narrower than CRE's

Same asymmetric pattern as Series 102's `replay.py`, but **narrower**:
`translate.py` imports only real Intelligence-layer **model** types
(`PriceStructureAssessment`, etc.), never an **engine** module — it reads
already-computed fields, it can never invoke a decision function
(test-verified: `test_translate_py_never_imports_a_decision_function_
only_model_types`, checking no import ends in `.engine`). Every other
file in the package stays fully Production-import-free, verified the
same way as Series 100/101/102.

## Confidence (declarative, not fit to outcomes)

`HIGH` if a phenomenon's condition held across every supplied real
snapshot, `MODERATE` if more than half, `LOW` otherwise — a plain
match-ratio rule, disclosed in `engine.py`, never tuned to make any
particular day look more confident.

## Golden replay tests / Replay compatibility

`test_golden_replay_classifies_a_real_correcting_day` — the real
2026-05-29 corpus day (manually verified to be a real, PSI
`CORRECTING` day with VSB `expansion_state == CONFIRMED`) reproducibly
classifies as real `TREND_FAILURE` + `VOLATILITY_EXPANSION`.
`test_golden_replay_production_state_unaffected_by_mpc` — same
zero-effect-on-Production guarantee already proven for MLE/EPS/CRE. Full
41-day corpus replay (`/tmp/sprint120_replay.py`) with MPC present:
**zero diffs**.

## Integration (as specified, not built further)

Per the mission: "Series 100 may consume MPC reports. Series 101 may
reference MPC reports. Series 102 may replay MPC reports. Production
must never import MPC." This sprint builds the classifier and its real
output artefact; no code in Series 99–102 was modified to consume it —
that wiring is future, separately-reviewed work.

## Verification summary

- Regression: **2949/2949 passing** (2920 pre-existing + 29 new MPC
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.
- Golden replay + determinism: verified against real corpus data.

## Explicitly not implemented (per mission's own Non-Goals)

Strategy recommendations, knowledge generation, Engineering Proposals,
Production changes, optimisation. Implementation stops here — do not
begin Series 104 without review.
