"""Phase 20.4 -- performance statistics. Pure, descriptive, no
tuning/optimization -- matches `msi_performance_analytics.engine`'s
own stated discipline (Series 101), whose `_drawdown_and_streaks`
(a plain function over `Sequence[float]`, no dependency on any
options-specific type) is reused UNMODIFIED here for max drawdown --
never re-derived. Win rate / profit factor / expectancy are trivial
one-liners with no existing equivalent found in this codebase (audited
directly; `msi_performance_analytics`'s own `build_trade_analytics`/
`build_edge_validation_report` are tightly coupled to `ShadowPosition`/
`DecisionRecord`, options-specific types this phase's futures-only
research never constructs -- not reused wholesale).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from bujji.msi_performance_analytics.engine import _drawdown_and_streaks

from .models import AttributedTrade


@dataclass(frozen=True)
class PerformanceStats:
    n: int
    win_rate: Optional[float]
    avg_gross_return: Optional[float]
    avg_net_return: Optional[float]
    profit_factor: Optional[float]     # sum(gains) / abs(sum(losses)) on NET P&L. None if no losses (undefined) or no trades.
    max_drawdown: Optional[float]      # over the NET P&L sequence, in real trade order.
    expectancy: Optional[float]        # mean NET P&L per trade -- same value as avg_net_return, named per this phase's own spec.
    longest_win_streak: Optional[int]
    longest_loss_streak: Optional[int]


def compute_performance_stats(trades: Sequence[AttributedTrade]) -> PerformanceStats:
    """`trades`: only trades with a real `net_pnl` (NO_TRADE / rejected/
    unfilled trades are never passed in -- honestly excluded upstream,
    never treated as a zero-P&L trade here)."""
    traded = [t for t in trades if t.net_pnl is not None]
    n = len(traded)
    if n == 0:
        return PerformanceStats(
            n=0, win_rate=None, avg_gross_return=None, avg_net_return=None,
            profit_factor=None, max_drawdown=None, expectancy=None,
            longest_win_streak=None, longest_loss_streak=None,
        )

    net_pnls = [t.net_pnl for t in traded]
    gross_pnls = [t.theoretical_gross_pnl for t in traded if t.theoretical_gross_pnl is not None]

    wins = [p for p in net_pnls if p > 0]
    losses = [p for p in net_pnls if p < 0]
    win_rate = len(wins) / n

    gains_sum = sum(wins)
    losses_sum = abs(sum(losses))
    profit_factor = (gains_sum / losses_sum) if losses_sum > 0 else None

    avg_net_return = sum(net_pnls) / n
    avg_gross_return = (sum(gross_pnls) / len(gross_pnls)) if gross_pnls else None

    max_dd, longest_win, longest_loss = _drawdown_and_streaks(net_pnls)

    return PerformanceStats(
        n=n, win_rate=win_rate, avg_gross_return=avg_gross_return, avg_net_return=avg_net_return,
        profit_factor=profit_factor, max_drawdown=max_dd, expectancy=avg_net_return,
        longest_win_streak=longest_win, longest_loss_streak=longest_loss,
    )
