"""Trade Intent Intelligence models — frozen, immutable records.

Implements Deliverable 2 (`TradeIntentAssessment`), the
`InvalidationCondition` record (Deliverable 5), and the mandatory
`Explanation` (Deliverable 6's 4 questions). Every dataclass here is
`frozen=True` and carries no logic -- construction lives in
`engine.py`, mirroring `bujji.msi_strategy_eligibility.models` exactly.

---------------------------------------------------------------------
Design decision — assessment_id: deterministic content hash, over WHAT.
---------------------------------------------------------------------
`assessment_id` is a `hashlib.md5` hash over:
  * `supporting_assessment_ids` (the input StrategyEligibilityAssessment's
    and MarketOpportunityAssessment's real assessment_ids, sorted);
  * `selected_strategy_family`;
  * the resulting intent dimension values (`market_bias`,
    `volatility_bias`, `directional_exposure`, `premium_exposure`,
    `risk_profile`);
  * `schema_version`.
NEVER over `timestamp`, NEVER `uuid4()`. Two identical (eligibility,
opportunity) pairs fed through `engine.determine_trade_intent()` twice,
at two different wall-clock times, always produce the identical
`assessment_id` -- mirrors Series 82's exact precedent, proven by
`tests/test_msi_trade_intent.py::test_assessment_id_deterministic_same_input_same_id`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# InvalidationCondition — Deliverable 5. A typed, mechanical, checkable
# record -- never free text -- of what assumption a formed intent
# depends on, and what real, observable upstream evidence would
# invalidate it BEFORE any strike/expiry/execution work happens.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class InvalidationCondition:
    protected_assumption: str    # e.g. "the eligibility read backing this intent remains at least MODERATE confidence".
    checkable_field: str          # The real upstream field this condition mechanically checks, e.g. "eligibility_confidence", "opportunity_state", "eligible_strategy_families".
    trigger_description: str      # Deterministic, mechanical description of the trigger, e.g. "eligibility_confidence drops below MODERATE".
    source_assessment_id: str      # Which real upstream assessment_id (eligibility's or opportunity's) this condition checks against.


# ---------------------------------------------------------------------------
# Explanation — mandatory. Answers Deliverable 6's 4 questions as real
# computed content, genuinely per-intent, never templated global prose.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Explanation:
    assessment_id: str
    why_this_market_expression: str            # Why this family/intent was formed -- citing opportunity_state, eligibility_confidence, and the selection priority order concretely.
    exposures_sought: Tuple[str, ...]           # Which exposures (market_bias, volatility_bias, premium_exposure, directional_exposure, risk_profile) this intent seeks, stated concretely.
    why_other_profiles_rejected: Tuple[str, ...]  # One entry per other eligible family (rejected by the disclosed placeholder priority order) and per ineligible family (rejected by Series 82 already).
    what_would_invalidate_before_execution: Tuple[str, ...]  # Mirrors invalidation_conditions' trigger_descriptions -- deterministic, mechanical statements only.
    schema_version: str


# ---------------------------------------------------------------------------
# TradeIntentAssessment — Deliverable 2, immutable. Purely descriptive
# of INTENT: exposure/bias/risk-profile/invalidation. NEVER strikes,
# expiry, sizing, or execution -- see engine.py's module docstring and
# the AST isolation test for the enforced boundary.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeIntentAssessment:
    assessment_id: str
    timestamp: str
    selected_strategy_family: str                    # The one family the disclosed placeholder selector picked from eligibility.eligible_strategy_families.
    intent_state: str                                 # One of taxonomy.ALL_INTENT_STATES -- always INTENT_STATE_FORMED for a real, non-None result of determine_trade_intent().
    market_bias: str                                  # One of taxonomy.ALL_MARKET_BIASES -- see Check 1b: currently always DELTA_NEUTRAL, honestly disclosed.
    volatility_bias: str                              # One of taxonomy.ALL_VOLATILITY_BIASES.
    directional_exposure: str                         # One of taxonomy.ALL_DIRECTIONAL_EXPOSURES.
    premium_exposure: str                             # One of taxonomy.ALL_PREMIUM_EXPOSURES.
    risk_profile: str                                 # One of taxonomy.ALL_RISK_PROFILES.
    invalidation_conditions: Tuple[InvalidationCondition, ...]   # Never empty for a formed (non-None) intent.
    supporting_assessment_ids: Tuple[str, ...]         # The real input StrategyEligibilityAssessment.assessment_id and MarketOpportunityAssessment.assessment_id -- referenced, never copied.
    explanation: Explanation
    provenance: str                                    # Free-text description of what produced this assessment.
    schema_version: str
