"""bujji.msi_strategy_optimization — Series 108: Strategy Optimisation &
Dynamic Position Management.

Consumes the frozen decision engine (MSI, Trade Thesis, Strategy
Selection, Trade Construction, Position Construction, Portfolio
Construction, Margin Bridge, Lifecycle, Execution Planning) read-only.
Modifies none of them. Adds strike/expiry/roll/adjustment/conversion
*optimisation guidance* only -- see docs/STRATEGY_OPTIMIZATION.md.
"""
from . import config, taxonomy, models, engine, serialization, query, runner, journal

__all__ = ["config", "taxonomy", "models", "engine", "serialization", "query", "runner", "journal"]
