"""Strategy Expression Engine — Series 93.

Consumes ONLY `bujji.msi_trade_thesis.models.TradeThesisAssessment`
(Series 92's real output) and the declarative `FAMILY_CHARACTERISTICS`/
`THESIS_EXPRESSION_RULES` tables in config.py. Never touches Strategy
Selection Foundation or the Strategy Selector's own logic -- both
remain completely unmodified; this package only produces a new,
independent assessment that Deliverable 5 wires into the Selector
ADDITIVELY (as a filter stage, in the Selector's own package, not here).
"""
from __future__ import annotations

import hashlib
from typing import Tuple

from bujji.msi_trade_thesis.models import TradeThesisAssessment

from . import config as _config
from . import taxonomy
from .models import Explanation, StrategyExpressionAssessment


def _assessment_id(thesis_id: str, required: Tuple[str, ...], forbidden: Tuple[str, ...],
                    compatible: Tuple[str, ...], schema_version: str) -> str:
    content = "|".join([thesis_id, ",".join(required), ",".join(forbidden), ",".join(compatible), schema_version])
    return hashlib.md5(content.encode("utf-8")).hexdigest()


def derive_strategy_expression(thesis: TradeThesisAssessment, *, timestamp: str) -> StrategyExpressionAssessment:
    """Produces exactly one StrategyExpressionAssessment. No
    optimisation, no profitability scoring, no strike logic -- only a
    required/forbidden characteristic filter over SSF's real 13
    families."""
    schema_version = taxonomy.MSI_STRATEGY_EXPRESSION_VERSION

    rule = _config.THESIS_EXPRESSION_RULES.get(thesis.thesis_type, _config.THESIS_EXPRESSION_RULES["NO_TRADE"])
    (desired_direction, desired_volatility_exposure, desired_risk_profile,
     desired_time_decay, desired_convexity, required, forbidden) = rule

    compatible = []
    incompatible = []
    incompatible_reasons = []
    for family, characteristics in _config.FAMILY_CHARACTERISTICS.items():
        missing_required = [c for c in required if c not in characteristics]
        present_forbidden = [c for c in forbidden if c in characteristics]
        if missing_required or present_forbidden:
            incompatible.append(family)
            reasons = []
            if present_forbidden:
                reasons.append(f"provides {', '.join(present_forbidden)}, which contradicts the thesis")
            if missing_required:
                reasons.append(f"missing required {', '.join(missing_required)}")
            incompatible_reasons.append(f"{family} rejected: {'; '.join(reasons)}")
        else:
            compatible.append(family)

    compatible_t = tuple(sorted(compatible))
    incompatible_t = tuple(sorted(incompatible))

    if thesis.thesis_type == "NO_TRADE":
        compatible_t, incompatible_t = (), ()
        incompatible_reasons = ["no thesis was formed -- no strategy expression is defined today"]

    aid = _assessment_id(thesis.assessment_id, required, forbidden, compatible_t, schema_version)

    why_expression = [
        f"thesis={thesis.thesis_type} maps to a declared, structural exposure rule "
        f"(direction={desired_direction}, volatility={desired_volatility_exposure}, "
        f"risk={desired_risk_profile}, theta={desired_time_decay}, convexity={desired_convexity})",
    ] if thesis.thesis_type != "NO_TRADE" else ["thesis is NO_TRADE -- no expression rule applies"]

    why_compatible = tuple(f"{fam} satisfies required {', '.join(required)} without any forbidden characteristic" for fam in compatible_t) \
        if required or forbidden else tuple(f"{fam}: no required/forbidden constraint active" for fam in compatible_t)

    explanation = Explanation(
        assessment_id=aid, why_this_expression=tuple(why_expression),
        why_families_compatible=why_compatible, why_families_incompatible=tuple(incompatible_reasons),
        schema_version=schema_version,
    )

    return StrategyExpressionAssessment(
        assessment_id=aid, timestamp=timestamp, thesis=thesis, desired_direction=desired_direction,
        desired_volatility_exposure=desired_volatility_exposure, desired_risk_profile=desired_risk_profile,
        desired_time_decay=desired_time_decay, desired_convexity=desired_convexity,
        required_characteristics=required, forbidden_characteristics=forbidden,
        compatible_strategy_families=compatible_t, incompatible_strategy_families=incompatible_t,
        explanation=explanation, provenance="bujji.msi_strategy_expression.engine.derive_strategy_expression",
        schema_version=schema_version,
    )
