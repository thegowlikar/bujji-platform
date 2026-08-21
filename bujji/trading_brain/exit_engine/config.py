"""Exit Engine configuration — Exit Engine v1 sprint. Purely
declarative, no runtime decision made here."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ExitRuleConfig:
    # Part 3, Rule 1: Hard Time Exit. "HH:MM" in real IST wall-clock
    # time, compared against the caller-supplied `now_ist_time` string
    # -- this module never reads the wall clock itself (same
    # determinism discipline used throughout this codebase). None
    # disables the rule entirely.
    hard_time_exit: Optional[str] = "15:15"

    # Part 3, Rule 2: Maximum Loss. A real, negative portfolio MTM
    # threshold (e.g. -5000.0) -- triggers when
    # PortfolioValuation.total_pnl <= this value. None disables the rule.
    max_loss: Optional[float] = None

    # Part 3, Rule 3: Profit Target. A real, positive portfolio MTM
    # threshold -- triggers when total_pnl >= this value. None disables.
    profit_target: Optional[float] = None

    # Part 3, Rule 4: Strategy Exit. Documented placeholder (see
    # engine.py's own docstring) -- no strategy-specific exit logic
    # exists anywhere in Trading Brain today (confirmed during the
    # prior EQ1 qualification sprint's own audit, Gap G5). This flag
    # only enables/disables the placeholder rule; it never claims real
    # strategy-aware exit logic exists.
    strategy_exit_enabled: bool = False
