"""Execution Report -- BUJJI Options OS v3, Gate F.2 Part 5.

Every simulated execution produces exactly one of these, immutable.
Journal-compatible by construction (a plain frozen dataclass of
primitives + ChargesBreakdown) -- rather than forcing this into
TradeJournal/TradeRecord's shape (a genuinely different, ORB-strategy-
specific generation: orb_high/orb_low/atm_strike fields, confirmed by
reading it directly -- a mismatched fit that would require inventing
translation logic this gate is not asked to build), ExecutionReport is
recorded through Gate F.1's own ShadowTradeTimeline, the natural,
already-existing per-event log for the Trading Brain runtime path.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from .charges import ChargesBreakdown
from .order_lifecycle import ExecutionStage


@dataclass(frozen=True)
class ExecutionReport:
    order_id: str
    client_order_id: str
    timestamp: datetime
    requested_qty: int
    filled_qty: int
    average_fill_price: Optional[float]
    slippage: float
    charges: Optional[ChargesBreakdown]
    latency_ms: float
    status: str                      # bujji.core.enums.OrderStatus value, unchanged interface
    stage: ExecutionStage
    rejection_reason: Optional[str]
