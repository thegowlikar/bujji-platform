"""Behaviour Brain — Market Intelligence Core.

Answers, once there is enough real trade history to answer it honestly:
is BUJJI's own trading behaviour showing a real pattern -- an exit rule
that's quietly underperforming, a losing streak worth a human's
attention, a day-of-week effect -- or is any apparent pattern just noise
from too few trades?

DATA REALITY, CHECKED BEFORE WRITING A LINE OF THIS BRAIN'S LOGIC
(2026-07-20): the real trade journal (`bujji/journal/journal.py`, backed
by `data/bujji.db`) currently has ZERO completed live trades. Even
counting this session's real-data backtest runs, there are only ~5
correlated real trading days on file (2026-07-13 to 2026-07-17, the same
5 days re-run several times) -- nowhere near independent or numerous
enough to detect a genuine behavioural pattern rather than noise.

THIS BRAIN IS THEREFORE BUILT INERT BY DESIGN. `MIN_TRADES_REQUIRED`
gates every output behind a hard trade-count floor, chosen as a
documented first pass (30 -- a commonly used rule-of-thumb minimum
sample size before basic rate/average statistics stop being dominated by
noise, not a rigorous statistical derivation specific to this strategy).
Below that floor, this brain reports UNKNOWN across the board -- not a
partial reading, not a cautious guess. Reporting a "pattern" from 5
correlated data points would be exactly the kind of fabrication this
codebase's discipline exists to prevent.

METHOD (once MIN_TRADES_REQUIRED is met)
-----------------------------------------
- win_rate, avg_pnl -- plain arithmetic over the real trade PnLs given.
- current_streak -- signed count of the most recent consecutive
  same-direction (win/loss) outcomes, from the most recent trade
  backwards. Positive = winning streak, negative = losing streak.
- streak_signal -- WINNING_STREAK / LOSING_STREAK if |current_streak|
  is at or beyond `STREAK_ALERT_THRESHOLD` (documented first pass, 3),
  else NORMAL.
- exit_reason_breakdown -- count/win-rate/avg-pnl grouped by the trade's
  real recorded exit reason (e.g. "vwap_breach", "mtm_stop",
  "hard_exit_15_05") -- surfaces whether a specific exit rule is
  quietly costing more than it's worth, once there's enough data per
  reason to say so (this brain does not gate per-reason on its own
  sub-count -- that refinement needs its own calibration pass once real
  data exists to calibrate against).
"""
from __future__ import annotations

from typing import Optional

from ..core.clock import now_ist
from .models import BehaviourReading, DataQuality, StreakSignal

MIN_TRADES_REQUIRED = 30   # First-pass floor -- see module docstring.
STREAK_ALERT_THRESHOLD = 3  # |current_streak| >= this -> WINNING_STREAK / LOSING_STREAK.


class BehaviourBrain:
    """Stateless: call `analyze(...)` with the real trade outcomes on
    file, oldest first. Never mutates anything, never talks to a broker,
    never decides whether to trade."""

    def analyze(
        self,
        trades: list[tuple[float, str]],  # (pnl, exit_reason), oldest first.
    ) -> BehaviourReading:
        as_of = now_ist()
        total = len(trades)

        if total < MIN_TRADES_REQUIRED:
            return BehaviourReading(
                total_trades=total, win_rate=None, avg_pnl=None,
                current_streak=None, streak_signal=StreakSignal.UNKNOWN,
                confidence=0.0, data_quality=DataQuality.INSUFFICIENT,
                evidence={"min_trades_required": MIN_TRADES_REQUIRED},
                reason=(f"insufficient_history: {total} real trade(s) on file, "
                       f"need >= {MIN_TRADES_REQUIRED} before any behavioural "
                       f"reading is statistically meaningful"),
                as_of=as_of,
            )

        pnls = [pnl for pnl, _ in trades]
        wins = sum(1 for pnl in pnls if pnl > 0)
        win_rate = wins / total * 100.0
        avg_pnl = sum(pnls) / total

        current_streak = self._current_streak(pnls)
        streak_signal = self._classify_streak(current_streak)

        breakdown: dict[str, dict[str, float]] = {}
        for reason_key in {r for _, r in trades}:
            reason_pnls = [pnl for pnl, r in trades if r == reason_key]
            reason_wins = sum(1 for pnl in reason_pnls if pnl > 0)
            breakdown[reason_key] = {
                "count": len(reason_pnls),
                "win_rate": round(reason_wins / len(reason_pnls) * 100.0, 2),
                "avg_pnl": round(sum(reason_pnls) / len(reason_pnls), 2),
            }

        return BehaviourReading(
            total_trades=total, win_rate=round(win_rate, 2), avg_pnl=round(avg_pnl, 2),
            current_streak=current_streak, streak_signal=streak_signal,
            exit_reason_breakdown=breakdown,
            confidence=1.0, data_quality=DataQuality.SUFFICIENT,
            evidence={"min_trades_required": MIN_TRADES_REQUIRED},
            reason=f"{total} real trades on file (>= {MIN_TRADES_REQUIRED} required)",
            as_of=as_of,
        )

    @staticmethod
    def _current_streak(pnls: list[float]) -> int:
        if not pnls:
            return 0
        last_was_win = pnls[-1] > 0
        streak = 0
        for pnl in reversed(pnls):
            if (pnl > 0) != last_was_win:
                break
            streak += 1
        return streak if last_was_win else -streak

    @staticmethod
    def _classify_streak(current_streak: int) -> StreakSignal:
        if current_streak >= STREAK_ALERT_THRESHOLD:
            return StreakSignal.WINNING_STREAK
        if current_streak <= -STREAK_ALERT_THRESHOLD:
            return StreakSignal.LOSING_STREAK
        return StreakSignal.NORMAL
