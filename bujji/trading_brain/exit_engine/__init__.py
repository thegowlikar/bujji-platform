"""Exit Engine v1 -- consumer-only exit rule evaluation. Reads a
PortfolioValuation and the current position ledger, evaluates a fixed,
disclosed set of v1 rules (Maximum Loss, Profit Target, Hard Time Exit,
a documented Strategy Exit placeholder), and produces one ExitDecision.
Never fetches market data, never computes MTM, never talks to a
broker, never owns a position."""
from .models import ExitDecision
from .config import ExitRuleConfig
from .engine import evaluate
from .order_builder import build_closing_orders
from .dashboard import render_exit_dashboard, render_exit_completion

__all__ = [
    "ExitDecision", "ExitRuleConfig", "evaluate", "build_closing_orders",
    "render_exit_dashboard", "render_exit_completion",
]
