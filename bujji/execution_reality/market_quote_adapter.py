"""Market Quote Adapter -- Execution Reality Layer, Phase-0.

The ONLY component permitted to call `Broker.get_quote()`. Converts
whatever the broker returns (or fails to return) into a normalized
`LegQuote`, applying exactly the five-case classification frozen in
the Phase-0 implementation plan. Makes no decision, approves or
rejects nothing, never retries indefinitely (a single bounded call
per `fetch()` invocation -- retry policy, if any, is the caller's
concern, not this adapter's), never caches a quote across contracts
(every `fetch()` call is a fresh broker call).

DATA REALITY, VERIFIED BEFORE WRITING THIS: `FyersBroker.get_quote()`'s
real return shape is `{"bid": float, "ask": float, "spread": float}`
-- confirmed by reading its implementation directly. It carries NO
quote-origination timestamp field. This means "staleness" cannot be
determined from a single live fetch (there is nothing to compare
against) -- `fetch()` always stamps a freshly-observed `LegQuote` with
`timestamp = clock()` at the moment of receipt, which is by definition
current at that instant. STALE is reachable only through
`revalidate_freshness()`, a separate, pure function that re-examines
an ALREADY-CAPTURED `LegQuote` against a later "now" -- this is an
honest design constraint, not an oversight: this codebase does not
assume broker capabilities it has not independently verified.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple

from bujji.core.enums import Side
from bujji.core.models import OptionContract

from .models import (
    DATA_QUALITY_INVALID, DATA_QUALITY_LIVE_QUOTE, DATA_QUALITY_STALE, DATA_QUALITY_UNAVAILABLE,
    RAW_STATUS_CALL_FAILED, RAW_STATUS_NONE_RETURNED, RAW_STATUS_RESPONDED, LegQuote,
)

Clock = Callable[[], datetime]

_logger = logging.getLogger("bujji-execution-reality")


class MarketQuoteAdapter:
    """Stateless apart from its broker/clock dependencies -- holds no
    quote cache, no per-symbol history, no decision state."""

    def __init__(self, broker, clock: Clock) -> None:
        self._broker = broker
        self._clock = clock

    async def fetch(self, contract: OptionContract, side: Side) -> Tuple[LegQuote, str]:
        """Exactly one broker call, exactly one LegQuote produced.
        Returns (LegQuote, raw_source_status) -- the raw status is
        returned separately because it answers a different question
        ("what did the broker actually do") than data_quality
        ("how should this be interpreted"), and callers building a
        QuoteObservationRecord need both."""
        now = self._clock()
        try:
            raw = await self._broker.get_quote(contract)
        except Exception as exc:  # noqa: BLE001 -- a broker call failure is a data-quality fact, not a crash
            _logger.warning("get_quote call failed for %s: %s", contract.symbol, exc)
            return self._unavailable(contract, side, now), RAW_STATUS_CALL_FAILED

        if raw is None:
            return self._unavailable(contract, side, now), RAW_STATUS_NONE_RETURNED

        bid = raw.get("bid")
        ask = raw.get("ask")

        if bid is None or ask is None:
            return self._unavailable(contract, side, now), RAW_STATUS_RESPONDED

        if bid <= 0 or ask <= 0 or bid > ask:
            return self._invalid(contract, side, now, bid, ask), RAW_STATUS_RESPONDED

        return self._live(contract, side, now, float(bid), float(ask)), RAW_STATUS_RESPONDED

    @staticmethod
    def revalidate_freshness(quote: LegQuote, now: datetime, max_age_seconds: float) -> LegQuote:
        """Re-examines an ALREADY-CAPTURED LegQuote against a later
        `now`. Only ever downgrades LIVE_QUOTE -> STALE when the
        elapsed time exceeds `max_age_seconds` -- never touches
        UNAVAILABLE/INVALID quotes (staleness is meaningless for data
        that was never valid to begin with), and never upgrades
        anything. `max_age_seconds` is a caller-supplied, explicit
        Phase-0 diagnostic tolerance -- NOT a calibrated trading
        threshold (no trading decision consumes this field in
        Phase-0); a real calibrated value is out of this phase's
        scope, per docs/EXECUTION_INTELLIGENCE_PHASE2_DESIGN.md's own
        calibration-governance rules."""
        if quote.data_quality != DATA_QUALITY_LIVE_QUOTE or quote.timestamp is None:
            return quote
        observed_at = datetime.fromisoformat(quote.timestamp)
        elapsed = (now - observed_at).total_seconds()
        if elapsed <= max_age_seconds:
            return quote
        return replace(quote, data_quality=DATA_QUALITY_STALE)

    def _unavailable(self, contract: OptionContract, side: Side, now: datetime) -> LegQuote:
        return LegQuote(
            symbol=contract.symbol, strike=float(contract.strike), option_type=contract.option_type.value,
            side=side, bid=None, ask=None, mid=None, absolute_spread=None, spread_percentage=None,
            data_quality=DATA_QUALITY_UNAVAILABLE, timestamp=now.isoformat(),
        )

    def _invalid(self, contract: OptionContract, side: Side, now: datetime,
                 bid: Optional[float], ask: Optional[float]) -> LegQuote:
        # Raw values are NOT preserved on LegQuote itself (its bid/ask
        # fields would misrepresent them as trustworthy) -- a caller
        # wanting the raw invalid values for forensic review reads
        # them from QuoteObservationRecord's own raw capture, not from
        # this normalized type.
        return LegQuote(
            symbol=contract.symbol, strike=float(contract.strike), option_type=contract.option_type.value,
            side=side, bid=None, ask=None, mid=None, absolute_spread=None, spread_percentage=None,
            data_quality=DATA_QUALITY_INVALID, timestamp=now.isoformat(),
        )

    def _live(self, contract: OptionContract, side: Side, now: datetime, bid: float, ask: float) -> LegQuote:
        mid = (bid + ask) / 2
        absolute_spread = ask - bid
        spread_percentage = absolute_spread / mid if mid > 0 else None
        return LegQuote(
            symbol=contract.symbol, strike=float(contract.strike), option_type=contract.option_type.value,
            side=side, bid=bid, ask=ask, mid=mid, absolute_spread=absolute_spread,
            spread_percentage=spread_percentage, data_quality=DATA_QUALITY_LIVE_QUOTE,
            timestamp=now.isoformat(),
        )
