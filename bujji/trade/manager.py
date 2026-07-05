"""MODULE 2 — Trade Manager.

Owns the single open position. On every completed 5-minute candle it runs a
full reassessment: Observe -> Evaluate -> Decide -> Hold/Exit. It monitors
*market health first*, P&L second, and asks the central question:

    "If I had no position right now, would I still take this trade?"

Market interpretation is delegated to the shared :class:`MarketBrain`, so the
ongoing validation reasons from the identical reading used at entry. Every
reassessment produces a :class:`DecisionTrace`, so each Hold/Exit explains
itself. This module NEVER talks to a broker.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.config import AppConfig
from ..core.decision_trace import DecisionTrace
from ..core.enums import CheckResult, Decision, Direction
from ..core.logging_setup import log_event
from ..core.models import Candle, CheckOutcome, Position, TradeDecision
from ..market.brain import MarketBrain, MarketState


class TradeManager:
    """Continuously validates the live thesis, one candle at a time."""

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._brain = MarketBrain(config.risk.breakout_body_ratio)
        self._position: Optional[Position] = None
        self._prev_candle: Optional[Candle] = None

    @property
    def position(self) -> Optional[Position]:
        return self._position

    def open_position(self, position: Position, entry_candle: Candle) -> None:
        self._position = position
        self._prev_candle = entry_candle
        log_event(
            self._log, "position_opened",
            direction=position.direction.value, symbol=position.contract.symbol,
            qty=position.quantity, entry_premium=position.entry_price,
            entry_spot=position.entry_spot,
            thesis=position.thesis.narrative if position.thesis else "",
        )

    def close_position(self) -> None:
        self._position = None
        self._prev_candle = None

    # ------------------------------------------------------------------ #
    # Per-candle reassessment
    # ------------------------------------------------------------------ #
    def reassess(
        self, candle: Candle, vwap: float, current_premium: float
    ) -> TradeDecision:
        """Run the full Observe/Evaluate/Decide cycle for one candle."""
        pos = self._position
        if pos is None:
            return TradeDecision(Decision.HOLD, candle.timestamp, reason="no_position")

        pos.candles_held += 1
        pos.update_excursion(current_premium)
        mtm = pos.mtm(current_premium)

        state = self._brain.interpret(candle, vwap, pos.orb, self._prev_candle)
        checks = self._run_checks(pos.direction, candle, state, mtm)
        failed = [c for c in checks if not c.passed]

        if failed:
            decision = Decision.EXIT
            reason = "; ".join(f"{c.name}:{c.detail}" for c in failed)
        else:
            decision = Decision.HOLD
            reason = "thesis_intact"

        trace = DecisionTrace(
            source="trade_manager",
            timestamp=candle.timestamp,
            conclusion=decision.value,
            reason=reason,
            inputs={"spot": candle.close, "vwap": round(vwap, 2),
                    "mtm": round(mtm, 2), "market": state.summary()},
            checks=tuple(checks),
        )
        self._prev_candle = candle

        self._log.info(trace.render())
        log_event(self._log, "reassessment", **trace.to_log())
        return TradeDecision(decision, candle.timestamp, tuple(checks), reason, trace)

    # ------------------------------------------------------------------ #
    # Checks — market health BEFORE P&L
    # ------------------------------------------------------------------ #
    def _run_checks(
        self, direction: Direction, candle: Candle, s: MarketState, mtm: float
    ) -> list[CheckOutcome]:
        return [
            self._check_hard_exit(candle),
            self._check_trend(direction, s),
            self._check_momentum(direction, s),
            self._check_control(direction, s),
            self._check_would_reenter(direction, s),
            self._check_risk(mtm),  # P&L check runs last, by design.
        ]

    def _check_trend(self, direction: Direction, s: MarketState) -> CheckOutcome:
        """CHECK 1 — is spot still on the correct side of VWAP?"""
        ok = s.at_or_above_vwap if direction is Direction.BULLISH else s.at_or_below_vwap
        return self._outcome("trend", ok, "on_side", "lost_vwap")

    def _check_momentum(self, direction: Direction, s: MarketState) -> CheckOutcome:
        """CHECK 2 — momentum should not collapse (need not accelerate)."""
        if direction is Direction.BULLISH:
            ok = s.higher_high or s.higher_close
        else:
            ok = s.lower_low or s.lower_close
        return self._outcome("momentum", ok, "progressing", "collapsed")

    def _check_control(self, direction: Direction, s: MarketState) -> CheckOutcome:
        """CHECK 3 — reject an aggressive candle in the opposing direction."""
        if direction is Direction.BULLISH:
            ok = not s.aggressive_bearish
        else:
            ok = not s.aggressive_bullish
        return self._outcome("control", ok, "held", "opposing_reversal")

    def _check_would_reenter(
        self, direction: Direction, s: MarketState
    ) -> CheckOutcome:
        """The core thesis question: would I still take this trade now?"""
        ok = s.above_vwap if direction is Direction.BULLISH else s.below_vwap
        return self._outcome("would_reenter", ok, "yes", "no")

    def _check_risk(self, mtm: float) -> CheckOutcome:
        """Hard risk stop — max MTM loss reached."""
        cap = -abs(self._cfg.risk.max_mtm_loss)
        ok = mtm > cap
        return self._outcome("risk", ok, "within_limit", f"max_loss_hit({mtm:.0f})")

    def _check_hard_exit(self, candle: Candle) -> CheckOutcome:
        """Time-based hard exit — no overnight positions."""
        ok = candle.timestamp.time() < self._cfg.timing.hard_exit
        return self._outcome("time", ok, "in_window", "hard_exit_time")

    @staticmethod
    def _outcome(name: str, ok: bool, pass_detail: str, fail_detail: str) -> CheckOutcome:
        return CheckOutcome(
            name,
            CheckResult.PASS if ok else CheckResult.FAIL,
            pass_detail if ok else fail_detail,
        )
