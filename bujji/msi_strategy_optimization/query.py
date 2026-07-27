"""bujji.msi_strategy_optimization.query — Series 108. Read-only lookups."""
from __future__ import annotations

from typing import Tuple

from bujji.msi_trade_construction import taxonomy as tc_taxonomy

from . import taxonomy


def objectives_for(strategy_family: str) -> Tuple[str, ...]:
    return taxonomy.FAMILY_OBJECTIVES.get(strategy_family, ())


def is_defined_risk(strategy_family: str) -> bool:
    """Reused BY IDENTITY from Series 90's own frozen taxonomy -- never
    a second, possibly-diverging risk-shape classification."""
    return strategy_family in tc_taxonomy.DEFINED_RISK_FAMILIES


def conversion_rules_for(strategy_family: str) -> Tuple[dict, ...]:
    return tuple(r for r in taxonomy.CONVERSION_RULES if r["from_family"] == strategy_family)


def roll_summary(roll_assessment) -> str:
    """A one-line, human-readable summary of the five independent roll
    signals -- for dashboards/reports, never a substitute for the
    Explanation object itself."""
    flags = []
    if roll_assessment.exit:
        flags.append("EXIT")
    if roll_assessment.roll_whole_strategy:
        flags.append("ROLL_WHOLE_STRATEGY")
    if roll_assessment.roll_expiry:
        flags.append("ROLL_EXPIRY")
    if roll_assessment.roll_strike:
        flags.append("ROLL_STRIKE")
    if roll_assessment.hold:
        flags.append("HOLD")
    return f"recommended={roll_assessment.recommended_action} signals={flags or ['NONE']}"
