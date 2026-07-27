"""Shadow Trading Engine serialization — Series 100. Pure dict
round-trip, mirrors every prior MSI package's convention."""
from __future__ import annotations

from typing import Any, Dict

from .models import Explanation, ShadowPosition


def _leg_to_dict(leg) -> Dict[str, Any]:
    return {"role": leg.role, "option_type": leg.option_type, "strike": leg.strike, "expiry": leg.expiry,
            "side": leg.side, "ratio": leg.ratio}


def explanation_to_dict(e: Explanation) -> Dict[str, Any]:
    return {
        "assessment_id": e.assessment_id, "why_entered": list(e.why_entered),
        "why_current_state": list(e.why_current_state), "why_exit_or_still_open": list(e.why_exit_or_still_open),
        "schema_version": e.schema_version,
    }


def position_to_dict(p: ShadowPosition) -> Dict[str, Any]:
    return {
        "shadow_trade_id": p.shadow_trade_id, "decision_id": p.decision_id,
        "execution_plan_id": p.execution_plan_id, "entry_time": p.entry_time, "entry_date": p.entry_date,
        "entry_price": p.entry_price, "entry_structure": p.entry_structure,
        "entry_legs": [_leg_to_dict(l) for l in p.entry_legs], "position_close_date": p.position_close_date,
        "simulated_margin": p.simulated_margin, "lifecycle_state": p.lifecycle_state,
        "realised_pnl": p.realised_pnl, "unrealised_pnl": p.unrealised_pnl, "exit_time": p.exit_time,
        "exit_reason": p.exit_reason, "completed": p.completed, "explanation": explanation_to_dict(p.explanation),
        "provenance": p.provenance, "schema_version": p.schema_version,
    }
