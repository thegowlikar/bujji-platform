"""MODULE 2 — Trade Manager (Straddle / Premium-VWAP variant).

After the straddle is entered, this module reassesses on every completed
5-minute candle.  The sole exit criterion is:

    combined_premium (CE_LTP + PE_LTP) closes above its equal-weight VWAP
    for ONE completed candle.

P&L-based stops are preserved (max_mtm_loss) as a hard capital guard.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..core.config import AppConfig
from ..core.decision_trace import DecisionTrace
from ..core.enums import CheckResult, Decision
from ..core.logging_setup import log_event
from ..core.models import Candle, CheckOutcome, Position, TradeDecision
from ..signal.indicators import PremiumVwapTracker
from ..signal.vwap_audit import PremiumVwapQuality


class TradeManager:
    """Manages one open straddle position, one candle at a time."""

    def __init__(self, config: AppConfig, logger: logging.Logger) -> None:
        self._cfg = config
        self._log = logger
        self._position: Optional[Position] = None
        self._premium_vwap = PremiumVwapTracker()
        self._consecutive_above: int = 0

    @property
    def position(self) -> Optional[Position]:
        return self._position

    @property
    def premium_vwap(self) -> float:
        """Current equal-weight VWAP of combined straddle premium."""
        return self._premium_vwap.value

    def premium_vwap_quality(self) -> PremiumVwapQuality:
        """Snapshot of this strategy's actual live indicator, for the audit
        trail / dashboard — see PremiumVwapQuality's docstring for why this
        replaced the old (unused, spot-VWAP) VwapQuality reporting path."""
        return PremiumVwapQuality.from_tracker(self._premium_vwap)

    def open_position(self, position: Position, entry_candle: Candle) -> None:
        self._position = position
        # Fresh tracker each day; seed with entry premium so VWAP starts
        # at the price we sold, giving sellers immediate credit for decay.
        self._premium_vwap = PremiumVwapTracker()
        self._premium_vwap.update(position.entry_price)
        self._consecutive_above = 0
        log_event(
            self._log, "straddle_position_opened",
            ce=position.ce_contract.symbol if position.ce_contract else "",
            pe=position.pe_contract.symbol if position.pe_contract else "",
            qty=position.quantity,
            entry_combined_premium=position.entry_price,
            entry_spot=position.entry_spot,
        )

    def close_position(self) -> None:
        self._position = None
        self._consecutive_above = 0

    # ------------------------------------------------------------------ #
    # Per-candle reassessment
    # ------------------------------------------------------------------ #
    def reassess(self, candle: Candle, combined_premium: float,
                combined_volume: float = 1.0) -> TradeDecision:
        """Run the exit check for one completed candle.

         is CE volume + PE volume for this exact candle --
        the weight for the volume-weighted Premium VWAP. Defaults to 1.0
        (equal-weight contribution) for any caller that cannot obtain real
        per-candle option volume, degrading gracefully rather than crashing.
        """
        pos = self._position
        if pos is None:
            return TradeDecision(Decision.HOLD, candle.timestamp, reason="no_position")

        pos.candles_held += 1
        pos.update_excursion(combined_premium)
        mtm = pos.mtm(combined_premium)

        # Update volume-weighted premium VWAP with this candle's combined
        # premium and combined (CE+PE) volume.
        self._premium_vwap.update(combined_premium, combined_volume)
        vwap = self._premium_vwap.value

        checks = [
            self._check_hard_exit(candle),
            self._check_vwap_breach(combined_premium, vwap),
            self._check_risk(mtm),
        ]
        failed = [c for c in checks if not c.passed]

        if failed:
            decision = Decision.EXIT
            reason = "; ".join(f"{c.name}:{c.detail}" for c in failed)
        else:
            decision = Decision.HOLD
            reason = "premium_below_vwap"

        trace = DecisionTrace(
            source="trade_manager",
            timestamp=candle.timestamp,
            conclusion=decision.value,
            reason=reason,
            inputs={
                "combined_premium": round(combined_premium, 2),
                "premium_vwap": round(vwap, 2),
                "consecutive_above": self._consecutive_above,
                "mtm": round(mtm, 2),
            },
            checks=tuple(checks),
        )
        self._log.info(trace.render())
        log_event(self._log, "straddle_reassessment", **trace.to_log())
        return TradeDecision(decision, candle.timestamp, tuple(checks), reason, trace)

    # ------------------------------------------------------------------ #
    # Checks
    # ------------------------------------------------------------------ #
    def _check_hard_exit(self, candle: Candle) -> CheckOutcome:
        ok = candle.timestamp.time() < self._cfg.timing.hard_exit
        return self._outcome("time", ok, "in_window", "hard_exit_time")

    def _check_vwap_breach(self, premium: float, vwap: float) -> CheckOutcome:
        """Exit on the first candle close above VWAP.

        `_consecutive_above` is still tracked (0 or 1 in practice now,
        since a streak of 1 already triggers exit) purely for the
        DecisionTrace/dashboard's observability -- it is not what the
        exit decision is gated on anymore.
        """
        if premium > vwap:
            self._consecutive_above += 1
        else:
            self._consecutive_above = 0

        ok = self._consecutive_above < 1
        detail = (
            "below_vwap" if self._consecutive_above == 0
            else f"above_vwap_streak_{self._consecutive_above}"
        )
        return self._outcome("premium_vwap", ok, detail,
                             f"1_candle_close_above_vwap(streak={self._consecutive_above})")

    def _check_risk(self, mtm: float) -> CheckOutcome:
        cap = -abs(self._cfg.risk.max_mtm_loss)
        ok = mtm > cap
        return self._outcome("risk", ok, "within_limit", f"max_loss_hit({mtm:.0f})")

    @staticmethod
    def _outcome(name: str, ok: bool, pass_detail: str, fail_detail: str) -> CheckOutcome:
        return CheckOutcome(
            name,
            CheckResult.PASS if ok else CheckResult.FAIL,
            pass_detail if ok else fail_detail,
        )
