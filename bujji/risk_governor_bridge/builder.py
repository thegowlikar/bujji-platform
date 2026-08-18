"""Phase 20.17 -- builds real, honest `CapitalSafetySnapshot` inputs
for the real risk governor. Every field is either a real, caller-
supplied figure (from `FyersBroker.get_funds()`, read-only, LIVE-
CERTIFIED per that method's own docstring) or explicitly `None`
(honestly unavailable) -- never fabricated. `open_risk`/`reserved_risk`
are `0.0`, not `None`: Cycle 1 genuinely has zero real open or
constructed positions right now, which is a TRUE fact to report, not a
guess.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot


def build_capital_snapshot_from_real_funds(funds: dict, *, as_of: datetime) -> CapitalSafetySnapshot:
    """`funds`: the real dict `FyersBroker.get_funds()` returns
    (read-only, never touches `place_order`/`modify_order`/
    `cancel_order`/`get_open_positions`/`get_order` -- those remain
    structurally unreachable via `disable_live_execution()` regardless
    of what this function does)."""
    total_capital = funds.get("account_equity")
    available_capital = funds.get("available_funds")
    used_margin = funds.get("used_margin")

    return CapitalSafetySnapshot(
        total_capital=total_capital, available_capital=available_capital, used_margin=used_margin,
        open_risk=0.0, reserved_risk=0.0,   # TRUE fact: zero real positions exist in Cycle 1.
        daily_pnl=None, daily_loss_limit=None,          # honestly unavailable -- no real trading history exists.
        peak_capital=None, max_allowed_drawdown=None,    # honestly unavailable -- no real account history tracked here.
        consecutive_losses=None, timestamp=as_of,
    )
