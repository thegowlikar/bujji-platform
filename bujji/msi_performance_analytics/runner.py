"""Performance Analytics & Edge Validation runner — Series 101. Dual
batch/streaming entrypoints, proven byte-identical by test (house
convention)."""
from __future__ import annotations

from typing import List, Sequence, Tuple

from .engine import build_trade_analytics
from .journal import PerformanceAnalyticsJournal
from .models import TradeAnalytics


def build_trade_analytics_batch(requests: Sequence[dict]) -> Tuple[TradeAnalytics, ...]:
    return tuple(build_trade_analytics(**req) for req in requests)


class PerformanceAnalyticsStream:
    def __init__(self) -> None:
        self.journal = PerformanceAnalyticsJournal()
        self._trades: List[TradeAnalytics] = []

    def submit_trade(self, *, recorded_at: str, **kwargs) -> TradeAnalytics:
        t = build_trade_analytics(**kwargs)
        self._trades.append(t)
        self.journal.record_trade_analytics(t, recorded_at=recorded_at)
        return t

    def trades(self) -> Tuple[TradeAnalytics, ...]:
        return tuple(self._trades)
