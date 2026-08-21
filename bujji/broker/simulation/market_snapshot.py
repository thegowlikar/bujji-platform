"""Market Snapshot -- BUJJI Options OS v3, Gate F.2 Part 1.

Pure data, always caller-supplied -- this module never fabricates a
market condition. `liquidity_score`/`volatility` are Optional:
missing means "unknown," which FillSimulator treats conservatively
(never invents a number to fill the gap).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    last_price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    volatility: Optional[float] = None            # e.g. recent realized vol, caller-supplied -- never derived here.
    available_depth: Optional[int] = None           # top-of-book quantity available, if known.
    liquidity_score: Optional[float] = None           # 0.0 (illiquid) .. 1.0 (deep), if known.
