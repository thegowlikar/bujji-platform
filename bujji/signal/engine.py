"""MODULE 1 — Signal Engine (Straddle variant).

Watches for the 09:20 candle and emits a single ENTER_STRADDLE signal for
the day.  No opening-range logic, no spot-VWAP logic — the trade decision is
purely time-based.  The exit decision (premium VWAP breach) lives entirely in
the Trade Manager, which has access to live CE+PE LTP data.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.config import AppConfig
from ..core.decision_trace import DecisionTrace
from ..core.enums import CheckResult, SignalType
from ..core.logging_setup import log_event
from ..core.models import Candle, CheckOutcome, OpeningRange, Signal
from .indicators import VwapTracker
from .vwap_audit import VwapQuality


class SignalEngine:
    """Emits one ENTER_STRADDLE per day at 09:20; no-op otherwise."""

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._signalled = False

    # ------------------------------------------------------------------ #
    # State accessors (kept for interface compatibility with Orchestrator)
    # ------------------------------------------------------------------ #
    @property
    def vwap(self) -> float:
        """No spot VWAP in straddle strategy."""
        return 0.0

    @property
    def vwap_is_real(self) -> bool:
        return False

    def vwap_quality(self) -> VwapQuality:
        return VwapQuality.from_tracker(VwapTracker())

    @property
    def opening_range(self) -> Optional[OpeningRange]:
        return None

    @property
    def orb_ready(self) -> bool:
        # Always "ready" — no opening range needed for straddle entry.
        return True

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #
    def on_candle(self, candle: Candle) -> Signal:
        """Return ENTER_STRADDLE at 09:20 once; NO_TRADE on every other candle."""
        t = candle.timestamp.time()

        if self._signalled:
            return self._no_trade(candle, "already_signalled")

        if t < self._cfg.timing.trading_start:
            return self._no_trade(candle, "before_trading_start")

        if t >= self._cfg.timing.hard_exit:
            return self._no_trade(candle, "outside_trading_window")

        # First candle at or after trading_start (09:20) — enter the straddle.
        self._signalled = True
        log_event(self._log, "straddle_signal_generated",
                  ts=candle.timestamp.isoformat(), spot=candle.close)
        return Signal(
            type=SignalType.ENTER_STRADDLE,
            timestamp=candle.timestamp,
            spot=candle.close,
            reason="atm_straddle_entry_09:20",
        )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _no_trade(self, candle: Candle, reason: str) -> Signal:
        return Signal(
            type=SignalType.NO_TRADE,
            timestamp=candle.timestamp,
            spot=candle.close,
            reason=reason,
        )
