"""Shadow Session Runner -- Shadow Runtime, Phase-5.

Owns session lifecycle, broker connection, observation scheduling,
persistence, and final artifact creation. Contains ZERO trading logic:
no strategy evaluation, no position sizing, no execution call, no
import of any decision-shaped module anywhere in this codebase.

BROKER ACCESS, EXHAUSTIVELY: this module calls exactly two broker
methods -- `connect()` and `get_quote()` (the latter only indirectly,
via the existing, unmodified `MarketQuoteAdapter`). It never calls
`place_order`/`modify_order`/`cancel_order`, never queries positions,
margin, or funds. It has no code path that could reach any of those
even if a caller supplied a broker object that implements them.

`start()` NEVER RAISES: every failure mode (startup validation,
connection failure, mid-session exceptions) is caught internally,
recorded into the final artifact's `errors`, and the artifact is
always produced via try/finally -- there is no partial session in the
sense of a half-built artifact, but startup failure means the
observation loop is never entered at all ("fail closed, no partial
session" from the approved design).

NO SCHEDULER IMPORT: the approved Phase-5 design's own final
dependency diagram excludes `production_runtime` entirely. The
observation loop below is a simple, explicitly bounded
`for _ in range(max_cycles)` -- never an unbounded `while True` --
with an injectable async sleep function so tests never actually wait.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from bujji.core.enums import Side
from bujji.core.models import OptionContract
from bujji.execution_reality.market_quote_adapter import MarketQuoteAdapter
from bujji.execution_reality.models import DATA_QUALITY_UNAVAILABLE
from bujji.execution_reality.quote_observation_store import QuoteObservationStore, build_quote_observation_record
from bujji.execution_reality.liquidity_pairing_adapter import LiquidityPairingAdapter, PairingError
from bujji.intelligence.liquidity_brain import LiquidityBrain

from .shadow_session_artifact import ShadowSessionArtifact

Clock = Callable[[], datetime]
SleepFn = Callable[[float], "object"]  # async callable, awaited by the runner


class ShadowRuntimeStartupError(Exception):
    """Raised internally on any startup validation/connection/smoke-test
    failure -- always caught by start() itself, never propagated to the
    caller. Exists as an internal control-flow signal, not a public API."""


class ShadowSessionRunner:
    """`watchlist`: Sequence[(OptionContract, Side)] -- the contracts
    observed each cycle. `monitoring_pair_roles`: optional
    {symbol: role_label}, e.g. {"NIFTY25000CE": "watch", "NIFTY25000PE":
    "watch"} -- if supplied, LiquidityBrain analysis runs each cycle
    over these MONITORING pairs (never real strategy legs -- no MSI
    construction is involved anywhere in this runner); if None,
    liquidity_summary on the final artifact is honestly None, not a
    fabricated empty summary."""

    def __init__(
        self, broker, watchlist: Sequence[Tuple[OptionContract, Side]], storage_path, session_id: str,
        clock: Clock, max_cycles: int, max_consecutive_failures: int = 3, sleep_seconds: float = 0.0,
        sleep_fn: Optional[SleepFn] = None, monitoring_pair_roles: Optional[Dict[str, str]] = None,
    ) -> None:
        self._broker = broker
        self._watchlist = list(watchlist)
        self._session_id = session_id
        self._clock = clock
        self._max_cycles = max_cycles
        self._max_consecutive_failures = max_consecutive_failures
        self._sleep_seconds = sleep_seconds
        if sleep_fn is not None:
            self._sleep_fn = sleep_fn
        else:
            import asyncio
            self._sleep_fn = asyncio.sleep
        self._monitoring_pair_roles = monitoring_pair_roles

        self._adapter = MarketQuoteAdapter(broker, clock)
        self._store = QuoteObservationStore(storage_path)
        self._pairing_adapter = LiquidityPairingAdapter() if monitoring_pair_roles else None
        self._liquidity_brain = LiquidityBrain() if monitoring_pair_roles else None

        self._data_quality_counts: Dict[str, int] = {}
        self._tightness_counts: Optional[Dict[str, int]] = {} if monitoring_pair_roles else None
        self._errors: List[str] = []
        self._heartbeats = 0
        self._consecutive_failures = 0

    async def start(self) -> ShadowSessionArtifact:
        start_time = self._clock().isoformat()
        try:
            self._validate_startup()
            await self._connect_with_smoke_test()
            for _ in range(self._max_cycles):
                await self._run_one_cycle()
                self._heartbeats += 1
                if self._consecutive_failures >= self._max_consecutive_failures:
                    if not await self._attempt_reconnect():
                        self._errors.append("reconnect_failed -- stopping session, never hanging")
                        break
                await self._sleep_fn(self._sleep_seconds)
        except ShadowRuntimeStartupError as exc:
            self._errors.append(f"startup_failed: {exc}")
        except Exception as exc:  # noqa: BLE001 -- start() must never raise; every failure is recorded, not propagated
            self._errors.append(f"unexpected_failure: {type(exc).__name__}: {exc}")
        finally:
            artifact = self._build_artifact(start_time)
        return artifact

    def _validate_startup(self) -> None:
        if not self._watchlist:
            raise ShadowRuntimeStartupError("watchlist is empty -- nothing to observe")
        if self._store.write_errors:
            raise ShadowRuntimeStartupError(f"storage unavailable: {self._store.write_errors}")

    async def _connect_with_smoke_test(self) -> None:
        try:
            await self._broker.connect()
        except Exception as exc:  # noqa: BLE001
            raise ShadowRuntimeStartupError(f"broker.connect() failed: {exc}") from exc

        contract, side = self._watchlist[0]
        quote, _ = await self._adapter.fetch(contract, side)
        if quote.data_quality == DATA_QUALITY_UNAVAILABLE:
            raise ShadowRuntimeStartupError(f"smoke test quote unavailable for {contract.symbol!r}")

    async def _run_one_cycle(self) -> None:
        cycle_legs = []
        cycle_had_failure = False
        for contract, side in self._watchlist:
            quote, raw_status = await self._adapter.fetch(contract, side)
            record = build_quote_observation_record(
                contract, side, exchange="NSE", broker_source=type(self._broker).__name__,
                leg_quote=quote, raw_source_status=raw_status, recorded_at=self._clock().isoformat(),
            )
            self._store.append(record)
            self._data_quality_counts[quote.data_quality] = self._data_quality_counts.get(quote.data_quality, 0) + 1
            if quote.data_quality == DATA_QUALITY_UNAVAILABLE:
                cycle_had_failure = True
            cycle_legs.append(quote)

        self._consecutive_failures = self._consecutive_failures + 1 if cycle_had_failure else 0

        if self._monitoring_pair_roles is not None and self._pairing_adapter is not None:
            try:
                pairs = self._pairing_adapter.pair(cycle_legs, self._monitoring_pair_roles)
            except PairingError as exc:
                self._errors.append(f"monitoring pair error: {exc}")
                return
            for pair in pairs:
                reading = self._liquidity_brain.analyze(pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask)
                key = reading.tightness.value
                self._tightness_counts[key] = self._tightness_counts.get(key, 0) + 1

    async def _attempt_reconnect(self) -> bool:
        try:
            await self._broker.connect()
        except Exception:  # noqa: BLE001
            return False
        self._consecutive_failures = 0
        return True

    def _build_artifact(self, start_time: str) -> ShadowSessionArtifact:
        return ShadowSessionArtifact(
            session_id=self._session_id, start_time=start_time, end_time=self._clock().isoformat(),
            observations_count=sum(self._data_quality_counts.values()),
            data_quality_summary=dict(self._data_quality_counts),
            liquidity_summary=dict(self._tightness_counts) if self._tightness_counts is not None else None,
            errors=tuple(self._errors),
            runtime_health={"heartbeats": self._heartbeats, "consecutive_failures_at_end": self._consecutive_failures},
        )
