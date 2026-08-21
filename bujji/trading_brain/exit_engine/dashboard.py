"""Exit Engine dashboard rendering — Exit Engine v1 sprint, Part 6.
Extends the existing portfolio dashboard rendering convention
(bujji/trading_brain/portfolio_valuation/dashboard.py) with exit-rule
status; never modifies that module (per this sprint's own "reuse
existing... do not redesign" instruction)."""
from __future__ import annotations

from typing import Optional

from .config import ExitRuleConfig
from .models import ExitDecision
from ..portfolio_valuation.models import PortfolioValuation
from ..portfolio_valuation.dashboard import render_portfolio_dashboard


def render_exit_dashboard(
    valuation: PortfolioValuation, decision: ExitDecision, config: ExitRuleConfig,
    *, now_ist_time: Optional[str] = None,
) -> str:
    lines = [render_portfolio_dashboard(valuation), ""]
    lines.append("Exit Rule Status:")
    lines.append(f"  hard_time_exit:      {config.hard_time_exit or 'disabled'}"
                 + (f"  (now={now_ist_time})" if now_ist_time else ""))
    lines.append(f"  max_loss:            {config.max_loss if config.max_loss is not None else 'disabled'}")
    lines.append(f"  profit_target:       {config.profit_target if config.profit_target is not None else 'disabled'}")
    lines.append(f"  strategy_exit:       {'enabled (placeholder, never triggers)' if config.strategy_exit_enabled else 'disabled'}")
    lines.append(f"  next_time_exit:      {config.hard_time_exit or 'N/A'}")
    lines.append(f"  current_portfolio_state: {'FLAT' if not valuation.legs else f'{len(valuation.legs)} open leg(s)'}")
    lines.append("")
    lines.append(f"Exit Decision [{decision.decision_id}]:")
    lines.append(f"  should_exit:  {decision.should_exit}")
    lines.append(f"  reason:       {decision.reason}")
    lines.append(f"  confidence:   {decision.confidence}")
    if decision.should_exit:
        lines.append(f"  affected:     {', '.join(decision.affected_positions)}")
    return "\n".join(lines)


def render_exit_completion(
    *, symbol: str, exit_reason: str, exit_timestamp: str, exit_price: Optional[float], final_pnl: Optional[float],
) -> str:
    """Rendered once a real closing fill has confirmed (Part 6's own
    "When an exit occurs" requirement) -- never rendered speculatively."""
    price_str = f"{exit_price:.2f}" if exit_price is not None else "UNKNOWN"
    pnl_str = f"{final_pnl:+.2f}" if final_pnl is not None else "UNKNOWN"
    return (
        f"POSITION CLOSED: {symbol}\n"
        f"  Exit Reason:  {exit_reason}\n"
        f"  Exit Time:    {exit_timestamp}\n"
        f"  Exit Price:   {price_str}\n"
        f"  Final P&L:    {pnl_str}"
    )
