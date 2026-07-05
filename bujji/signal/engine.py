"""MODULE 1 — Signal Engine.

Consumes completed 5-minute spot candles, maintains the Opening Range and
session VWAP, and decides whether a breakout worthy of entry has occurred.

This module NEVER places orders and NEVER references a broker. Its output is a
:class:`Signal` carrying a :class:`TradeThesis` (why the trade is justified) and
a :class:`DecisionTrace` (how the decision was reached). Market interpretation
is delegated to the shared :class:`MarketBrain`, so the entry test uses the exact
same reading of the tape that the Trade Manager later reuses.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.config import AppConfig
from ..core.decision_trace import DecisionTrace
from ..core.enums import CheckResult, Direction, SignalType
from ..core.logging_setup import log_event
from ..core.models import Candle, CheckOutcome, OpeningRange, Signal
from ..core.thesis import TradeThesis
from ..market.brain import MarketBrain, MarketState
from .indicators import OpeningRangeBuilder, VwapTracker
from .vwap_audit import VwapQuality


class SignalEngine:
    """Builds the opening range, tracks VWAP, and detects entry breakouts."""

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._vwap = VwapTracker(config.market.vwap_equal_weight_fallback)
        self._brain = MarketBrain(config.risk.breakout_body_ratio)
        self._orb_builder = OpeningRangeBuilder(
            config.timing.orb_start, config.timing.orb_end
        )
        self._orb: Optional[OpeningRange] = None
        self._signalled = False  # One signal per day guard at engine level.

    # ------------------------------------------------------------------ #
    # State accessors
    # ------------------------------------------------------------------ #
    @property
    def vwap(self) -> float:
        return self._vwap.value

    @property
    def vwap_is_real(self) -> bool:
        """True when VWAP is backed by genuine traded volume (not approximated)."""
        return self._vwap.is_real

    def vwap_quality(self) -> VwapQuality:
        """Point-in-time VWAP data-quality snapshot for the audit subsystem."""
        return VwapQuality.from_tracker(self._vwap)

    @property
    def opening_range(self) -> Optional[OpeningRange]:
        return self._orb

    @property
    def orb_ready(self) -> bool:
        return self._orb is not None

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #
    def on_candle(self, candle: Candle) -> Signal:
        """Process one completed candle and return a Signal decision."""
        self._vwap.update(candle)

        t = candle.timestamp.time()
        if t < self._cfg.timing.orb_end:
            self._orb_builder.add(candle)
            self._orb = self._orb_builder.result
            return self._no_trade(candle, "orb_building")

        if self._orb is None:
            self._orb = self._orb_builder.result
        if self._orb is None:
            return self._no_trade(candle, "orb_incomplete")

        if self._signalled:
            return self._no_trade(candle, "already_signalled")

        if not (self._cfg.timing.trading_start <= t < self._cfg.timing.trading_end):
            return self._no_trade(candle, "outside_trading_window")

        # Never trade on an unreliable VWAP. FYERS supplies real index volume in
        # historical candles; if it is somehow absent and the approximation is
        # disabled, stand down rather than risk capital on a bad reference.
        if not self._vwap.ready:
            self._log.warning(
                "vwap_not_ready: no real volume and equal-weight fallback "
                "disabled (zero_vol_candles=%d)", self._vwap.zero_volume_candles
            )
            return self._no_trade(candle, "vwap_not_ready")

        state = self._brain.interpret(candle, self._vwap.value, self._orb)
        direction, conditions = self._evaluate_entry(state)

        if direction is None:
            return self._no_trade(candle, "no_breakout", conditions, state)

        self._signalled = True
        thesis = TradeThesis.from_entry(
            direction=direction,
            timestamp=candle.timestamp,
            spot=candle.close,
            vwap=self._vwap.value,
            orb=self._orb,
            body_ratio=candle.body_ratio,
            body_threshold=self._cfg.risk.breakout_body_ratio,
            conditions=conditions,
        )
        trace = DecisionTrace(
            source="signal_engine",
            timestamp=candle.timestamp,
            conclusion="ENTER",
            reason=f"{direction.value}_breakout",
            inputs=self._trace_inputs(state),
            checks=conditions,
        )
        signal = Signal(
            type=SignalType.ENTER_LONG_PREMIUM_SELL,
            timestamp=candle.timestamp,
            direction=direction,
            spot=candle.close,
            vwap=self._vwap.value,
            orb=self._orb,
            reason=f"{direction.value}_breakout",
            thesis=thesis,
            trace=trace,
        )
        self._log.info(trace.render())
        log_event(self._log, "signal_generated", **trace.to_log(),
                  narrative=thesis.narrative)
        return signal

    # ------------------------------------------------------------------ #
    # Entry evaluation over a Market Brain reading
    # ------------------------------------------------------------------ #
    def _evaluate_entry(
        self, s: MarketState
    ) -> tuple[Optional[Direction], tuple[CheckOutcome, ...]]:
        """Return (direction | None, the named conditions evaluated)."""
        bull = (
            self._chk("above_vwap", s.above_vwap),
            self._chk("above_orb_high", s.above_orb_high),
            self._chk("strong_body", s.strong_body),
        )
        if all(c.passed for c in bull):
            return Direction.BULLISH, bull

        bear = (
            self._chk("below_vwap", s.below_vwap),
            self._chk("below_orb_low", s.below_orb_low),
            self._chk("strong_body", s.strong_body),
        )
        if all(c.passed for c in bear):
            return Direction.BEARISH, bear

        # No entry: report the bullish attempt's checks for transparency.
        return None, bull

    def evaluate_entry(
        self, candle: Candle, vwap: float, orb: OpeningRange
    ) -> Optional[Direction]:
        """Public helper — would this candle qualify for entry right now?

        Reuses the identical Market Brain reading, so the Trade Manager can ask
        the same question mid-trade.
        """
        state = self._brain.interpret(candle, vwap, orb)
        direction, _ = self._evaluate_entry(state)
        return direction

    @staticmethod
    def _chk(name: str, ok: bool) -> CheckOutcome:
        return CheckOutcome(
            name, CheckResult.PASS if ok else CheckResult.FAIL,
            "true" if ok else "false",
        )

    @staticmethod
    def _trace_inputs(s: MarketState) -> dict:
        return {
            "spot": s.spot,
            "vwap": round(s.vwap, 2),
            "body_ratio": round(s.body_ratio, 2),
        }

    def _no_trade(
        self,
        candle: Candle,
        reason: str,
        conditions: tuple[CheckOutcome, ...] = (),
        state: Optional[MarketState] = None,
    ) -> Signal:
        trace = DecisionTrace(
            source="signal_engine",
            timestamp=candle.timestamp,
            conclusion="NO_TRADE",
            reason=reason,
            inputs=self._trace_inputs(state) if state else {"spot": candle.close},
            checks=conditions,
        )
        return Signal(
            type=SignalType.NO_TRADE,
            timestamp=candle.timestamp,
            spot=candle.close,
            vwap=self._vwap.value,
            orb=self._orb,
            reason=reason,
            trace=trace,
        )
