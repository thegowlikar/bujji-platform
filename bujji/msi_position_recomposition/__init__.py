"""bujji.msi_position_recomposition — Series 110: Roll Execution Planning
& Position Recomposition.

Composes, read-only: `msi_dynamic_management` (Series 109),
`msi_strategy_optimization` (Series 108), `msi_trade_construction`
(Series 90). Modifies none of them. Produces the exact new position (or
`RECOMPOSITION_NOT_POSSIBLE`) that a roll/rebalance/adjustment/
conversion decision implies, plus a directly execution-planning-
consumable close/open delta. See docs/POSITION_RECOMPOSITION.md.
"""
from . import config, taxonomy, models, engine, serialization, query, runner, journal

__all__ = ["config", "taxonomy", "models", "engine", "serialization", "query", "runner", "journal"]
