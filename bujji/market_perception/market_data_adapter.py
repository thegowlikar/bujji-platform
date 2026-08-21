"""Market Data Adapter -- Shadow Campaign v2 Phase 1.

Converts read-only FYERS broker responses into one immutable
MarketSnapshot. This adapter NEVER makes a decision, computes a
strategy, calls Trading Brain, calls Risk Governor, or places/queries
an order/position/margin. Enforced both by review and by
tests/test_market_perception_safety.py's source-level grep check.

Broker calls made here, and ONLY these: get_spot, get_vix,
get_option_chain (via option_chain_adapter), get_futures_quote (via
futures_adapter) -- all read-only quote/chain lookups.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, List, Optional

from .futures_adapter import build_future_snapshot
from .models import (
    HEALTH_DEGRADED, HEALTH_OK, HEALTH_UNAVAILABLE, SNAPSHOT_VERSION,
    MarketSnapshot, OptionChainConfig, SpotSnapshot, VixSnapshot,
)
from .option_chain_adapter import build_option_chain_snapshot

Clock = Callable[[], datetime]


class MarketDataAdapter:
    """`clock` follows the same injection convention used throughout
    this project (never raw datetime.now()) -- tests pass a fixed
    clock, production passes a real one."""

    def __init__(
        self, broker, clock: Clock, source: str = "fyers_live", underlying: str = "NIFTY",
        chain_config: Optional[OptionChainConfig] = None,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._source = source
        self._underlying = underlying
        self._chain_config = chain_config or OptionChainConfig()

    async def build_snapshot(self) -> MarketSnapshot:
        start = self._clock()
        missing: List[str] = []

        spot_ltp = None
        try:
            spot_ltp = await self._broker.get_spot(self._underlying)
        except Exception:
            missing.append("spot")

        vix_data = None
        try:
            vix_data = await self._broker.get_vix()
        except Exception:
            vix_data = None
        if vix_data is None:
            missing.append("vix")

        futures_snapshot = await build_future_snapshot(self._broker, self._underlying, spot_ltp)
        if futures_snapshot is None:
            missing.append("futures")

        option_chain_snapshot = None
        if spot_ltp is not None:
            option_chain_snapshot = await build_option_chain_snapshot(
                self._broker, self._underlying, spot_ltp, self._chain_config,
            )
        if option_chain_snapshot is None:
            missing.append("option_chain")

        end = self._clock()
        latency_ms = (end - start).total_seconds() * 1000.0

        if spot_ltp is None:
            health = HEALTH_UNAVAILABLE
        elif missing:
            health = HEALTH_DEGRADED
        else:
            health = HEALTH_OK

        return MarketSnapshot(
            snapshot_version=SNAPSHOT_VERSION,
            timestamp=end.isoformat(),
            source=self._source,
            latency_ms=latency_ms,
            health_status=health,
            missing_fields=tuple(missing),
            spot=SpotSnapshot(symbol=self._underlying, ltp=spot_ltp),
            vix=VixSnapshot(
                value=(vix_data or {}).get("level"),
                prev_close=(vix_data or {}).get("prev_close"),
            ),
            futures=futures_snapshot,
            option_chain=option_chain_snapshot,
        )
