"""bujji.live_shadow_operator — Sprint 107: Live Shadow Operator (Production).

Orchestration only. Every trading decision is made by FROZEN modules
from Series 73-106 (SessionDriver, run_full_cadence, and everything
they call). This package adds no MSI logic, no strategy logic, no
threshold, no new decision behaviour of any kind — see
docs/LIVE_SHADOW_OPERATOR.md Section 0 for the full audit this package
was built against.
"""
from .safety import SHADOW_MODE, assert_shadow_safe, render_shadow_banner
from .operator import LiveShadowOperator, DailyOutcome

__all__ = [
    "SHADOW_MODE", "assert_shadow_safe", "render_shadow_banner",
    "LiveShadowOperator", "DailyOutcome",
]
