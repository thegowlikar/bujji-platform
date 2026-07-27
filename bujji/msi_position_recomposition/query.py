"""bujji.msi_position_recomposition.query — Series 110. Read-only lookups."""
from __future__ import annotations

from . import taxonomy


def execution_delta_summary(assessment) -> str:
    """Human-readable 'Close: ... / Open: ...' rendering (Deliverable 6's
    own worked example format) -- for dashboards/reports only, never a
    substitute for the real `ExecutionDelta` object."""
    if not assessment.possible or assessment.execution_delta is None:
        return f"{taxonomy.RECOMPOSITION_NOT_POSSIBLE}: {assessment.reason_not_possible}"
    ed = assessment.execution_delta
    close_str = ", ".join(f"{l.strike} {l.option_type}" for l in ed.close_legs) or "none"
    open_str = ", ".join(f"{l.strike} {l.option_type}" for l in ed.open_legs) or "none"
    keep_str = ", ".join(f"{l.strike} {l.option_type}" for l in ed.kept_legs) or "none"
    return f"Close: {close_str} | Open: {open_str} | Keep: {keep_str}"


def is_full_rebuild(assessment) -> bool:
    return assessment.full_rebuild
