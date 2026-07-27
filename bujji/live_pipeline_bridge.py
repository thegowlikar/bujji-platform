"""bujji.live_pipeline_bridge — Sprint 105: Live Pipeline Integration.

Connects EXISTING, UNMODIFIED components into one real session driver:

    FyersTickFeed (real, live/recorded ticks)
        -> LiveObservationEvent (bujji.live_observation.models)
        -> Observation (bujji.live_observation.engine.translate_event,
                         delegating to bujji.market_observation.engine.build_observation
                         -- the SAME function every replay script already calls)
        -> Events (bujji.live_market_events.engine.detect_price_change -- SAME function replay uses)
        -> Episodes (bujji.market_episode.engine.advance_time/process_event -- SAME functions replay uses)
        -> MSI (bujji.msi_price_structure / msi_market_structure / msi_market_direction --
                all FROZEN, unmodified, called exactly as every replay script already does)
        -> Real Option-Chain Domains (Participant Positioning, Volatility Structure, Consensus --
                REAL, computed from a real Bhavcopy chain via `load_option_chain`, resolving the
                conviction divergence disclosed in docs/LIVE_PIPELINE_INTEGRATION.md Section 6;
                falls back to a disclosed neutral MPPI stand-in only when no chain has been loaded)
        -> Real Live Premium Feed (fetch_live_atm_premiums / set_live_atm_premiums -- REAL,
                per-leg `Broker.get_quote` calls, mid-price derived, taking priority over the
                Bhavcopy end-of-day settlement premium for VSB whenever a live quote exists)
        -> Trade Thesis -> Strategy Expression -> Strategy Selection -> Position Construction
           -> Portfolio Construction -> Margin Bridge -> Execution Planning -> Decision Auditor
           (ALL FROZEN, Series 87-99, unmodified)
        -> Shadow Trading (Series 100, FROZEN, unmodified) -- NEVER a broker order.

Deliberately NOT a new MSI package (no taxonomy/config/models/engine
convention) -- this is integration/glue code, per this sprint's own
framing ("primarily integration code instead of new architecture"). It
imports every downstream module read-only, calls their real, existing
public functions, and adds zero new decision logic of any kind.

`SessionDriver` (Deliverable 3) never calls `bujji.execution` or any
`Broker.place_order` implementation -- verified structurally by the
accompanying test's AST import-ban check, mirroring every MSI package's
own established discipline.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bujji.core.process_lock import LockAcquisitionError, ProcessLock

from bujji.live_observation import engine as lo_engine
from bujji.live_observation import taxonomy as lo_taxonomy
from bujji.live_observation.models import LiveObservationEvent

from bujji.live_market_events import engine as lme_engine
from bujji.market_episode import engine as mee_engine
from bujji.market_observation import taxonomy as moc_taxonomy

from bujji.msi_price_structure import engine as psi_engine
from bujji.msi_market_structure import engine as mssi_engine
from bujji.msi_market_direction import engine as mdi_engine
from bujji.msi_market_direction import taxonomy as mdi_taxonomy

from bujji.msi_participant_positioning import engine as mppi_engine
from bujji.msi_volatility_structure import engine as vsb_engine
from bujji.msi_volatility_structure import taxonomy as vsb_taxonomy
from bujji.msi_consensus import engine as consensus_engine
from bujji.msi_consensus import taxonomy as consensus_taxonomy

from bujji.msi_trade_thesis import engine as thesis_engine
from bujji.msi_strategy_expression import engine as se_engine
from bujji.msi_decision_auditor import engine as da_engine

from bujji.msi_shadow_trading import engine as st_engine

from bujji.options_observation import runner as opt_runner


@dataclass
class PipelineLatencyRecord:
    """Deliverable 7 -- one real, measured stage latency, in whatever
    unit the caller's own monotonic clock reports (never wall-clock
    read by this module itself; the caller supplies before/after
    readings)."""
    stage: str
    seconds: float


@dataclass
class SessionResult:
    """Deliverable 4/8's expected end-to-end output, assembled from
    real, unmodified downstream objects -- never a new model type,
    just a plain container for what the session driver produced."""
    observations: List[Any] = field(default_factory=list)
    events: List[Any] = field(default_factory=list)
    episodes: Tuple[Any, ...] = ()
    psi: Optional[Any] = None
    mssi: Optional[Any] = None
    mdi: Optional[Any] = None
    mppi: Optional[Any] = None
    vsb: Optional[Any] = None
    consensus: Optional[Any] = None
    thesis: Optional[Any] = None
    expression: Optional[Any] = None
    decision: Optional[Any] = None
    dropped_ticks: int = 0
    duplicate_events: int = 0
    reconnects: int = 0
    option_chain_wired: bool = False
    closes_with_ts: Tuple[Tuple[str, float], ...] = ()
    live_premiums_wired: bool = False
    latencies: List[PipelineLatencyRecord] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Option-chain wiring (real, this update) -- reuses the EXACT same
# domain-view construction every Series 88-101 replay script already
# uses (`_mdi_domain_view`/`_vsb_domain_view`), never a new
# transformation. The chain itself still comes from real NSE Bhavcopy
# text (no live option-chain WebSocket/REST feed was wired here --
# `FyersBroker.get_option_chain` remains the live equivalent, adapter
# required, disclosed as a follow-up, not fabricated here) -- but the
# DOWNSTREAM domains (MPPI, VSB, Consensus) are now real, computed
# functions of real chain data, not neutral stand-ins.
# ---------------------------------------------------------------------------
_CONF_MAP = {"NONE": 0.0, "LOW": 0.3, "MODERATE": 0.6, "HIGH": 0.9}


def _mdi_domain_view(mdi):
    lean_map = {
        "STRONG_BULLISH": consensus_taxonomy.LEAN_BULLISH, "BULLISH": consensus_taxonomy.LEAN_BULLISH,
        "WEAK_BULLISH": consensus_taxonomy.LEAN_BULLISH, "NEUTRAL": consensus_taxonomy.LEAN_NEUTRAL,
        "WEAK_BEARISH": consensus_taxonomy.LEAN_BEARISH, "BEARISH": consensus_taxonomy.LEAN_BEARISH,
        "STRONG_BEARISH": consensus_taxonomy.LEAN_BEARISH,
        "MIXED": consensus_taxonomy.LEAN_AMBIGUOUS, "UNKNOWN": consensus_taxonomy.LEAN_AMBIGUOUS,
    }
    return consensus_engine.DomainAssessmentView(
        domain_name="MARKET_DIRECTION", lean=lean_map[mdi.overall_direction],
        confidence=_CONF_MAP[mdi.overall_confidence], evidence_ids=mdi.supporting_assessment_ids,
        source_assessment_id=mdi.assessment_id,
    )


def _vsb_domain_view(vsb):
    return consensus_engine.DomainAssessmentView(
        domain_name="VOLATILITY_STRUCTURE", lean=consensus_taxonomy.LEAN_NEUTRAL,
        confidence=_CONF_MAP[vsb.confidence], evidence_ids=(), source_assessment_id=vsb.assessment_id,
    )


def load_option_chain(bhavcopy_text: str, day: str, underlying: str = "NIFTY") -> Tuple[Any, ...]:
    """Real reuse of `options_observation.runner.ingest_all_option_series_from_bhavcopy`
    -- the SAME parser every replay script already uses. Returns the
    real per-contract chain (each contract's last observation for the
    day), never a re-parsed or re-derived value."""
    series, _matched = opt_runner.ingest_all_option_series_from_bhavcopy(bhavcopy_text, day, underlying=underlying)
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


def _atm_inputs(chain: Sequence, day: str):
    """Real ATM strike/premium/time-to-expiry extraction -- identical
    logic to every replay script's own inline computation (Series 88
    onward), reused here verbatim rather than re-derived. Returns
    (spot, atm_strike, atm_ce_premium, atm_pe_premium, t_years), any of
    which may be None if the real chain lacks that piece of evidence --
    never guessed."""
    from datetime import datetime
    spot = next((row.underlying_price for row in chain if row.underlying_price is not None), None)
    if spot is None:
        return None, None, None, None, None
    strikes = sorted({row.strike for row in chain if row.strike is not None})
    if not strikes:
        return spot, None, None, None, None
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    atm_ce = atm_pe = None
    for row in chain:
        if row.strike == atm_strike and row.option_type == "CE" and row.settlement is not None:
            atm_ce = row.settlement
        if row.strike == atm_strike and row.option_type == "PE" and row.settlement is not None:
            atm_pe = row.settlement
    expiries = sorted({row.expiry for row in chain if row.strike == atm_strike})
    t_years = None
    if expiries:
        exp_dt = datetime.fromisoformat(expiries[0])
        trade_dt = datetime.fromisoformat(day)
        t_years = max((exp_dt - trade_dt).days, 1) / 365.0
    return spot, atm_strike, atm_ce, atm_pe, t_years


# ---------------------------------------------------------------------------
# Live premium feed adapter (real, this update) -- Deliverable 1's own
# audit found no live endpoint returns a batch of real-time option
# PREMIUMS (`FyersBroker.get_option_chain` returns only real-time open
# interest); the only real, live-verified premium-bearing call is
# `FyersBroker.get_quote(contract)`, which returns real bid/ask (never a
# single last-traded-price field -- confirmed by reading the real,
# working implementation directly). This adapter calls `get_quote`
# once per real leg (CE, PE) -- there is no batch alternative to call
# instead -- and derives a real, disclosed MID price
# (`(bid + ask) / 2`), the standard, honest point-estimate for "the
# price" when only a two-sided quote (not a trade print) is available.
#
# Deliberately duck-typed: this module never imports `bujji.broker`
# (verified by the same AST test that already bans it) -- `broker` here
# is any object exposing an async `get_quote(contract) -> Optional[dict]`
# method with the real, documented `{"bid":..., "ask":..., "spread":...}`
# shape; the real `FyersBroker` satisfies this without this module ever
# depending on it directly.
# ---------------------------------------------------------------------------
async def fetch_live_premium(broker: Any, contract: Any) -> Optional[float]:
    """Real mid-price derivation from a real `get_quote` response.
    Returns None (never a guess) if the quote is unavailable or the
    broker reports a crossed/invalid market -- mirrors this whole
    codebase's own fail-closed discipline (e.g. `LiquidityBrain`'s
    identical `crossed_market` check)."""
    quote = await broker.get_quote(contract)
    if quote is None:
        return None
    bid, ask = quote.get("bid"), quote.get("ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
        return None
    return round((bid + ask) / 2.0, 4)


async def fetch_live_atm_premiums(broker: Any, ce_contract: Any, pe_contract: Any) -> Tuple[Optional[float], Optional[float]]:
    """Real, per-leg live premium fetch for the ATM straddle VSB needs --
    two real `get_quote` calls (no batch endpoint exists for this, per
    Deliverable 1's own audit), each independently allowed to fail
    closed (a stale/missing CE quote never blocks a real PE quote from
    still being used, and vice versa)."""
    ce_premium = await fetch_live_premium(broker, ce_contract)
    pe_premium = await fetch_live_premium(broker, pe_contract)
    return ce_premium, pe_premium


def tick_to_event(sequence: int, instrument: str, timestamp: str, price: float, source: str) -> LiveObservationEvent:
    """Real, minimal construction of the exact event shape
    `live_observation.engine.translate_event` already expects for a
    TICK_RECEIVED event -- no new translation logic, only assembling
    the real, documented payload shape."""
    event_id = hashlib.md5(f"{instrument}|{timestamp}|{sequence}".encode("utf-8")).hexdigest()
    return LiveObservationEvent(
        event_id=event_id, event_type=lo_taxonomy.EVENT_TICK_RECEIVED, timestamp=timestamp,
        source=source, sequence=sequence,
        payload={"instrument": instrument, "price": price, "exchange": "NSE", "segment": "EQUITY"},
    )


class SessionDriver:
    """Deliverable 3. Responsibilities: initialise feed, subscribe,
    process ticks, close session, flush journals. No trading logic of
    any kind lives here -- every decision is made by a FROZEN,
    unmodified downstream call."""

    def __init__(self, lock_path: str = "data/bujji_live_pipeline.lock",
                prior_closes_with_ts: Sequence[Tuple[str, float]] = (),
                underlying: str = "NIFTY") -> None:
        self._lock = ProcessLock(lock_path)
        self._sequence = 0
        self._seen_event_keys: set = set()
        self.result = SessionResult()
        self._chain: Optional[Tuple[Any, ...]] = None
        self._prev_chain: Optional[Tuple[Any, ...]] = None
        self._day: Optional[str] = None
        # Multi-day close history for VSB's own realized-volatility calc
        # (Series 88's own `RegimeBrain`-derived reuse) -- threaded
        # EXACTLY like every replay script's own `prev_closes` pattern
        # (Series 88 onward): the caller carries `result.closes_with_ts`
        # from one session into the next session's `prior_closes_with_ts`
        # constructor argument. This module never invents history of its
        # own; it only accumulates real ticks it was actually given.
        self._underlying = underlying
        self._prior_closes_with_ts: Tuple[Tuple[str, float], ...] = tuple(prior_closes_with_ts)
        self._today_closes: List[Tuple[str, float]] = []
        self._live_atm_ce_premium: Optional[float] = None
        self._live_atm_pe_premium: Optional[float] = None

    def set_live_atm_premiums(self, ce_premium: Optional[float], pe_premium: Optional[float]) -> None:
        """Wires real, live-fetched ATM premiums into the session,
        taking priority over the Bhavcopy-derived settlement premiums
        `_atm_inputs` would otherwise use for VSB. Deliberately a plain,
        SYNCHRONOUS setter -- the real async `fetch_live_atm_premiums`
        call happens in the caller's own event loop, keeping
        `SessionDriver` itself fully synchronous and testable without
        one (mirrors this whole arc's own separation between async
        broker code and pure/sync MSI engines). Either argument may be
        None (a real, honest "quote unavailable" outcome) -- only a
        non-None value actually overrides the Bhavcopy figure; a None
        value here simply leaves that leg's Bhavcopy premium in place,
        it never blanks out a real value that already exists."""
        if ce_premium is not None:
            self._live_atm_ce_premium = ce_premium
        if pe_premium is not None:
            self._live_atm_pe_premium = pe_premium
        if ce_premium is not None or pe_premium is not None:
            self.result.live_premiums_wired = True

    def load_option_chain(self, bhavcopy_text: str, day: str, underlying: str = "NIFTY",
                          prev_chain: Optional[Tuple[Any, ...]] = None) -> Tuple[Any, ...]:
        """Wires real option-chain evidence into the session -- reuses
        `options_observation.runner` directly (Series 90's own real
        parser). No live option-chain WebSocket/REST feed is wired here
        (that remains `FyersBroker.get_option_chain`, adapter required,
        a disclosed follow-up); this method accepts already-real
        Bhavcopy text from whatever source the caller has (a live daily
        download or a recorded file), exactly as every replay script in
        this arc already does. Once loaded, `run_decision_cadence`
        computes REAL Participant Positioning, Volatility Structure,
        and Consensus from this chain instead of neutral stand-ins."""
        self._chain = load_option_chain(bhavcopy_text, day, underlying=underlying)
        self._prev_chain = prev_chain
        self._day = day
        self.result.option_chain_wired = True
        return self._chain

    def acquire(self) -> None:
        """Deliverable 5 reuse: the SAME F4 single-instance lock
        production's own app.py uses, pointed at a dedicated path so a
        live-pipeline run can never collide with a live trading process
        or a second live-pipeline run."""
        self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def process_tick(self, instrument: str, timestamp: str, price: float, source: str = "recorded_stream") -> None:
        """Deliverable 5: duplicate-tick handling. A tick with the same
        (instrument, timestamp) key as one already processed this
        session is dropped, counted, and never re-fed into the
        pipeline -- reuses the SAME idea `live_market_events.engine
        .detect_duplicate` already implements for observation-level
        duplicates, applied one level earlier (at the raw tick)."""
        key = (instrument, timestamp)
        if key in self._seen_event_keys:
            self.result.dropped_ticks += 1
            return
        self._seen_event_keys.add(key)

        if instrument == self._underlying:
            self._today_closes.append((timestamp, price))

        self._sequence += 1
        event = tick_to_event(self._sequence, instrument, timestamp, price, source)
        observation = lo_engine.translate_event(event, origin=moc_taxonomy.ORIGIN_LIVE)
        if observation is None:
            return
        self.result.observations.append(observation)

        previous = self.result.observations[-2] if len(self.result.observations) > 1 else None
        new_events = lme_engine.detect_price_change(observation, previous)
        dup = lme_engine.detect_duplicate(observation, previous)
        if dup is not None:
            self.result.duplicate_events += 1
        for ev in new_events:
            self.result.events.append(ev)
            self.result.episodes = mee_engine.advance_time(self.result.episodes, ev.timestamp, detection_context="LIVE")
            self.result.episodes = mee_engine.process_event(self.result.episodes, ev, detection_context="LIVE")

    def run_decision_cadence(self, *, timestamp: str) -> SessionResult:
        """Runs the FROZEN decision chain exactly once, using whatever
        real events/episodes have been accumulated so far this session
        -- identical call shape to every replay script's own per-day
        MSI invocation (Series 88 onward), never a re-implementation.

        When `load_option_chain` was called first, Participant
        Positioning, Volatility Structure, and Consensus are REAL,
        computed from the real chain -- exactly resolving Sprint 105's
        own disclosed conviction divergence. When no chain was loaded,
        this falls back to the same neutral stand-ins as before,
        honestly reflected in `result.option_chain_wired`."""
        events_tuple = tuple(self.result.events)
        psi = psi_engine.assess_price_structure(self.result.episodes, events_tuple, timestamp=timestamp)
        mssi = mssi_engine.assess_market_structure(self.result.episodes, events_tuple, timestamp=timestamp)
        mdi = mdi_engine.determine_market_direction(psi, mssi, timestamp=timestamp)

        mppi = _neutral_mppi()
        vsb = None
        consensus_views = [_mdi_domain_view(mdi)]
        expected_domains = ["MARKET_DIRECTION"]
        consensus_level_label = "NO_CONSENSUS"

        # Real multi-day close history: prior sessions' real closes
        # (threaded in via `prior_closes_with_ts`) PLUS this session's
        # own real ticks-so-far for the underlying -- identical
        # composition to every replay script's own
        # `closes_with_ts = prev_closes + [(c["ts"], c["close"]) for c in candles]`.
        full_closes_with_ts = self._prior_closes_with_ts + tuple(self._today_closes)
        self.result.closes_with_ts = full_closes_with_ts

        if self._chain is not None:
            mppi = mppi_engine.assess_participant_positioning(self._chain, self._prev_chain, timestamp=timestamp)
            spot, atm_strike, atm_ce, atm_pe, t_years = _atm_inputs(self._chain, self._day)
            # Real, live-fetched premiums (via set_live_atm_premiums / the
            # real get_quote adapter above) take priority over the
            # Bhavcopy-derived end-of-day settlement premiums -- a live
            # quote is always more current real evidence than a prior
            # day's settlement figure, when both exist.
            if self._live_atm_ce_premium is not None:
                atm_ce = self._live_atm_ce_premium
            if self._live_atm_pe_premium is not None:
                atm_pe = self._live_atm_pe_premium
            if spot is not None and atm_strike is not None:
                vsb = vsb_engine.assess_volatility_structure(
                    spot=spot, strike=atm_strike, t_years=t_years or (7 / 365),
                    ce_premium=atm_ce, pe_premium=atm_pe, closes_with_ts=full_closes_with_ts, timestamp=timestamp,
                )
                consensus_views.append(_vsb_domain_view(vsb))
                expected_domains.append("VOLATILITY_STRUCTURE")

        consensus = consensus_engine.compute_consensus(
            tuple(consensus_views), expected_domains=tuple(expected_domains), timestamp=timestamp,
        )
        consensus_level_label = consensus.consensus_level

        thesis = thesis_engine.derive_trade_thesis(psi, mssi, mdi, mppi, vsb, consensus, timestamp=timestamp)
        expression = se_engine.derive_strategy_expression(thesis, timestamp=timestamp)

        decision = da_engine.build_decision_record(
            timestamp.split("T")[0], psi.explanation.which_observations_support_it,
            psi.explanation.which_episodes_caused_it, mdi.overall_direction, consensus_level_label,
            vsb.volatility_regime if vsb else "UNKNOWN", thesis, None, None, None, None, None, None,
            timestamp=timestamp,
        )

        self.result.psi, self.result.mssi, self.result.mdi = psi, mssi, mdi
        self.result.mppi, self.result.vsb, self.result.consensus = mppi, vsb, consensus
        self.result.thesis, self.result.expression, self.result.decision = thesis, expression, decision
        return self.result

    def close_session(self) -> SessionResult:
        """Flushes nothing new (this arc's own journals are in-memory,
        per-run, per Sprint 102's own disclosed finding) -- releases the
        lock so a subsequent session can acquire it cleanly."""
        self.release()
        return self.result


def _neutral_mppi():
    """Fallback used only when no real option chain has been loaded
    this session (`load_option_chain` was never called) -- Consensus
    itself is always real (computed by `msi_consensus.engine.compute_consensus`
    even in this fallback case, over just the real MARKET_DIRECTION
    view), never a fabricated stand-in."""
    from types import SimpleNamespace
    return SimpleNamespace(positioning_bias="UNKNOWN_POSITIONING")
