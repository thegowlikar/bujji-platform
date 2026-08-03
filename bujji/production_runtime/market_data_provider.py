"""Market Data Provider -- Bujji Options OS, Phase-1 Runner Bridge.

PURPOSE: the ONE boundary between "where the option chain and spot come
from" and everything downstream (TradingSessionGovernor, F.1's
TradingBrainRuntime, msi_trade_construction). Downstream code only ever
sees a `chain`/`spot` pair -- it never knows or cares whether that pair
came from a replayed historical bhavcopy, a live broker feed, or a
websocket tick stream. This is what makes a future live-data swap a
provider-only change, never a strategy/risk-layer change.

NO BROKER, NO WEBSOCKET DEPENDENCY HERE, DELIBERATELY: this module
imports nothing from bujji.broker.fyers/fyers_ws/hybrid, and nothing
from bujji.live_shadow_operator. A live implementation of this
interface is explicitly Category B/C future work (see the Phase-1
design audit) -- Phase-1 ships exactly one concrete provider,
`ReplayChainProvider`, which reuses the EXISTING, already-tested real
historical NSE bhavcopy ingestion path
(`bujji.options_observation.runner.ingest_all_option_series_from_bhavcopy`)
-- the same fixture pattern F.1's own test suite and the pre-Monday
dry rehearsal already used. No new chain-parsing logic is invented
here.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence


class MarketDataUnavailableError(Exception):
    """Raised when a provider cannot supply a chain/spot for the
    requested date -- fail closed, never silently substitute a stale
    or synthetic value."""


class MarketDataProvider(ABC):
    """Downstream code (the runner, TradingSessionGovernor.attempt_entry)
    depends on this abstraction only, never on a concrete source."""

    @abstractmethod
    def get_option_chain(self, as_of_date: str) -> Sequence:
        """Real option-chain rows for `as_of_date`, in the exact shape
        `TradingBrainRuntime.process_entry_cycle(chain=...)` already
        expects (whatever `construct_trade`'s own chain contract is --
        this provider never reshapes it, only sources it)."""

    @abstractmethod
    def get_spot(self) -> Optional[float]:
        """The underlying spot price associated with the most recently
        loaded chain. None if genuinely unavailable -- never a guess
        or a stale carry-forward silently presented as current."""


class ReplayChainProvider(MarketDataProvider):
    """Phase-1's only concrete MarketDataProvider. Loads ONE real,
    historical NSE F&O bhavcopy file and serves it as the day's chain
    -- the same real-data path already proven in F.1's own test suite
    and the pre-Monday dry rehearsal. This is a REPLAY of a real past
    session, not synthetic data, but it is also NOT live: a bhavcopy
    is a single EOD snapshot, so `get_option_chain`/`get_spot` return
    the identical value on every call within one runner session --
    there is no intraday tick granularity here. That limitation is
    inherent to this provider, not hidden from callers (see this
    module's own docstring and the Phase-1 report)."""

    def __init__(self, bhavcopy_path: str, underlying: str = "NIFTY") -> None:
        self._bhavcopy_path = bhavcopy_path
        self._underlying = underlying
        self._chain: Optional[Sequence] = None
        self._spot: Optional[float] = None

    def _ensure_loaded(self, as_of_date: str) -> None:
        if self._chain is not None:
            return
        from bujji.options_observation import runner as opt_runner

        try:
            with open(self._bhavcopy_path) as f:
                text = f.read()
        except OSError as exc:
            raise MarketDataUnavailableError(
                f"could not read bhavcopy file {self._bhavcopy_path!r}: {exc}"
            ) from exc

        series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(
            text, as_of_date, underlying=self._underlying,
        )
        chain = tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)
        if not chain:
            raise MarketDataUnavailableError(
                f"bhavcopy {self._bhavcopy_path!r} produced zero usable option-chain rows "
                f"for underlying={self._underlying!r}, as_of_date={as_of_date!r}"
            )
        spot = next((r.underlying_price for r in chain if r.underlying_price), None)
        if spot is None or spot <= 0:
            raise MarketDataUnavailableError(
                f"bhavcopy {self._bhavcopy_path!r} produced no usable spot price"
            )
        self._chain = chain
        self._spot = spot

    def get_option_chain(self, as_of_date: str) -> Sequence:
        self._ensure_loaded(as_of_date)
        return self._chain

    def get_spot(self) -> Optional[float]:
        return self._spot
