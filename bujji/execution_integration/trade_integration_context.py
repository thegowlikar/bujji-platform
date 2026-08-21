"""Trade Integration Context -- Execution Integration Layer, Phase-4.

The one artifact this phase builds: an immutable correlation record
between two independently-owned facts. It represents "MSI's
assessment_id and Execution Reality's liquidity_context_id are being
associated" -- nothing more. It does NOT represent approval,
permission, a trade decision, an execution instruction, or a risk
decision -- no field exists anywhere in this module for any of those.

NO CLOCK, NO datetime.now(), DELIBERATELY: `created_at` is always
caller-supplied, exactly like TradeLiquidityContext's own discipline
(Phase-2). This module cannot generate a timestamp under any
circumstance -- there is no fallback if the caller omits one.

NO VALIDATION AGAINST MSI OR EXECUTION REALITY: `assessment_id` and
`liquidity_context_id` are accepted as plain strings, never checked
against a real TradeConstructionAssessment or TradeLiquidityContext.
This is deliberate, not an oversight -- validating against either
would require importing that system's types, which this package must
never do (see this package's own __init__.py docstring).

NO UNIQUENESS ENFORCEMENT, NO REGISTRY: multiple TradeIntegrationContext
records may reference the same assessment_id (Scenario 3 -- multiple
liquidity observations per assessment) or the same liquidity_context_id
(Scenario 4 -- one observation used by multiple candidate assessments).
This module has no storage and enforces no relationship cardinality --
it only ever constructs one immutable record at a time.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TradeIntegrationContext:
    integration_id: str
    assessment_id: str
    liquidity_context_id: str
    created_at: str


def build_trade_integration_context(
    integration_id: str, assessment_id: str, liquidity_context_id: str, created_at: str,
) -> TradeIntegrationContext:
    """Pure function: no side effects, no clock, no registry, no
    storage, no lookup. Simply constructs the immutable artifact from
    exactly the values supplied."""
    return TradeIntegrationContext(
        integration_id=integration_id, assessment_id=assessment_id,
        liquidity_context_id=liquidity_context_id, created_at=created_at,
    )
