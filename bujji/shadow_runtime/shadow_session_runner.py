"""Shadow Session Runner -- Shadow Runtime, Phase-5.

Owns session lifecycle, broker connection, observation scheduling,
persistence, and final artifact creation. Contains ZERO trading logic:
no strategy evaluation, no position sizing, no execution call, no
import of any decision-shaped module anywhere in this codebase.

BROKER ACCESS, EXHAUSTIVELY: `connect()` and `get_quote()` (the latter
only indirectly, via the existing, unmodified `MarketQuoteAdapter`) are
called unconditionally every cycle. When `market_perception_enabled`
(default False) is turned on, the cycle additionally calls
`get_spot()`, `get_vix()`, `get_option_chain()`, `get_futures_quote()`,
and `get_recent_candles()` -- all read-only quote/chain/candle lookups,
via `bujji.market_perception`'s own adapters (Shadow Campaign v2 Phase
1/2). When `intelligence_cycle_enabled` (default False) is ALSO turned
on, the same already-fetched MarketSnapshot additionally flows through
the full understanding pipeline (Phase 3B-6B bridges: MarketStateBuilder,
direction_bridge, msi_adapter, synthesizer, domain_view_adapter,
msi_consensus, msi_decision_synthesis, trade_thesis_bridge,
strategy_eligibility_bridge) -- no new broker method is called beyond
what market_perception_enabled already uses. It never calls
`place_order`/`modify_order`/`cancel_order`, never queries positions,
margin, or funds, under any flag combination. It has no code path that
could reach any of those even if a caller supplied a broker object that
implements them.

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
from bujji.intelligence.context import EXECUTION_MODE_LIVE, IntelligenceContext
from bujji.intelligence.liquidity_brain import LiquidityBrain

from bujji.market_perception.intelligence_adapter import build_intelligence_snapshot, fetch_spot_candles
from bujji.market_perception.market_data_adapter import MarketDataAdapter
from bujji.market_perception.models import OptionChainConfig

from bujji.market_state.intelligence_cycle_recorder import IntelligenceCycleRecorder
from bujji.state_persistence.regime_memory import record_regime_cycle
from bujji.state_persistence.store import EventStore

from .recovery import recover_shadow_session
from .shadow_session_artifact import ShadowSessionArtifact
import dataclasses
import json

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
        market_perception_enabled: bool = False, market_perception_underlying: str = "NIFTY",
        market_snapshot_path: Optional[str] = None, intelligence_snapshot_path: Optional[str] = None,
        intelligence_cycle_enabled: bool = False, intelligence_cycle_path: Optional[str] = None,
        market_perception_chain_config: Optional[OptionChainConfig] = None,
        regime_memory_event_store_path: Optional[str] = None,
        observation_memory_recovery_enabled: bool = False,
        premium_behaviour_recovery_enabled: bool = False,
        execution_mode: str = EXECUTION_MODE_LIVE,
        health_path: Optional[str] = None,
        session_date: Optional[str] = None,
        intelligence_pipeline_enabled: bool = False,
        intelligence_pipeline_event_store_path: Optional[str] = None,
        on_cycle_evidence: Optional[Callable[[object, object], None]] = None,
    ) -> None:
        # Phase 19.10.2 -- Intelligence Loop Integration. Off by default:
        # every pre-existing caller/test keeps behaving byte-for-byte
        # identically. Requires intelligence_cycle_enabled (and therefore
        # market_perception_enabled) also True, since it reuses the SAME
        # already-fetched MarketSnapshot + candles those steps build --
        # no new broker call.
        self._intelligence_pipeline_enabled = intelligence_pipeline_enabled
        self._intelligence_pipeline_event_store = (
            EventStore(intelligence_pipeline_event_store_path) if intelligence_pipeline_event_store_path else None
        )
        # Phase 6 (Paper Intelligence Campaign) -- OFF by default (None):
        # every pre-existing caller/test keeps behaving byte-for-byte
        # identically. When supplied, called once per cycle with
        # (recorder.last_evidence, previous_intelligence_snapshot) --
        # the SAME two real objects this class already builds and holds
        # internally (`self._intelligence_cycle_recorder.last_evidence`,
        # `self._previous_intelligence_snapshot`), never a third
        # computation. Wrapped in its own try/except, matching every
        # other additive step in this file's own "never break the main
        # loop" discipline -- a caller's callback failing must never
        # crash a live session.
        self._on_cycle_evidence = on_cycle_evidence
        self._previous_intelligence_snapshot = None
        self._previous_phenomena = None
        self._previous_state_node = None
        # Phase 19.10.1 -- Replay Mode Fix. Previously hardcoded LIVE at
        # every IntelligenceContext construction site in this file
        # (Phase 19.10.0's own confirmed gap); now caller-supplied,
        # defaulting to LIVE so every pre-existing caller/test keeps
        # behaving byte-for-byte identically.
        self._execution_mode = execution_mode
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

        # Shadow Campaign v2 Phase 2 -- OFF by default; existing quote
        # logging above is completely unaffected either way.
        self._market_perception_enabled = market_perception_enabled
        self._market_perception_underlying = market_perception_underlying
        self._market_snapshot_path = market_snapshot_path
        self._intelligence_snapshot_path = intelligence_snapshot_path
        self._market_data_adapter = (
            MarketDataAdapter(
                broker, clock, underlying=market_perception_underlying,
                chain_config=market_perception_chain_config or OptionChainConfig(),
            )
            if market_perception_enabled else None
        )

        # Shadow Campaign v2 -- Continuous Intelligence Observatory --
        # OFF by default; everything above is completely unaffected
        # either way. Requires market_perception_enabled=True as well,
        # since it consumes the same MarketSnapshot that step builds.
        self._intelligence_cycle_enabled = intelligence_cycle_enabled
        self._intelligence_cycle_path = intelligence_cycle_path

        # Phase 15C -- Runtime Recovery Integration. Off by default:
        # `regime_memory_event_store_path=None` (the default) means
        # `recover_shadow_session` always returns a fresh RegimeMemoryState,
        # next_cycle_index=0, and recovery_report=None -- byte-identical
        # to pre-Phase-15C behavior. Only when a caller explicitly opts
        # in does this runner (a) hydrate a prior session's regime
        # memory instead of starting fresh, and (b) persist each
        # cycle's regime observation as it happens so a LATER restart
        # can hydrate it in turn.
        self._regime_memory_event_store_path = regime_memory_event_store_path
        self._regime_event_store = EventStore(regime_memory_event_store_path) if regime_memory_event_store_path else None
        initial_regime_state, next_cycle_index, recovery_report = recover_shadow_session(
            intelligence_cycle_path, regime_memory_event_store_path, session_id,
        )
        self._cycle_counter = next_cycle_index
        self._recovery_report = recovery_report

        # Phase 15D -- Observation Memory Recovery. Off by default. When
        # enabled, replays whatever this session's own market_snapshot_path
        # ALREADY contains (from a prior process, before this constructor
        # ever writes to it) through a fresh MarketStateBuilder -- the
        # exact same pure `.process()` the live recorder already calls
        # every cycle -- real-data validated byte-for-byte against the
        # full 174-cycle SHADOW-OBSERVATORY-2026-08-06 session. No new
        # persistence format: market_snapshots.jsonl IS the event log.
        self._observation_memory_recovery_enabled = observation_memory_recovery_enabled
        initial_observation_memory = None
        self._observation_memory_recovery_report = None
        if observation_memory_recovery_enabled and intelligence_cycle_enabled:
            from bujji.market_state_builder.recovery import hydrate_observation_memory
            initial_observation_memory, obs_report = hydrate_observation_memory(market_snapshot_path)
            self._observation_memory_recovery_report = obs_report

        # Phase 15E -- Premium Behaviour Recovery. Off by default. Same
        # architecture as Phase 15D: market_snapshots.jsonl already
        # contains everything needed (ATM CE/PE mid premiums + spot per
        # real cycle) -- no new persistence format, reuses
        # read_market_snapshots_with_diagnostics directly.
        self._premium_behaviour_recovery_enabled = premium_behaviour_recovery_enabled
        initial_premium_behaviour_state = None
        self._premium_behaviour_recovery_report = None
        if premium_behaviour_recovery_enabled and intelligence_cycle_enabled:
            from bujji.premium_behaviour.recovery import hydrate_premium_behaviour
            initial_premium_behaviour_state, pb_report = hydrate_premium_behaviour(market_snapshot_path)
            self._premium_behaviour_recovery_report = pb_report

        self._intelligence_cycle_recorder = (
            IntelligenceCycleRecorder(
                underlying=market_perception_underlying, initial_regime_state=initial_regime_state,
                initial_observation_memory=initial_observation_memory,
                initial_premium_behaviour_state=initial_premium_behaviour_state,
            )
            if intelligence_cycle_enabled else None
        )

        # Phase 19.10.1 -- Runtime Lifecycle Model + Live Health Heartbeat.
        # Off by default (health_path=None): every pre-existing caller/test
        # keeps behaving byte-for-byte identically, since neither
        # `self._lifecycle` nor any heartbeat file is ever touched unless
        # a caller opts in.
        self._health_path = health_path
        self._session_date = session_date
        self._lifecycle = None

    def _advance_lifecycle(self, to_stage: "RuntimeStage", *, reason: str = "") -> None:
        if self._health_path is None:
            return
        from .lifecycle import RuntimeLifecycle
        at = self._clock().isoformat()
        if self._lifecycle is None:
            self._lifecycle = RuntimeLifecycle.start(at=at)
        else:
            self._lifecycle = self._lifecycle.advance(to_stage, at=at, reason=reason)
        self._write_heartbeat(last_error=reason or None)

    def _write_heartbeat(self, *, last_error: Optional[str] = None) -> None:
        if self._health_path is None or self._lifecycle is None:
            return
        from .health import health_for_stage, write_health_heartbeat
        health = health_for_stage(
            self._lifecycle.current_stage, session_date=self._session_date or "",
            at=self._clock().isoformat(), last_error=last_error,
        )
        write_health_heartbeat(self._health_path, health)

    async def start(self) -> ShadowSessionArtifact:
        from .lifecycle import RuntimeStage

        start_time = self._clock().isoformat()
        self._advance_lifecycle(RuntimeStage.INITIALIZING)
        try:
            self._advance_lifecycle(RuntimeStage.WAITING_FOR_SESSION)
            self._validate_startup()
            await self._connect_with_smoke_test()
            self._advance_lifecycle(RuntimeStage.COLLECTING)
            for _ in range(self._max_cycles):
                await self._run_one_cycle()
                self._heartbeats += 1
                self._write_heartbeat()
                if self._consecutive_failures >= self._max_consecutive_failures:
                    if not await self._attempt_reconnect():
                        self._errors.append("reconnect_failed -- stopping session, never hanging")
                        break
                await self._sleep_fn(self._sleep_seconds)
            self._advance_lifecycle(RuntimeStage.PROCESSING_INTELLIGENCE)
        except ShadowRuntimeStartupError as exc:
            self._errors.append(f"startup_failed: {exc}")
        except Exception as exc:  # noqa: BLE001 -- start() must never raise; every failure is recorded, not propagated
            self._errors.append(f"unexpected_failure: {type(exc).__name__}: {exc}")
        finally:
            if self._lifecycle is not None and not self._lifecycle.is_terminal():
                self._advance_lifecycle(RuntimeStage.FINALIZING)
            artifact = self._build_artifact(start_time)
            if self._lifecycle is not None and not self._lifecycle.is_terminal():
                final_stage = RuntimeStage.FAILED if self._errors else RuntimeStage.COMPLETED
                self._advance_lifecycle(final_stage, reason="; ".join(self._errors) if self._errors else "")
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

        if self._market_perception_enabled and self._market_data_adapter is not None:
            await self._run_market_perception_step()

        if self._monitoring_pair_roles is not None and self._pairing_adapter is not None:
            try:
                pairs = self._pairing_adapter.pair(cycle_legs, self._monitoring_pair_roles)
            except PairingError as exc:
                self._errors.append(f"monitoring pair error: {exc}")
                return
            liquidity_context = IntelligenceContext(as_of_time=self._clock(), execution_mode=self._execution_mode)
            for pair in pairs:
                reading = self._liquidity_brain.analyze(
                    pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask, context=liquidity_context,
                )
                key = reading.tightness.value
                self._tightness_counts[key] = self._tightness_counts.get(key, 0) + 1

    async def _run_market_perception_step(self) -> None:
        """Phase 2: build one MarketSnapshot + one Market Intelligence
        Snapshot and append each to its own JSONL artifact. Wrapped so a
        failure here is recorded like any other cycle error and NEVER
        stops or corrupts the existing quote-observation loop above --
        market perception is strictly additive."""
        try:
            snapshot = await self._market_data_adapter.build_snapshot()
            if self._market_snapshot_path:
                _append_jsonl(self._market_snapshot_path, _snapshot_to_dict(snapshot))

            candles = await fetch_spot_candles(self._broker, self._market_perception_underlying)
            intelligence = build_intelligence_snapshot(snapshot, candles, self._clock())
            if self._intelligence_snapshot_path:
                _append_jsonl(
                    self._intelligence_snapshot_path,
                    {"timestamp": snapshot.timestamp, "intelligence": intelligence},
                )
        except Exception as exc:  # noqa: BLE001 -- additive step, must never break the main loop
            self._errors.append(f"market_perception_failed: {type(exc).__name__}: {exc}")
            return

        if self._intelligence_cycle_enabled and self._intelligence_cycle_recorder is not None:
            try:
                # Reuse the SAME candles already fetched above for
                # build_intelligence_snapshot() -- fetching twice per
                # cycle needlessly doubled FYERS request volume (a real
                # cause of rate-limiting observed 2026-08-06).
                record = await self._intelligence_cycle_recorder.record_cycle(
                    snapshot, self._broker, self._clock, candles=candles,
                )
                if self._intelligence_cycle_path:
                    _append_jsonl(self._intelligence_cycle_path, record)

                # Phase 15C: persist this cycle's real regime reading so
                # a future restart can hydrate it. `cycle_id` is derived
                # from a real, monotonically-incrementing count of
                # cycles already observed THIS session (seeded at
                # startup from however many real cycles were already in
                # intelligence_cycle_path) -- never fabricated, and
                # EventStore's own (session_id, cycle_id) dedup is the
                # actual safety net against any collision.
                if self._regime_event_store is not None:
                    record_regime_cycle(
                        self._regime_event_store, self._session_id, f"c{self._cycle_counter}",
                        record["timestamp"], self._intelligence_cycle_recorder.last_raw_regime,
                    )
                    self._cycle_counter += 1
            except Exception as exc:  # noqa: BLE001 -- additive step, must never break the main loop
                self._errors.append(f"intelligence_cycle_failed: {type(exc).__name__}: {exc}")

        # Phase 19.10.2 -- Intelligence Loop Integration. Reuses the SAME
        # `snapshot`/`candles` already fetched above this cycle -- no new
        # broker call. Wrapped exactly like every other additive step in
        # this method: a failure here is recorded and the main
        # observation loop is never broken.
        if self._intelligence_pipeline_enabled:
            await self._run_intelligence_pipeline_step(snapshot, candles)

        if self._on_cycle_evidence is not None:
            evidence = self._intelligence_cycle_recorder.last_evidence if self._intelligence_cycle_recorder else None
            try:
                self._on_cycle_evidence(evidence, self._previous_intelligence_snapshot)
            except Exception as exc:  # noqa: BLE001 -- a caller's callback must never break the main loop
                self._errors.append(f"on_cycle_evidence_failed: {type(exc).__name__}: {exc}")

    async def _run_intelligence_pipeline_step(self, snapshot, candles) -> None:
        from .cycle_artifact import build_cycle_artifact, record_cycle_artifact, record_cycle_failure
        from .intelligence_pipeline_adapter import IntelligencePipelineAdapterError, build_intelligence_heartbeat_cycle
        from .reality_translator import RealityTranslationError, translate_market_snapshot_to_reality_snapshot

        # `self._heartbeats` is already a real, monotonically-incrementing
        # count of completed cycles this session (incremented once per
        # cycle in `start()`'s own loop, independent of any other opt-in
        # flag) -- reused here rather than a second counter.
        cycle_id = f"ip{self._heartbeats}"
        as_of_time = self._clock()
        try:
            reality_snapshot = translate_market_snapshot_to_reality_snapshot(snapshot)
            context = IntelligenceContext(
                as_of_time=as_of_time, execution_mode=self._execution_mode,
                reality_snapshot_reference=reality_snapshot.fingerprint(),
            )
            cycle = build_intelligence_heartbeat_cycle(
                reality_snapshot=reality_snapshot, spot_candles=candles, context=context,
                previous_market_intelligence_snapshot=self._previous_intelligence_snapshot,
                previous_phenomena=self._previous_phenomena,
                previous_state_node=self._previous_state_node,
            )
        except (RealityTranslationError, IntelligencePipelineAdapterError) as exc:
            self._errors.append(f"intelligence_pipeline_failed: {type(exc).__name__}: {exc}")
            if self._intelligence_pipeline_event_store is not None:
                record_cycle_failure(
                    self._intelligence_pipeline_event_store, session_id=self._session_id, cycle_id=cycle_id,
                    execution_mode=self._execution_mode, as_of_time=as_of_time.isoformat(),
                    error=f"{type(exc).__name__}: {exc}", recorded_at=self._clock(),
                )
            self._write_heartbeat(last_error=f"intelligence_pipeline_failed: {exc}")
            return
        except Exception as exc:  # noqa: BLE001 -- additive step, must never break the main loop
            self._errors.append(f"intelligence_pipeline_failed: {type(exc).__name__}: {exc}")
            if self._intelligence_pipeline_event_store is not None:
                record_cycle_failure(
                    self._intelligence_pipeline_event_store, session_id=self._session_id, cycle_id=cycle_id,
                    execution_mode=self._execution_mode, as_of_time=as_of_time.isoformat(),
                    error=f"{type(exc).__name__}: {exc}", recorded_at=self._clock(),
                )
            self._write_heartbeat(last_error=f"intelligence_pipeline_failed: {exc}")
            return

        self._previous_intelligence_snapshot = cycle.market_intelligence_snapshot
        self._previous_phenomena = cycle.phenomena
        self._previous_state_node = cycle.state_node

        if self._intelligence_pipeline_event_store is not None:
            health_status = self._lifecycle.current_stage.value if self._lifecycle is not None else "RUNNING"
            artifact = build_cycle_artifact(
                cycle=cycle, cycle_id=cycle_id, session_id=self._session_id,
                execution_mode=self._execution_mode, runtime_health_status=health_status,
            )
            record_cycle_artifact(self._intelligence_pipeline_event_store, artifact, recorded_at=self._clock())

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
            recovery_report=self._recovery_report.to_dict() if self._recovery_report is not None else None,
            observation_memory_recovery_report=(
                self._observation_memory_recovery_report.to_dict()
                if self._observation_memory_recovery_report is not None else None
            ),
            premium_behaviour_recovery_report=(
                self._premium_behaviour_recovery_report.to_dict()
                if self._premium_behaviour_recovery_report is not None else None
            ),
        )


def _snapshot_to_dict(snapshot) -> dict:
    """Plain-dict serialization of a MarketSnapshot (and any nested
    frozen dataclass within it) for JSONL persistence -- no reshaping,
    every field passed through as-is via dataclasses.asdict."""
    return dataclasses.asdict(snapshot)


def _append_jsonl(path: str, record: dict) -> None:
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
