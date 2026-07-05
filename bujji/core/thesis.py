"""Trade Thesis — the captured *why* behind a trade.

A :class:`TradeThesis` is created by the Signal Engine at the moment a signal
fires and travels with the position for its entire life. It records the exact
market conditions that justified entry so that:

  * the Trade Manager can compare the *current* market against the *original*
    thesis ("would I still enter now?"),
  * the journal and dashboard can explain, in plain language, why the trade was
    taken, and
  * post-hoc analysis can audit whether entries were disciplined.

It carries no behaviour that changes trading rules — it is a record.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .enums import CheckResult, Direction
from .models import CheckOutcome, OpeningRange


@dataclass(frozen=True)
class TradeThesis:
    """Immutable snapshot of the rationale for a trade."""

    direction: Direction
    created_at: datetime
    entry_spot: float
    entry_vwap: float
    orb: OpeningRange
    breakout_close: float
    breakout_body_ratio: float
    body_threshold: float
    # The named entry conditions that were TRUE at signal time.
    conditions: tuple[CheckOutcome, ...] = ()

    @property
    def narrative(self) -> str:
        """One-line human-readable statement of the thesis."""
        side = "above" if self.direction is Direction.BULLISH else "below"
        level = self.orb.high if self.direction is Direction.BULLISH else self.orb.low
        instrument = "ATM Put" if self.direction is Direction.BULLISH else "ATM Call"
        return (
            f"{self.direction.value}: spot {self.breakout_close} closed {side} "
            f"VWAP {self.entry_vwap:.2f} and {side} ORB {level:.2f} with a "
            f"{self.breakout_body_ratio:.0%} body (>= {self.body_threshold:.0%}) "
            f"-> sell {instrument}."
        )

    def as_conditions_dict(self) -> dict[str, str]:
        return {c.name: c.result.value for c in self.conditions}

    @classmethod
    def from_entry(
        cls,
        direction: Direction,
        timestamp: datetime,
        spot: float,
        vwap: float,
        orb: OpeningRange,
        body_ratio: float,
        body_threshold: float,
        conditions: tuple[CheckOutcome, ...],
    ) -> "TradeThesis":
        return cls(
            direction=direction,
            created_at=timestamp,
            entry_spot=spot,
            entry_vwap=vwap,
            orb=orb,
            breakout_close=spot,
            breakout_body_ratio=body_ratio,
            body_threshold=body_threshold,
            conditions=conditions,
        )
