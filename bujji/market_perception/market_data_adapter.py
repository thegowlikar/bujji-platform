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
        quote_source=None,
    ) -> None:
        self._broker = broker
        self._clock = clock
        self._source = source
        self._underlying = underlying
        self._chain_config = chain_config or OptionChainConfig()
        # OPTIONAL, AND DECLARED. When supplied, this is a callable returning
        # `{symbol: Quote}` from the journal-backed tick path. REST remains
        # available and keeps working exactly as before when it is absent --
        # but a REST value can never present itself as tick evidence, because
        # every quote carries its own source and this adapter does not rewrite
        # it. Mixed-source snapshots stay legible for that reason.
        self._quote_source = quote_source

    def live_quotes(self, symbols) -> dict:
        """Typed quotes for these symbols, or {} when no tick source is wired.

        Deliberately does NOT fall back to REST here: the caller asked for
        tick evidence, and quietly answering with something else is the
        substitution this whole migration exists to make impossible.
        """
        if self._quote_source is None:
            return {}
        try:
            return dict(self._quote_source(symbols) or {})
        except Exception:  # noqa: BLE001 -- an unreadable tick source is 'none', never wrong data
            return {}


    def tick_rest_coverage(self, snapshot) -> dict:
        """How much of THIS snapshot the tick path could have priced, and
        where the two sources disagree.

        WHY THIS EXISTS RATHER THAN A MERGE. Bujji reads the market twice:
        this REST-fed snapshot feeds regime derivation and strike selection,
        and a websocket tick path feeds position pricing. Collapsing them is
        the goal, and it cannot be done honestly yet -- no tick has ever
        arrived in this configuration, and the first regime derivation of a
        session runs before the universe is subscribed, so at that moment the
        tick path has nothing to offer by construction.

        Making selection depend on an unproven feed would convert every
        session into a no-trade day, which is safe but is a decision nobody
        has the evidence to make. So this measures the question instead:
        given the chain REST actually returned, how many of those symbols did
        the tick path hold a quote for, and did the prices agree?

        DECIDES NOTHING. It returns a record. No caller may gate on it, and
        the snapshot is not modified -- exactly the shape of the existing IV
        divergence shadow record.

        NO TOLERANCE IS APPLIED. Raw differences are reported because no
        measurement exists that would justify a threshold; naming one here
        would be inventing the trading policy this record is meant to inform.
        """
        chain = getattr(snapshot, "option_chain", None)
        legs = tuple(getattr(chain, "legs", ()) or ()) if chain is not None else ()
        symbols = [leg.symbol for leg in legs if getattr(leg, "symbol", None)]

        record = {
            "chain_symbols": len(symbols),
            "tick_source_wired": self._quote_source is not None,
            "tick_covered": 0,
            "tick_uncovered": len(symbols),
            "compared": 0,
            "rest_missing_ltp": 0,
            "tick_missing_ltp": 0,
            "max_abs_difference": None,
            "differences": [],
        }
        if not symbols or self._quote_source is None:
            return record

        quotes = self.live_quotes(symbols)
        record["tick_covered"] = sum(1 for s in symbols if s in quotes)
        record["tick_uncovered"] = len(symbols) - record["tick_covered"]

        worst = None
        for leg in legs:
            quote = quotes.get(getattr(leg, "symbol", None))
            if quote is None:
                continue
            rest_ltp = getattr(leg, "ltp", None)
            tick_ltp = getattr(quote, "ltp", None)
            if rest_ltp is None:
                record["rest_missing_ltp"] += 1
                continue
            # UNAVAILABLE is not zero. A typed quote reports absence as
            # absence, and a symbol the feed never delivered must not be
            # compared as though it had delivered a price.
            if tick_ltp is None:
                record["tick_missing_ltp"] += 1
                continue
            record["compared"] += 1
            difference = float(tick_ltp) - float(rest_ltp)
            if worst is None or abs(difference) > abs(worst):
                worst = difference
            if len(record["differences"]) < 10:
                record["differences"].append({
                    "symbol": leg.symbol,
                    "rest_ltp": float(rest_ltp),
                    "tick_ltp": float(tick_ltp),
                    "difference": difference,
                    "tick_source": getattr(quote, "source", None),
                })
        record["max_abs_difference"] = abs(worst) if worst is not None else None
        return record

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
