"""Phase 20.3 -- Attribution Framework.

THE LOOKAHEAD PROBLEM AND HOW THIS MODULE AVOIDS IT (read before
changing anything here): `classify_intraday_window` (Phase 20.1C)
derives a window's regime FROM that same window's own realized
candles -- efficiency ratio, ADX, and (for TREND_UP vs TREND_DOWN)
the window's own net price move. If a strategy were entered AND
exited using that SAME window's prices, "MIC said TREND_UP" and "the
trade made money" would be nearly tautological (a TREND_UP window is
one where price already went up), making "was MIC's call right"
unanswerable from "did the trade work" -- they'd almost always agree
by construction, not by genuine forecasting skill.

This module breaks that circularity the only way available without
redesigning MIC (an explicit boundary of this phase): MIC classifies
window N; the trade is placed on window N+1 (the very next rolling
window), using the REGIME-PERSISTENCE assumption ("the state MIC just
observed will hold for the next window"). Window N+1 is THEN
independently classified by the SAME `classify_intraday_window` call,
using ONLY N+1's own real candles -- this is the ground truth for
"was MIC's call (implicitly, about what N+1 would look like) correct."
This is disclosed as a genuine, deliberate Cycle-1 design decision, not
a hidden assumption -- see docs/PHASE_20_3_STRATEGY_RESEARCH_REPORT.md.

Definitions, made fully explicit (never left implicit):
- MIC_CORRECT: window N+1's OWN independently-classified regime is one
  of the SELECTED family's `market_conditions_required` -- i.e. the
  persistence assumption held.
- MIC_WRONG: it is not (the regime changed between N and N+1).
- STRATEGY_WORKED: the REALIZED trade (on N+1, after real execution
  costs -- Phase 20.2.1-corrected net P&L) has net_pnl > 0.
- "C) MIC wrong + strategy would have worked" (the phase's own
  phrasing): this module reports STRATEGY_WORKED for the trade
  ACTUALLY simulated under the selected family's rule, despite the
  regime having changed -- never a second, unsimulated counterfactual
  trade under a DIFFERENT family. No counterfactual strategy is ever
  invented or run; "would have worked" here means exactly "the trade
  that was actually taken, using the family MIC selected, still ended
  up profitable even though the regime moved on."
"""
from __future__ import annotations

from typing import Optional

from .models import (
    ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED, ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED,
    ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED, ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED,
)


def compute_mic_correct(realized_next_window_state: str, family) -> bool:
    """`family`: the StrategyFamily selected off window N's regime.
    Correct iff N+1's OWN real classification still falls inside the
    conditions that family requires."""
    return realized_next_window_state in family.market_conditions_required


def attribute(mic_correct: bool, outcome_worked: bool) -> str:
    """Pure 2x2. Never invents a fifth outcome -- exactly the four
    quadrants this phase's own spec names."""
    if mic_correct and outcome_worked:
        return ATTRIBUTION_A_MIC_CORRECT_STRATEGY_WORKED
    if mic_correct and not outcome_worked:
        return ATTRIBUTION_B_MIC_CORRECT_STRATEGY_FAILED
    if not mic_correct and outcome_worked:
        return ATTRIBUTION_C_MIC_WRONG_STRATEGY_WORKED
    return ATTRIBUTION_D_MIC_WRONG_STRATEGY_FAILED
