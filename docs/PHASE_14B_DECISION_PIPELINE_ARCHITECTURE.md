# Phase 14B — Decision Pipeline Architecture

## 1. Opportunity synthesis (`bujji.msi_decision_synthesis`, Series 77)

Fuses PSI/MSSI/MDI/MPPI/VSB into one `MarketOpportunityAssessment` (`opportunity_state`, `confidence_level`, `opportunity_quality`). Each domain's raw `state` string is looked up in `config.STATE_LEAN_MAP` to get a `(lean, preferred_opportunity_type)`. **Phase 14B-P1 finding**: this table was built domain-agnostically before real MSI brains existed and never back-integrated — real state strings never matched, so every cycle resolved to `MONITOR`. Fixed additively: real state values added as new keys (old placeholder keys untouched, `engine.py` untouched).

## 2. Eligibility (`bujji.msi_strategy_eligibility`, Series 82)

A **safety gate**, never a selector. Consumes `MarketOpportunityAssessment` + `ConsensusAssessment` only (never MDI/MSSI/VSB/liquidity directly — those are already summarized by Opportunity/Consensus). Produces `eligible_strategy_families` / `ineligible_strategy_families`, a complete partition of its own 8-family taxonomy, gated by a 3-tier coherence check (`NONE`/`REDUCED`/`NORMAL`) driven by `opportunity.confidence_level` + `consensus.consensus_level` + `consensus.evidence_sufficiency`.

## 3. Family taxonomy (three, not one)

| Taxonomy | Series | Family count | Used by |
|---|---|---|---|
| DSE | 77 | separate vocabulary (`DEFINED_RISK_DIRECTIONAL`, etc.) | `opportunity.compatible_strategy_families` |
| SEI | 82 | 8 families | Eligibility |
| SSF | 87 | 13 families | Selection, Suitability |

SEI already maps to DSE (`taxonomy.FAMILY_TO_DSE_FAMILY`). **No SEI↔SSF mapping existed before Phase 14B** — literal-string intersection was 1/13 (`CALENDAR` only).

## 4. Mapping layer (`bujji.strategy_taxonomy_bridge.mapping`) — Phase 14B P0.2

Declarative `ELIGIBILITY_TO_SELECTION: Dict[str, Tuple[str,...]]`, one-to-many, derived from each family's real risk/exposure semantics (not guessed — cross-referenced against `msi_trade_construction.taxonomy.DEFINED_RISK_FAMILIES`/`UNDEFINED_RISK_FAMILIES`, itself already a declared structural fact). Exhaustive both directions: all 8 SEI families `SUPPORTED`, all 13 SSF families `MAPPED`. A reverse index is derived once at import time. Neither source taxonomy is renamed or modified.

## 5. Strategy Selection (`msi_strategy_selection_foundation` + `msi_strategy_selector`, Series 87)

Unchanged, untouched. Consumes MDI/MSSI/consensus/VSB/liquidity directly, produces a real, ranked `StrategySelectionAssessment`.

## 6. TradeIntent (`bujji.msi_trade_intent`, Series 83) — Phase 14B P0.1

Two entrypoints now coexist:
- `determine_trade_intent(eligibility, opportunity, ...)` — **unchanged**, legacy placeholder-selector path, byte-identical behavior for every existing caller.
- `determine_trade_intent_from_selection(selected_family, selection_confidence, eligibility, opportunity, ...)` — **new**, modern path. Takes Selection's real pick as given (never re-decides it), checks via the taxonomy bridge whether Eligibility permits it, and only then forms intent (profile derived from the first permitted mapped SEI family, disclosed as bridged in the explanation). Returns `None` — never forces a pick through — when: no family selected, no eligibility/opportunity data, `eligibility_confidence == NONE`, or none of the bridged families are in `eligible_strategy_families`.

## 7. Consistency checking (`bujji.strategy_taxonomy_bridge.consistency`) — Phase 14B P0.3

Pure, read-only reporting function: `check_selection_eligibility_consistency(selected_family, eligibility_dict) -> {status, ...}`. Five possible verdicts: `NOT_APPLICABLE`, `UNKNOWN`, `ELIGIBILITY_UNRESOLVED`, `ELIGIBLE`, `NOT_ELIGIBLE`. Never gates, never mutates, never feeds back into any decision.

## 8. UNKNOWN handling — Phase 14B P0.4

Eligibility itself never returns `UNKNOWN` for a family (it always partitions all 8 into eligible/ineligible). The consistency checker's `UNKNOWN`/`ELIGIBILITY_UNRESOLVED` verdicts specifically mean "this partition cannot be trusted this cycle" (no data, or `eligibility_confidence == NONE`) — never collapsed into `NOT_ELIGIBLE`.

## 9. Legacy compatibility

`determine_trade_intent()` is untouched; `_placeholder_select_one_eligible_family` still exists and still works for any caller that hasn't adopted the modern path. Not deleted, per explicit instruction, until its own compatibility requirements are separately understood.

## 10. Future boundary — Trade Construction / PaperBroker

Per Phase 14B's explicit ordering: `ShadowTradeConstruction`/`PaperBroker` integration (Phase 14 Tasks 5-22) remains deliberately out of scope until this repaired chain is validated across more real sessions.

## Architecture diagram

```
Market Snapshot
      |
Intelligence (PSI/MSSI/MDI/MPPI/VSB)
      |
      +--> build_domain_signals() --> synthesize()  [Phase 14B-P1 fix: real state vocab]
      |         |
      |         v
      |    MarketOpportunityAssessment (opportunity_state, confidence_level)
      |         |
      |         v
      |    determine_eligibility()  --> StrategyEligibilityAssessment
      |         |                        (8-family SEI taxonomy)
      |         |
      |         v
      |    strategy_taxonomy_bridge.mapping  [Phase 14B-P0.2]
      |         |  (13-family SSF <-> 8-family SEI, exhaustive, declared)
      |         v
      +--> msi_strategy_selection_foundation + msi_strategy_selector
                |  (13-family SSF taxonomy, unchanged)
                v
          StrategySelectionAssessment
                |
                +--> strategy_taxonomy_bridge.consistency  [Phase 14B-P0.3, report-only]
                |         ELIGIBLE / NOT_ELIGIBLE / UNKNOWN / ELIGIBILITY_UNRESOLVED
                |
                v
          determine_trade_intent_from_selection()  [Phase 14B-P0.1, additive]
                |  (legacy determine_trade_intent() still exists, unchanged)
                v
          TradeIntentAssessment  or  None (honest refusal)
                |
                X  <-- Trade Construction / PaperBroker: intentionally NOT wired yet
```
