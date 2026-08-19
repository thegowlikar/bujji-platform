#!/usr/bin/env python
"""Bujji Options OS -- Phase-1 Shadow Runner.

The ONLY outer entrypoint for the Trading Session Governor stack
(production_runtime/, trading_session_governor/, trading_brain/,
msi_trade_construction/, shadow_observatory/). Lives at the repo root,
matching the existing convention for standalone orchestration scripts
(bujji_calibration_cli.py, run_daily_observation.py, run_live_shadow.py)
-- it is deliberately NOT inside the bujji/ package, and it is NOT
`bujji/app.py` (the unrelated, deprecated legacy ORB-VWAP entrypoint).

THIS FILE CONTAINS ZERO TRADING LOGIC. It only:
  - parses CLI arguments
  - loads config/options_os_shadow.yaml (never config/config.yaml)
  - initializes logging under the "bujji-options-os-shadow" namespace
  - constructs the composition root + PaperBroker-based stack
  - constructs Bujji Shadow Observatory recorder
  - constructs TradingSessionGovernor
  - calls Governor lifecycle methods, in order, and nothing else
  - handles exceptions at the process boundary
  - exits cleanly, WITHOUT any auto-resume of a prior session

ARCHITECTURE RULES (verified during the Phase-1 design audit, enforced
here by construction):
  - TradingSessionGovernor is the ONLY entry/exit authority this runner
    talks to. Strategy selection, risk approval, order construction,
    and exit decisions all remain exactly where they already live --
    this file never reimplements or duplicates any of it.
  - ShadowSessionController is deliberately NOT imported or used: its
    own run_entry_cycle()/run_management_cycle()/run_eod_reconciliation()
    each independently call D.4/F.4 a second time, which would produce
    duplicate lifecycle evaluations and duplicate order submissions if
    combined with TradingSessionGovernor's own calls (verified by
    reading its method bodies during the design audit, not assumed).
  - bujji.live_shadow_operator / run_live_shadow.py are NOT imported --
    that is a separate, unrelated system using the legacy exit_engine,
    not D.4.
  - No live broker, no FYERS/websocket wiring, no MIC/regime automation,
    no restart-recovery resurrection -- all explicitly Category B/C
    future work per the design audit, not attempted here.

PHASE-1 LIMITATION, DISCLOSED, NOT HIDDEN: `ReplayChainProvider` sources
a single real historical NSE bhavcopy (an EOD snapshot), so there is no
intraday tick granularity available to this runner. POSITION_MANAGEMENT
and EOD_CLOSE therefore revalue positions using the SAME entry-time
reference prices captured at fill -- unrealized P&L will read as flat
until a live/replay TICK feed (a genuinely different, larger piece of
work) is added. Only the mandatory_exit_time hard limit can meaningfully
fire in Phase-1; profit-target/max-loss hard limits cannot, because no
real price movement is observed. This is an honest architectural
placeholder, not a synthetic-data workaround.

KNOWN, PRE-EXISTING GAP THIS RUNNER DOES NOT PAPER OVER: D.2's own
classify_portfolio_risk() returns RISK_INVALID for a genuinely empty
book, so the very first trade of a fresh session/journal may be blocked
at the PORTFOLIO stage even though nothing is actually wrong (documented
during Gate D.6's own audit). This runner does NOT seed a fabricated
prior position group to work around it -- doing so would fake trading
history. A PORTFOLIO-stage block on session #1 is expected, real D.2
behavior, not a runner bug.
"""
from __future__ import annotations

import argparse
import logging
import sys
import uuid
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 2


class ConfigurationError(Exception):
    """Raised for any missing/invalid input required BEFORE a session
    may begin -- always maps to EXIT_CONFIG_ERROR, never silently
    patched with a guessed value."""


class RunnerStage:
    STARTUP = "STARTUP"
    PRE_MARKET_CHECK = "PRE_MARKET_CHECK"
    MARKET_SESSION = "MARKET_SESSION"
    ENTRY_WINDOW = "ENTRY_WINDOW"
    POSITION_MANAGEMENT = "POSITION_MANAGEMENT"
    EOD_CLOSE = "EOD_CLOSE"
    SESSION_ARCHIVE = "SESSION_ARCHIVE"
    SHUTDOWN = "SHUTDOWN"


def load_config(config_path: Path) -> Dict[str, Any]:
    if not config_path.exists():
        raise ConfigurationError(f"config file not found: {config_path}")
    try:
        with open(config_path) as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"config file {config_path} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"config file {config_path} did not parse to a mapping")
    return data


def parse_args(argv) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bujji Options OS -- Phase-1 Shadow Runner")
    parser.add_argument("--config", default=str(REPO_ROOT / "config" / "options_os_shadow.yaml"),
                         help="Path to options_os_shadow.yaml (never config/config.yaml).")
    date_group = parser.add_mutually_exclusive_group(required=True)
    date_group.add_argument("--as-of-date", help="YYYY-MM-DD -- the session date.")
    date_group.add_argument("--date-today", action="store_true",
                             help="use today's real date -- for systemd, which cannot hardcode a date.")
    parser.add_argument("--lock-path", default="/opt/bujji/app/data/options_os_trading.lock",
                         help="Single-instance lock. Its OWN path -- never another system's.")
    parser.add_argument("--skip-calendar-check", action="store_true",
                         help="bypass the trading-day gate -- manual/testing only, never systemd.")
    parser.add_argument("--skip-market-hours-check", action="store_true",
                         help="bypass the market-hours gate -- replay/testing only, NEVER systemd. "
                              "The gate is this order-placing unit's protection against running "
                              "against a closed-market book.")
    parser.add_argument("--bhavcopy-path", default=None, help="Override providers.market_data.bhavcopy_path.")
    parser.add_argument("--trend-regime", default=None, help="Override regime.trend_regime.")
    parser.add_argument("--volatility-regime", default=None, help="Override regime.volatility_regime.")
    parser.add_argument("--session-id", default=None, help="Override the generated session_id.")
    return parser.parse_args(argv)


def _emergency_brake(*, unrealized_pnl, realized_pnl, daily_loss_limit,
                     consecutive_blind_cycles, max_consecutive_blind_cycles) -> "Optional[str]":
    """Pure. The only loss authority BETWEEN scheduled exits (Master Plan D-6:
    emergency close was MISSING -- nothing watched between 5-minute passes and
    blind cycles disabled even the per-position stop).

    Two triggers, both fail-safe:
      1. Session loss breach: realized + unrealized <= -daily_loss_limit.
      2. Sustained blindness WITH an open position: if we cannot price the
         book for N consecutive cycles, we cannot know the loss -- get out
         rather than hold what we cannot see.
    Returns the reason string, or None to continue."""
    if (max_consecutive_blind_cycles
            and consecutive_blind_cycles >= max_consecutive_blind_cycles):
        return (f"EMERGENCY_BLIND: {consecutive_blind_cycles} consecutive unpriced "
                f"cycles with an open position -- cannot see, will not hold")
    if unrealized_pnl is None or not daily_loss_limit:
        return None
    total = (realized_pnl or 0.0) + unrealized_pnl
    if total <= -abs(daily_loss_limit):
        return (f"EMERGENCY_LOSS: session P&L {total:.0f} breached the daily "
                f"loss limit -{abs(daily_loss_limit):.0f}")
    return None


def _make_capital_snapshot_provider(capital_cfg: dict, clock, log=None):
    """The capital snapshot the risk context consults (STOP #1).

    Selected by `capital_snapshot.source`:

      "static"       The pre-2026-08-18 behaviour, verbatim: every field from
                     config with defaults totalling Rs 1 crore of capital that
                     does not exist. Kept for legacy configs and offline
                     tests; warns loudly every session.
      "fyers_funds"  Real account capital from the live-certified get_funds()
                     endpoint (2026-07-19). Requires daily_loss_limit and
                     max_allowed_drawdown declared in the same block --
                     operator-owned risk limits, no defaults. Fails closed
                     when reality is unavailable for too long.
    """
    from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot

    log = log or logging.getLogger("bujji.options_os_runner")
    source = str(capital_cfg.get("source", "static")).strip().lower()
    if source == "static":
        declared = (capital_cfg.get("total_capital") is not None
                    and capital_cfg.get("available_capital") is not None)
        if declared:
            log.info(
                "capital snapshot: DECLARED SIMULATED capital of %s (operator "
                "declaration in the session YAML; real margin still applies).",
                capital_cfg["total_capital"])
        else:
            log.warning(
                "capital snapshot is STATIC with UNDECLARED values -- defaults "
                "fabricate Rs 1cr. Declare total/available capital explicitly, or "
                "set capital_snapshot.source: fyers_funds for real account capital.")

        def capital_snapshot_provider() -> CapitalSafetySnapshot:
            return CapitalSafetySnapshot(
                total_capital=capital_cfg.get("total_capital", 10_000_000.0),
                available_capital=capital_cfg.get("available_capital", 10_000_000.0),
                used_margin=capital_cfg.get("used_margin", 200_000.0),
                open_risk=capital_cfg.get("open_risk", 100_000.0),
                reserved_risk=capital_cfg.get("reserved_risk", 0.0),
                daily_pnl=capital_cfg.get("daily_pnl", 0.0),
                daily_loss_limit=capital_cfg.get("daily_loss_limit", 500_000.0),
                peak_capital=capital_cfg.get("peak_capital", 10_000_000.0),
                max_allowed_drawdown=capital_cfg.get("max_allowed_drawdown", 0.20),
                consecutive_losses=capital_cfg.get("consecutive_losses", 0),
                timestamp=clock(),
            )

        return capital_snapshot_provider
    if source == "fyers_funds":
        from bujji.broker.fyers_funds_capital import FyersCapitalSnapshotProvider

        return FyersCapitalSnapshotProvider(policy=capital_cfg, clock=clock)
    raise RuntimeError(
        f"capital_snapshot.source={source!r} is not one of: static, fyers_funds")


def _make_margin_provider(providers_cfg: dict, log=None):
    """The margin provider the session's risk context will consult.

    Selected by `providers.margin` in the session YAML -- a PATH-level choice
    with three honest states, no silent middle ground:

      "simulated"          SimulatedMarginProvider. Reports margin_verified=True
                           WITHOUT any broker -- test-fixture semantics that its
                           own docstring forbids in production. Kept only so
                           legacy configs and offline tests keep working, and it
                           logs a warning naming itself every time.
      "fyers_uncertified"  Real whole-book quotes from the live-certified
                           span_margin endpoint, margin_verified=False by
                           construction -> capital_check VETOes every entry.
                           Honest, and intentionally blocking: this is the state
                           to run while deciding whether to certify.
      "fyers_certified"    Same real quotes, margin_verified=True. OPERATOR
                           DECISION ONLY (CertifiedWholeBookMarginProvider's
                           human-confirmation contract) -- never make this a
                           default.

    Unknown mode or missing FYERS credentials for a fyers mode: RuntimeError.
    Sizing risk off a guess is worse than not starting.
    """
    import os

    from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider

    log = log or logging.getLogger("bujji.options_os_runner")
    block = providers_cfg.get("margin", {"type": "simulated"})
    if not isinstance(block, dict):
        # Every entry under providers: is a type-block (see market_data) and
        # the paper-only deployment guard iterates them on that assumption.
        raise RuntimeError(
            f"providers.margin must be a block like {{type: simulated}}, got {block!r}")
    mode = str(block.get("type", "simulated")).strip().lower()
    if mode == "simulated":
        log.warning(
            "margin provider is SIMULATED (test-fixture margin, margin_verified=True "
            "with no broker behind it). Set providers.margin to fyers_uncertified / "
            "fyers_certified in the session YAML for real SPAN margin.")
        return SimulatedMarginProvider()
    if mode in ("fyers_uncertified", "fyers_certified"):
        from bujji.broker.fyers_span_margin import build_fyers_margin_provider

        app_id = os.getenv("FYERS_APP_ID")
        access_token = os.getenv("FYERS_ACCESS_TOKEN")
        if not app_id or not access_token:
            raise RuntimeError(
                f"providers.margin={mode!r} needs FYERS_APP_ID and FYERS_ACCESS_TOKEN "
                "in the environment -- refusing to run with margin from a guess.")
        certified = mode == "fyers_certified"
        if not certified:
            log.warning(
                "margin provider is FYERS UNCERTIFIED: real broker quotes, "
                "margin_verified=False by construction -- capital_check will VETO "
                "every entry until an operator sets providers.margin: fyers_certified.")
        return build_fyers_margin_provider(
            app_id=app_id, access_token=access_token, certified=certified)
    raise RuntimeError(
        f"providers.margin={mode!r} is not one of: simulated, fyers_uncertified, "
        "fyers_certified")


def _resolve_exchange_lot_size(session_cfg: dict, log=None, cache_dir=None) -> int:
    """The exchange lot size the session will size every order with.

    AUTHORITATIVE SOURCE: the FYERS instrument master cache -- never the YAML.
    The 2026-07-19 audit found the live master says NIFTY=65 while the session
    YAMLs (and the old `session_cfg.get(..., 75)` fallback here) said 75, so
    every constructed quantity was 15.4% oversized. The YAML value is kept
    only as a cross-check: a mismatch logs a warning naming both numbers
    (config rot made visible), but the master's number is what is used.

    Fails CLOSED: if the master cannot answer (no cache, no rows, or a
    lot-size transition where expiries disagree), the session refuses to
    start rather than sizing off a guess. RuntimeError carries the cause.
    """
    from bujji.broker.instrument_master import InstrumentMaster

    log = log or logging.getLogger("bujji.options_os_runner")
    underlying = session_cfg.get("underlying", "NIFTY")
    # `instrument_master_dir` selects WHICH master file to read (tests and
    # replay point it at a pinned fixture); it is a path, never a number --
    # there is deliberately no way to hand this function a lot size directly.
    cache_dir = cache_dir or session_cfg.get("instrument_master_dir")
    master = InstrumentMaster(
        Path(cache_dir) if cache_dir else REPO_ROOT / "data" / "instrument_master", log)
    try:
        real = master.lot_size_for(underlying)
    except Exception as exc:
        raise RuntimeError(
            f"exchange lot size for {underlying} unresolvable from the "
            f"instrument master -- refusing to size orders off a guess: {exc}"
        ) from exc
    yaml_value = session_cfg.get("exchange_lot_size")
    if yaml_value is not None and int(yaml_value) != real:
        log.warning(
            "exchange_lot_size mismatch: config says %s, instrument master says "
            "%s for %s -- using the master. Update the session YAML.",
            yaml_value, real, underlying,
        )
    return real


def build_logger(namespace: str) -> logging.Logger:
    logger = logging.getLogger(namespace)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(f"%(asctime)s | %(levelname)-7s | {namespace} | %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


class OptionsOSRunner:
    """Orchestrates exactly one shadow session through the 8-stage
    lifecycle. Holds no trading decision of its own -- every decision-
    shaped call goes to TradingSessionGovernor or the existing F.1-F.4
    components it composes."""

    def __init__(self, config: Dict[str, Any], as_of_date: str, session_id: str,
                 logger: logging.Logger, clock=None) -> None:
        """`clock` is a zero-argument callable returning an aware datetime.

        INJECTED BECAUSE THE SESSION IS TIME-DRIVEN. `_position_management`
        compares the clock against `monitor_until` to decide when to stop
        monitoring, so a runner that can only read the real wall clock
        cannot be exercised at a chosen point in the trading day -- the
        behaviour under test depends entirely on when the test happens to
        run. That bit for real on 2026-08-18: with no injection point,
        tests inherited the production defaults (300s x 78 cycles) and the
        loop exited early only because every run happened after 15:15. The
        first run started after midnight slept for 6h30m.

        Defaults to `now_ist`, so production behaviour is unchanged and no
        caller has to pass anything.
        """
        from bujji.core.clock import now_ist
        self._clock_fn = clock if clock is not None else now_ist
        self._config = config
        self._as_of_date = as_of_date
        self._session_id = session_id
        self._logger = logger
        self._stage = RunnerStage.STARTUP

        # Populated during STARTUP.
        self._broker = None
        self._journal = None
        self._root = None
        self._trading_brain_runtime = None
        self._registry = None
        self._lifecycle_runtime = None
        self._portfolio_engine = None
        self._executor = None
        self._governor = None
        self._store = None
        self._recorder = None
        self._market_data_provider = None
        self._regime_provider = None
        self._intelligence_broker = None
        self._regime_as_of = None

        self._entry_prices: Dict[str, float] = {}
        self._governor_result_summary: Dict[str, Any] = {}

        # Canonical position lifecycle (bujji.position_lifecycle), driven
        # through lifecycle_outcome_bridge. This is what closes the
        # learning loop: before it, this runner reached a real PaperBroker
        # fill and a real exit, then stopped at PositionLifecycleRuntime's
        # in-memory enum flip and produced NO OutcomeMemoryRecord at all,
        # so a 30-session paper campaign taught Bujji nothing. The
        # registry/PositionLifecycleRuntime bookkeeping above is
        # untouched and still owns risk-side state -- this is the
        # outcome/learning record, not a second execution path.
        self._lifecycle_states: Dict[str, Any] = {}
        self._canonical_position_id: Optional[str] = None
        self._exit_prices_by_leg: Dict[str, dict] = {}
        # D-8: every REAL order this session placed, entry and exit. A
        # position's cost is what it cost to get in AND out; charging one
        # side understates every round trip.
        self._execution_order_ids: List[str] = []
        self._contracts_by_symbol: Dict[str, Any] = {}
        self._outcome_memory_record = None
        # Real per-cycle unrealized P&L for the open position, appended
        # once per management pass -- the raw material for MFE/MAE.
        self._valuation_history: list = []
        # Optional intraday tick source. When absent the runner falls
        # back to entry prices and says so -- see _current_leg_prices().
        self._price_provider = None
        self._priced_from_ticks_cycles = 0
        self._blind_cycles = 0

    def _clock(self):
        """The session's clock. Stays a BOUND METHOD on purpose: components
        are wired with `clock=self._clock` throughout, so the injection
        point had to sit behind this call rather than replace it."""
        return self._clock_fn()

    # ------------------------------------------------------------ #
    # Lifecycle stages
    # ------------------------------------------------------------ #

    def run(self) -> Dict[str, Any]:
        try:
            self._startup()
            self._pre_market_check()
            self._market_session()
            if self._session_cfg.get("continuous"):
                self._continuous_session()
            else:
                self._entry_window()
                self._position_management()
            self._eod_close()
            self._session_archive()
        finally:
            self._shutdown()
        return self._governor_result_summary

    def _startup(self) -> None:
        self._stage = RunnerStage.STARTUP
        self._logger.info("STARTUP -- session_id=%s as_of_date=%s", self._session_id, self._as_of_date)

        from bujji.broker.paper import PaperBroker
        from bujji.journal.position_group_journal import PositionGroupJournal
        from bujji.trading_brain.risk_governor.simulated_margin_provider import SimulatedMarginProvider
        from bujji.trading_brain.risk_governor.adaptive_risk_memory import AdaptiveRiskMemory
        from bujji.trading_brain.risk_governor.capital_safety_governor import CapitalSafetySnapshot
        from bujji.production_runtime.runtime_state_machine import RuntimeState
        from bujji.production_runtime.trading_brain_composition_root import build_trading_brain_composition_root
        from bujji.production_runtime.trading_brain_runtime import TradingBrainRuntime
        from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
        from bujji.production_runtime.position_lifecycle_runtime import PositionLifecycleRuntime
        from bujji.production_runtime.portfolio_reality_engine import PortfolioRealityEngine
        from bujji.production_runtime.trade_lifecycle_executor import TradeLifecycleExecutor
        from bujji.production_runtime.trading_session_governor.session_governor import TradingSessionGovernor
        from bujji.production_runtime.trading_session_governor.exit_policy import ExitPolicyConfig
        from bujji.production_runtime.market_data_provider import ReplayChainProvider
        from bujji.production_runtime.regime_provider import HumanSuppliedRegimeProvider
        from bujji.shadow_observatory.session_store import SessionStore
        from bujji.shadow_observatory.recorder import ShadowObservatoryRecorder

        session_cfg = self._config.get("session", {})
        providers_cfg = self._config.get("providers", {})
        capital_cfg = self._config.get("capital_snapshot", {})
        exit_cfg = self._config.get("exit_policy", {})
        artifacts_cfg = self._config.get("artifacts", {})

        underlying = session_cfg.get("underlying", "NIFTY")
        exchange_lot_size = _resolve_exchange_lot_size(session_cfg)

        # Realistic execution: the NORMAL profile's slippage/latency
        # (built in Gate F.2, previously dead code for every live path).
        # Constructed via the factory helper so there is ONE place that
        # defines production execution realism. A frictionless broker
        # made every 4-leg premium-selling fill look free, inflating the
        # credit recorded on every trade of the campaign.
        from bujji.broker.factory import _production_paper_broker
        self._broker = _production_paper_broker()

        journal_path = REPO_ROOT / artifacts_cfg.get("journal_path", "data/options_os_shadow_position_group_journal.db")
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._journal = PositionGroupJournal(str(journal_path))

        capital_snapshot_provider = _make_capital_snapshot_provider(
            capital_cfg, self._clock)

        self._root = build_trading_brain_composition_root(
            broker=self._broker, journal=self._journal,
            margin_provider=_make_margin_provider(providers_cfg),
            capital_snapshot_provider=capital_snapshot_provider, memory=AdaptiveRiskMemory(),
            clock=self._clock, underlying=underlying, exchange_lot_size=exchange_lot_size,
            initial_state=RuntimeState.ENTRY_ENABLED,
        )
        self._trading_brain_runtime = TradingBrainRuntime(self._root)

        self._registry = PositionRealityRegistry(self._broker)
        self._lifecycle_runtime = PositionLifecycleRuntime(self._registry, self._clock)
        self._portfolio_engine = PortfolioRealityEngine(self._registry, event_bus=self._root.event_bus)
        self._executor = TradeLifecycleExecutor(self._broker, self._registry, self._lifecycle_runtime,
                                                 event_bus=self._root.event_bus)

        mandatory_exit_time = None
        raw_time = exit_cfg.get("mandatory_exit_time")
        if raw_time:
            hh, mm, ss = (int(part) for part in str(raw_time).split(":"))
            mandatory_exit_time = dt_time(hh, mm, ss)
        exit_policy_config = ExitPolicyConfig(
            profit_target_fraction=exit_cfg.get("profit_target_fraction"),
            max_loss_fraction=exit_cfg.get("max_loss_fraction"),
            mandatory_exit_time=mandatory_exit_time,
        )

        self._governor = TradingSessionGovernor(
            session_id=self._session_id, trading_brain_runtime=self._trading_brain_runtime,
            registry=self._registry, lifecycle_runtime=self._lifecycle_runtime, executor=self._executor,
            exit_policy_config=exit_policy_config, clock=self._clock, event_bus=self._root.event_bus,
        )

        shadow_sessions_root = REPO_ROOT / artifacts_cfg.get("shadow_sessions_root", "shadow_sessions")
        self._store = SessionStore(shadow_sessions_root, self._session_id)
        self._recorder = ShadowObservatoryRecorder(self._store)
        self._recorder.attach(self._root.event_bus)

        market_data_cfg = providers_cfg.get("market_data", {})
        md_type = (market_data_cfg.get("type") or "replay_chain").lower()
        if md_type == "observation_store":
            # The chain as it really stood at a moment on a CAPTURED day,
            # from the same observation store the tick source reads -- so
            # entry book and revaluation come from one coherent session
            # instead of a bhavcopy on one date and ticks on another.
            from bujji.historical_reality.store import HistoricalObservationStore
            from bujji.production_runtime.store_chain_provider import StoreChainProvider

            store_path = market_data_cfg.get("observation_store_path")
            if not store_path:
                raise ConfigurationError(
                    "providers.market_data.observation_store_path is required for "
                    "type=observation_store -- refusing to guess."
                )
            self._market_data_provider = StoreChainProvider(
                HistoricalObservationStore(store_path), underlying=underlying,
                as_of_time=market_data_cfg.get("chain_as_of_time", "T09:20:00+05:30"),
            )
        elif md_type == "fyers_live":
            # The live book, right now. Reuses the guarded FyersBroker
            # (market DATA only -- disable_live_execution neuters the
            # order surface before anything touches it), and reads
            # premiums from the raw optionchain response whose field
            # names were established from a real dated capture rather
            # than guessed.
            import os as _os

            from bujji.broker.fyers import FyersBroker
            from bujji.broker.guard import disable_live_execution
            from bujji.core.config import BrokerConfig
            from bujji.production_runtime.live_chain_provider import LiveChainProvider

            app_id, token = _os.getenv("FYERS_APP_ID"), _os.getenv("FYERS_ACCESS_TOKEN")
            if not app_id or not token:
                raise ConfigurationError(
                    "FYERS_APP_ID / FYERS_ACCESS_TOKEN must be set for market_data type=fyers_live "
                    "-- refusing to trade on an absent book rather than falling back to stale data."
                )
            chain_broker = disable_live_execution(FyersBroker(
                BrokerConfig(name="fyers", app_id=app_id, access_token=token,
                             app_secret=_os.getenv("FYERS_APP_SECRET"),
                             refresh_token=_os.getenv("FYERS_REFRESH_TOKEN"),
                             pin=_os.getenv("FYERS_PIN")),
                self._logger,
            ))
            import asyncio as _asyncio
            _asyncio.run(chain_broker.connect())
            self._market_data_provider = LiveChainProvider(
                chain_broker, underlying=underlying,
                strike_count=int(market_data_cfg.get("strike_count", 20)),
            )
        else:
            bhavcopy_path = market_data_cfg.get("bhavcopy_path")
            if not bhavcopy_path:
                raise ConfigurationError(
                    "providers.market_data.bhavcopy_path is required (config or --bhavcopy-path) -- refusing to guess."
                )
            self._market_data_provider = ReplayChainProvider(bhavcopy_path=bhavcopy_path, underlying=underlying)

        regime_cfg = providers_cfg.get("regime", {})
        regime_type = (regime_cfg.get("type") or "human_supplied").lower()
        if regime_type == "market_thesis":
            # Bujji derives its OWN regime from real market evidence
            # instead of being told one. Composes already-built, already-
            # tested pieces -- MarketDataAdapter -> IntelligenceCycleRecorder
            # -> CycleEvidence -> market_thesis.assess() ->
            # MarketThesisRegimeProvider -- over a read-only replay broker.
            # Nothing here re-derives intelligence; it only supplies the
            # data source that was missing.
            from bujji.historical_reality.store import HistoricalObservationStore
            from bujji.production_runtime.replay_market_broker import ReplayMarketBroker

            store_path = (regime_cfg.get("observation_store_path")
                          or market_data_cfg.get("observation_store_path"))
            if not store_path:
                raise ConfigurationError(
                    "providers.regime.observation_store_path is required for type=market_thesis "
                    "-- refusing to guess."
                )
            self._regime_as_of = regime_cfg.get(
                "as_of", f"{self._as_of_date}{market_data_cfg.get('chain_as_of_time', 'T09:20:00+05:30')}")
            self._intelligence_broker = ReplayMarketBroker(
                HistoricalObservationStore(store_path), as_of=self._regime_as_of, underlying=underlying,
            )
            self._regime_provider = self._build_market_thesis_regime_provider()
        elif regime_type == "market_thesis_live":
            # The LIVE equivalent needs no new facade: FyersBroker already
            # exposes the same six read-only market-data methods the
            # snapshot path uses, and `disable_live_execution` neuters
            # place/modify/cancel/positions on the instance BEFORE it is
            # handed to anything -- the identical guard pattern
            # run_daily_intelligence_session.py and the Cycle-1 entrypoint
            # already use. Writing a parallel live facade would duplicate
            # a broker that already exists and is live-verified.
            import os as _os

            from bujji.broker.fyers import FyersBroker
            from bujji.broker.guard import disable_live_execution
            from bujji.core.config import BrokerConfig

            app_id, token = _os.getenv("FYERS_APP_ID"), _os.getenv("FYERS_ACCESS_TOKEN")
            if not app_id or not token:
                raise ConfigurationError(
                    "FYERS_APP_ID / FYERS_ACCESS_TOKEN must be set for regime type=market_thesis_live. "
                    "Token renewal is a human step (SEBI-bound) -- refusing to start blind rather than "
                    "silently falling back to a weaker regime source."
                )
            self._regime_as_of = regime_cfg.get("as_of") or self._clock().isoformat()
            self._intelligence_broker = disable_live_execution(FyersBroker(
                BrokerConfig(name="fyers", app_id=app_id, access_token=token,
                             app_secret=_os.getenv("FYERS_APP_SECRET"),
                             refresh_token=_os.getenv("FYERS_REFRESH_TOKEN"),
                             pin=_os.getenv("FYERS_PIN")),
                self._logger,
            ))
            import asyncio as _asyncio
            _asyncio.run(self._intelligence_broker.connect())
            self._regime_provider = self._build_market_thesis_regime_provider()
        else:
            self._regime_provider = HumanSuppliedRegimeProvider(
                trend_regime=regime_cfg.get("trend_regime"), volatility_regime=regime_cfg.get("volatility_regime"),
            )

        # ---- Tick source (constructed AFTER the regime block, on purpose). ----
        # `type: broker` hands the management loop the SAME execution-neutered
        # live FyersBroker the regime/warmup evidence path builds
        # (self._intelligence_broker under regime type market_thesis_live) --
        # never the PaperBroker. The previous wiring (fixed 2026-08-19, Master
        # Plan D-3) passed self._broker, a PaperBroker whose get_ltp is a
        # seeded random walk, while logging "live quotes": the first session
        # to open a position would have fired stops and recorded MFE/MAE
        # against fabricated prices. Fail closed: when this config has no real
        # data broker, refuse to start rather than silently substituting
        # synthetic prices (constitution: no silent live->synthetic fallback).
        # A test or replay that WANTS the synthetic walk must declare it:
        # `type: paper_synthetic`.
        tick_cfg = providers_cfg.get("tick_source", {})
        tick_type = (tick_cfg.get("type") or "none").lower()
        if tick_type == "observation_store":
            from bujji.historical_reality.store import HistoricalObservationStore
            from bujji.production_runtime.intraday_price_provider import HistoricalTickProvider

            tick_store_path = tick_cfg.get("observation_store_path") or market_data_cfg.get("observation_store_path")
            if not tick_store_path:
                raise ConfigurationError(
                    "providers.tick_source.observation_store_path is required for "
                    "type=observation_store -- refusing to guess."
                )
            self._price_provider = HistoricalTickProvider(HistoricalObservationStore(tick_store_path))
            self._logger.info("Tick source: observation_store (%s)", tick_store_path)
        elif tick_type == "broker":
            import asyncio as _asyncio

            from bujji.production_runtime.intraday_price_provider import LiveTickProvider

            data_broker = getattr(self, "_intelligence_broker", None)
            if regime_type != "market_thesis_live" or data_broker is None:
                raise ConfigurationError(
                    "providers.tick_source.type=broker requires the live FYERS data "
                    "broker (providers.regime.type=market_thesis_live). Under this "
                    "config the only broker available is the synthetic PaperBroker -- "
                    "refusing to price position management off a random walk while "
                    "calling it live. Declare type=paper_synthetic to opt into "
                    "synthetic ticks explicitly, or type=observation_store for replay."
                )
            self._price_provider = LiveTickProvider(data_broker, _asyncio.run)
            self._logger.info(
                "Tick source: FYERS live quotes (execution-neutered data broker; "
                "same instance as the regime evidence path)")
        elif tick_type == "paper_synthetic":
            import asyncio as _asyncio

            from bujji.production_runtime.intraday_price_provider import LiveTickProvider

            self._price_provider = LiveTickProvider(self._broker, _asyncio.run)
            self._logger.warning(
                "Tick source: PAPER SYNTHETIC -- the PaperBroker's seeded random "
                "walk, NOT market data. Every management price is fabricated by "
                "construction. Test/replay use only; never a live campaign.")
        else:
            self._logger.warning(
                "Tick source: NONE. Every management cycle will be BLIND -- positions will be "
                "revalued against their own entry prices, so unrealized P&L is 0 by construction "
                "and no stop-loss or profit-target can fire. Set providers.tick_source.type."
            )

        self._session_cfg = session_cfg


    def _warm_up_observation_memory(self, adapter):
        """Poll real spot, gate the result for sampling stability, and
        return an ObservationMemory to seed the recorder with -- or None.

        Returns None (leaving single-cycle behaviour untouched) when no
        `regime.warmup` block is configured. Returns None AND records the
        refusal when the gate finds the regime unstable, so the thesis
        falls through to its own honest NO_TRADE rather than being handed
        evidence whose reading depends on how often we happened to look.
        """
        import asyncio

        warmup_cfg = (self._config.get("providers", {})
                      .get("regime", {}).get("warmup"))
        if not warmup_cfg:
            return None

        from bujji.market_state_builder.recovery import replay_snapshots
        from bujji.regime_stability import (
            DEFAULT_STRIDES, WarmupPlan, assess_stability, poll_spot_series,
        )

        plan = WarmupPlan(
            polls=int(warmup_cfg.get("polls", 16)),
            interval_seconds=float(warmup_cfg.get("interval_seconds", 30.0)),
            strides=tuple(warmup_cfg.get("strides", DEFAULT_STRIDES)),
        )
        # Fails fast at STARTUP, not after polling for minutes and then
        # discovering the gate must refuse for a configuration reason that
        # had nothing to do with the market.
        plan.validate()
        self._logger.info(
            "WARMUP -- %d polls every %.0fs (delays entry by %.1f min), strides=%s",
            plan.polls, plan.interval_seconds, plan.duration_seconds / 60.0,
            list(plan.strides),
        )

        broker = self._intelligence_broker
        snapshots = poll_spot_series(
            fetch_spot=lambda: asyncio.run(broker.get_spot(self._root.underlying)),
            clock=self._clock, plan=plan, logger=self._logger,
        )

        def derive(window):
            # Uses the SHARED accumulation loop. An earlier revision looped
            # `builder.process(...)` inline here -- a fourth copy of the very
            # thing `replay_snapshots` was extracted to consolidate.
            outcome = replay_snapshots(window)
            if outcome.last_assessment is None:
                return None
            state = outcome.last_assessment.price_structure.structure_state
            return None if state == "UNKNOWN" else state

        verdict = assess_stability(snapshots, derive, strides=plan.strides)
        self._governor_result_summary["warmup_polls"] = len(snapshots)
        self._governor_result_summary["regime_stable"] = verdict.is_stable
        self._governor_result_summary["regime_stability_reason"] = verdict.reason

        if not verdict.is_stable:
            self._logger.warning("WARMUP -- regime REFUSED by the stability gate: %s",
                                 verdict.reason)
            return None

        self._logger.info("WARMUP -- stable across strides: %s", verdict.reason)
        outcome = replay_snapshots(snapshots)
        for err in outcome.errors:
            self._logger.warning("WARMUP -- snapshot replay error: %s", err)
        self._logger.info("WARMUP -- seeded observation memory from %d real polls",
                          outcome.replayed)
        return outcome.memory

    def _build_market_thesis_regime_provider(self, seeded_memory=None):
        """Run one real intelligence cycle and translate its thesis into
        the governor's regime vocabulary.

        Every stage is an existing, tested component. If the captured
        evidence is too thin to support a thesis, `MarketThesisRegimeProvider`
        raises `MissingRegimeInputError` at `get_regime()` and the session
        ends as a NO-TRADE day -- which is the correct autonomous answer
        to "I cannot see well enough to decide", and strictly better than
        a human supplying a regime the evidence does not support.
        """
        import asyncio

        from bujji.market_perception.market_data_adapter import MarketDataAdapter
        from bujji.market_state.intelligence_cycle_recorder import IntelligenceCycleRecorder
        from bujji.market_thesis import assess as assess_market_thesis
        from bujji.production_runtime.market_thesis_regime_provider import MarketThesisRegimeProvider

        broker = self._intelligence_broker
        adapter = MarketDataAdapter(broker, self._clock, underlying=self._root.underlying)

        # WARM-UP + STABILITY GATE (opt-in via regime.warmup).
        #
        # Without it the recorder sees exactly ONE snapshot, and the
        # price-structure domains are cross-cycle by construction: PSI needs
        # >= 3 real price deltas before swing_state leaves NO_SWING_DATA, so
        # PSI/MSSI/MDI could never form an opinion and the thesis was
        # structurally NO_TRADE every session, whatever the market did.
        #
        # The warm-up feeds real spaced spot polls through the SAME builder
        # first. But a warm-up alone is not enough: replaying 478 real
        # captured snapshots showed the derived structure moving with the
        # SAMPLING INTERVAL alone (30/60/120s -> TRENDING, 180s ->
        # CORRECTING). So the gate derives at several strides over the one
        # polled series and demands unanimity -- a regime that changes when
        # you look at it differently is not a reading of the market.
        #
        # Measured on that real capture: only ~8% of windows are stable.
        # This is expected and is the point. It converts "NO_TRADE because
        # starved" into "NO_TRADE because the structure is not resolvable",
        # with the disagreeing strides named. Absent the config block the
        # single-cycle behaviour below is unchanged.
        # Continuous mode hands in its OWN rolling evidence window -- the
        # one-shot warm-up (8 minutes of fresh polls) is the single-shot
        # path's way of building what continuous mode maintains all day.
        warmup_memory = (seeded_memory if seeded_memory is not None
                         else self._warm_up_observation_memory(adapter))
        recorder = IntelligenceCycleRecorder(
            underlying=self._root.underlying,
            initial_observation_memory=warmup_memory,
        ) if warmup_memory is not None else IntelligenceCycleRecorder(
            underlying=self._root.underlying)

        snapshot = asyncio.run(adapter.build_snapshot())
        candles = asyncio.run(broker.get_recent_candles(self._root.underlying, 5, 75))
        # D-5: record_cycle RETURNS the understanding layer's own honest
        # record of this cycle's conclusions. It used to be called for its
        # side effect and the record dropped on the floor -- so a NO_TRADE
        # day left no trace of WHY. It is now persisted (see _persist_thesis).
        cycle_record = asyncio.run(recorder.record_cycle(snapshot, broker, self._clock, candles=candles))

        evidence = recorder.last_evidence
        if evidence is None:
            raise ConfigurationError(
                "intelligence cycle produced no evidence at "
                f"{self._regime_as_of!r} -- refusing to trade on absent understanding."
            )
        thesis = assess_market_thesis(
            psi=evidence.psi, mssi=evidence.mssi, mdi=evidence.mdi, mppi=evidence.mppi,
            vsb=evidence.vsb, consensus=evidence.consensus, liquidity=evidence.liquidity,
            timestamp=evidence.timestamp,
        )
        volatility_regime = evidence.vsb.volatility_regime if evidence.vsb is not None else None
        self._logger.info(
            "Regime derived from market evidence: market_regime=%s directional_bias=%s volatility=%s",
            getattr(thesis, "market_regime", None), getattr(thesis, "directional_bias", None),
            volatility_regime,
        )
        self._governor_result_summary["regime_source"] = "market_thesis"
        self._governor_result_summary["derived_market_regime"] = getattr(thesis, "market_regime", None)

        # D-5: hold the audit record until the provider has mapped the thesis
        # into the governor's two-string regime vocabulary, so the persisted
        # record links evidence -> thesis -> regime -> selection end to end.
        # Completed and written by _persist_thesis() after get_regime().
        from bujji.shadow_observatory.thesis_artifact import build_thesis_artifact
        self._pending_thesis_artifact = build_thesis_artifact(
            thesis=thesis, cycle_record=cycle_record,
            stability=getattr(self, "_pending_stability", None),
            cycle=getattr(self, "_pending_cycle", None),
            recorded_at=self._clock().isoformat(),
            level_context=self._level_context_dict(),
        )
        return MarketThesisRegimeProvider(thesis, volatility_regime)

    def _persist_thesis(self, trend_regime=None, volatility_regime=None) -> None:
        """Write the pending derivation record, now that we know what the
        selector was actually handed. Never raises into the session: the
        recorder swallows its own failures, and a missing audit record must
        not end a run that may hold an open position."""
        artifact = getattr(self, "_pending_thesis_artifact", None)
        if artifact is None:
            return
        artifact["regime_handed_to_selector"] = {
            "trend_regime": trend_regime, "volatility_regime": volatility_regime,
        }
        self._recorder.record_market_thesis(artifact)
        self._pending_thesis_artifact = None
        verdict = artifact.get("family_verdict") or {}
        self._logger.info(
            "THESIS RECORDED -- regime=%s->%s families preferred=%s rejected=%s unknown=%s",
            artifact.get("thesis", {}).get("market_regime"), trend_regime,
            len(verdict.get("preferred") or []), len(verdict.get("rejected") or []),
            len(verdict.get("insufficient_evidence") or []))

    def _load_price_levels(self) -> None:
        """Load today's price-levels map, once, at session start.

        NEVER FATAL. A missing or unreadable map means Bujji trades exactly
        as it did yesterday -- nothing in the decision chain consumes this.
        The absence is recorded rather than silently tolerated, because "no
        map on the day of a trade" is a fact the audit trail should carry.
        """
        self._levels = None
        self._zones = None
        self._levels_snapshot_info = None
        try:
            from bujji.price_levels import latest_snapshot_path, load_snapshot, snapshot_age_days

            directory = str(REPO_ROOT / "data" / "price_levels")
            path = latest_snapshot_path(self._as_of_date, directory)
            if path is None:
                self._levels_snapshot_info = {"status": "NO_SNAPSHOT", "directory": directory}
                self._logger.warning(
                    "PRICE LEVELS -- no snapshot on or before %s in %s; the session runs "
                    "without a structure map (observation-only, no decision impact).",
                    self._as_of_date, directory)
                return
            snapshot = load_snapshot(path)
            self._levels = snapshot.levels
            self._zones = snapshot.zones
            age = snapshot_age_days(snapshot.built_for, self._as_of_date)
            self._levels_snapshot_info = {
                "status": "LOADED", "path": path, "built_for": snapshot.built_for,
                "built_at": snapshot.built_at, "age_days": age,
                "instrument": snapshot.instrument, "resolution": snapshot.resolution,
                "levels": len(snapshot.levels.levels), "zones": len(snapshot.zones.zones),
                "live_zones": len(snapshot.zones.live_zones),
            }
            self._logger.info(
                "PRICE LEVELS -- map loaded: %d levels, %d zones (%d live), built for %s "
                "(%d day(s) old).", len(snapshot.levels.levels), len(snapshot.zones.zones),
                len(snapshot.zones.live_zones), snapshot.built_for, age)
        except Exception as exc:  # noqa: BLE001 -- an observation layer must not end a session
            self._levels_snapshot_info = {"status": f"FAILED:{type(exc).__name__}",
                                          "error": str(exc)}
            self._logger.warning("PRICE LEVELS -- map unavailable (%s); session continues "
                                 "without it.", exc)

    def _level_context_dict(self):
        """Where price sits relative to structure, right now.

        Live samples update touch counts on the loaded map before the context
        is built -- they TEST structure, never form or break it (see
        price_levels.live). Returns None when there is no map, which the
        thesis record carries as a real absence.
        """
        levels = getattr(self, "_levels", None)
        spot = getattr(self, "_last_spot", None)
        if levels is None or spot is None:
            return None
        try:
            from bujji.price_levels import apply_sample, build_level_context

            self._levels, self._zones, touched, tested = apply_sample(
                levels, getattr(self, "_zones", None), float(spot), self._clock().isoformat())
            context = build_level_context(spot=float(spot), levels=self._levels,
                                          zones=self._zones)
            payload = context.to_dict()
            payload["live_sample"] = {"price": float(spot), "levels_touched": touched,
                                      "zones_tested": tested}
            payload["snapshot"] = self._levels_snapshot_info
            return payload
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("PRICE LEVELS -- context build failed (%s).", exc)
            return {"status": f"FAILED:{type(exc).__name__}", "error": str(exc)}

    def _await_market_open(self) -> None:
        """AUTHORITATIVE, in-process market-hours gate for the ONE unit that
        places orders.

        WHY (operator directive, 2026-08-19): the capture units were moved to
        a 09:14 fire + wait-for-open so the first sample lands at 09:15:01.
        This unit was left at 09:22:30, so Bujji was ASLEEP for the first 7.5
        minutes of every live session. Moving its timer earlier is only safe
        once the timer stops being the sole market-hours protection -- this
        unit connects the broker and pulls a live chain the instant it
        starts, so a pre-open fire needs a real gate here, not a schedule.

        Two outcomes, both honest:
          - close to the open -> WAIT, then proceed at the open instant.
          - far from the open -> REFUSE (ConfigurationError), never a session
            against a closed-market book.

        The upper bound is DERIVED from the session's own configured end
        (continuous.observe_until, else management.monitor_until) rather than
        introducing a fourth market-close constant -- that split is a known,
        separately-tracked defect and this gate does not add to it.
        """
        import datetime as _dt
        import time as _time

        from bujji.market_reality.open_wait import wait_until_open

        if self._session_cfg.get("skip_market_hours_check"):
            # Explicit, loud, and never a default. Replay and unit tests run
            # at arbitrary wall-clock times and exercise lifecycle mechanics,
            # not market hours. A test asserts no installed systemd unit
            # passes the flag that sets this.
            self._logger.warning(
                "MARKET_HOURS_GATE BYPASSED (skip_market_hours_check) -- replay/testing only. "
                "A live session must never run with this set.")
            return

        def _as_time(text, field):
            parts = [int(x) for x in str(text).split(":")]
            while len(parts) < 3:
                parts.append(0)
            try:
                return _dt.time(*parts[:3])
            except ValueError as exc:
                raise ConfigurationError(f"{field}={text!r} is not a valid time: {exc}") from exc

        market_open = _as_time(self._session_cfg.get("market_open", "09:15:00"), "session.market_open")
        # OWN SLOT IN THE OPENING SECONDS. Three units now wake at 09:14 and
        # release at the open; without per-unit offsets their startup bursts
        # would stack against the shared 10/s FYERS ceiling -- the same
        # collision the capture scripts avoid with +0/+2/+5s. This unit is
        # the heaviest starter (broker connect + full chain pull), so it
        # takes the last slot: still inside the opening seconds, never first.
        open_offset = float(self._session_cfg.get("market_open_offset_seconds", 0.0))

        if not wait_until_open(now_fn=self._clock, market_open=market_open,
                               open_offset_seconds=open_offset,
                               sleep_fn=_time.sleep, logger=self._logger):
            raise ConfigurationError(
                f"REFUSING TO START: the market open ({market_open.isoformat()}) is not "
                f"imminent (now={self._clock().time().isoformat()}). This unit places orders "
                f"and pulls a live chain on start; it will not run against a closed-market "
                f"book. This is the expected outcome of a stray or far-from-open start.")

        ccfg = self._session_cfg.get("continuous")
        if isinstance(ccfg, dict):
            session_end = _as_time(ccfg.get("observe_until", "15:30"), "session.continuous.observe_until")
        else:
            mgmt = self._session_cfg.get("management") or {}
            session_end = _as_time(mgmt.get("monitor_until", "15:15:00"), "session.management.monitor_until")

        now_t = self._clock().time()
        if now_t >= session_end:
            raise ConfigurationError(
                f"REFUSING TO START: now={now_t.isoformat()} is at or past this session's "
                f"configured end ({session_end.isoformat()}). A late start would connect the "
                f"broker after the book stops being tradeable.")
        self._logger.info("MARKET_HOURS_GATE -- open=%s(+%.0fs) end=%s now=%s: cleared.",
                          market_open.isoformat(), open_offset, session_end.isoformat(),
                          now_t.isoformat())

    def _pre_market_check(self) -> None:
        self._stage = RunnerStage.PRE_MARKET_CHECK
        self._logger.info("PRE_MARKET_CHECK")

        # Market-hours gate FIRST: before the broker connects, before any live
        # chain is pulled. The timer is now only the coarse first net.
        self._await_market_open()

        import asyncio
        asyncio.run(self._broker.connect())

        # Fail closed HERE, before the session ever starts, rather than
        # discovering a missing input mid-session. get_trend_regime()/
        # get_volatility_regime() already raise MissingRegimeInputError
        # on their own -- this call surfaces that as early as possible.
        _trend, _vol = self._regime_provider.get_regime()
        self._persist_thesis(_trend, _vol)

        try:
            self._market_data_provider.get_option_chain(self._as_of_date)
        except Exception as exc:  # noqa: BLE001 -- re-raise as ConfigurationError, uniform exit code
            raise ConfigurationError(f"market data provider failed pre-market check: {exc}") from exc

        self._load_price_levels()
        self._recorder.record_session_start(started_at=self._clock(), initial_state="ENTRY_ENABLED")
        from bujji.shadow_observatory.models import SessionManifest
        self._recorder.record_session_manifest(SessionManifest(
            session_id=self._session_id, start_time=self._clock().isoformat(), mode="OPTIONS_OS_SHADOW_PHASE1",
            strategy_engine="msi_trade_construction", risk_engine="D.1-D.6", broker="PaperBroker",
            code_version=None, config_hash=None, market="NSE_FO", symbols=(self._session_cfg.get("underlying", "NIFTY"),),
        ))

    def _market_session(self) -> None:
        self._stage = RunnerStage.MARKET_SESSION
        self._logger.info("MARKET_SESSION -- observing.")
        self._governor.begin_market_analysis()

    def _continuous_session(self) -> None:
        """Bujji lives through the WHOLE market day (operator directive
        2026-08-19): mandatory close is a rule about POSITIONS, never about
        the organism. The single-shot session sampled the stability gate at
        one instant and exited; this loop maintains a rolling evidence
        window all day, re-derives the regime every decision cycle, attempts
        entry WHENEVER evidence stabilises (one strategy per day, unchanged,
        enforced by the governor's own lock), manages any position via the
        existing loop (including the emergency brake), and after any close --
        target, stop, emergency, or 15:15 mandatory -- keeps OBSERVING until
        the market ends. Every phase is built from the already-tested
        pieces: poll_spot_series, assess_stability, replay_snapshots, the
        extracted _attempt_entry, and _position_management."""
        import asyncio
        import datetime as _dt

        from bujji.market_state_builder.recovery import replay_snapshots
        from bujji.regime_stability import WarmupPlan, assess_stability, poll_spot_series

        self._stage = RunnerStage.ENTRY_WINDOW
        ccfg = self._session_cfg.get("continuous")
        ccfg = ccfg if isinstance(ccfg, dict) else {}
        poll_interval = max(float(ccfg.get("evidence_poll_interval_seconds", 30.0)), 0.01)
        decision_interval = float(ccfg.get("decision_interval_seconds", 300.0))
        window = int(ccfg.get("evidence_window_polls", 16))
        strides = tuple(ccfg.get("strides", (1, 2, 4)))
        # Hard cycle cap is the final authority on termination, same rule as
        # the management loop -- a mis-set clock can never spin forever.
        max_cycles = int(ccfg.get("max_cycles", 96))

        def _parse_t(key, default):
            h, m = str(ccfg.get(key, default)).split(":")[:2]
            return _dt.time(int(h), int(m))

        entry_cutoff = _parse_t("entry_cutoff", "14:30")
        observe_until = _parse_t("observe_until", "15:30")
        polls_per_cycle = max(1, int(decision_interval // poll_interval))
        # PHASE, not start time. The 09:22:30 timer bought burst separation
        # from the shadow campaign's :15/:20/:25 beat by starting LATE --
        # which cost 7.5 minutes of live market. That separation is a
        # property of the DECISION cadence, so it belongs here: Bujji wakes
        # at the open and collects evidence from the first tick, while the
        # first decision cycle is lengthened by the offset so every
        # chain-pulling burst still lands off the campaign's beat. The
        # evidence polls are single-call and do not collide.
        phase_offset = max(float(ccfg.get("decision_phase_offset_seconds", 0.0)), 0.0)
        first_cycle_extra_polls = int(round(phase_offset / poll_interval))
        if first_cycle_extra_polls:
            self._logger.info(
                "Continuous decision cadence phase-offset by %.0fs (+%d polls on cycle 1) to keep "
                "chain bursts off the shadow campaign's beat.", phase_offset, first_cycle_extra_polls)
        # The STRIDES belong to the stability assessment over the trailing
        # WINDOW (which must support them); the per-cycle poll batch is just
        # data collection and validates against stride (1,) only. Conflating
        # the two made every production cycle's 10-poll batch fail the
        # window's stride-4 requirement (caught by the test suite before it
        # ever ran live).
        from bujji.regime_stability.warmup import MIN_OBSERVATIONS_PER_STRIDE
        if polls_per_cycle < MIN_OBSERVATIONS_PER_STRIDE:
            raise ConfigurationError(
                f"continuous.decision_interval_seconds/{'{'}evidence_poll_interval_seconds{'}'} "
                f"yields {polls_per_cycle} polls per cycle; the gate needs >= "
                f"{MIN_OBSERVATIONS_PER_STRIDE}. Widen the decision interval or narrow polling.")
        widest = max(strides)
        if window < widest * MIN_OBSERVATIONS_PER_STRIDE:
            raise ConfigurationError(
                f"continuous.evidence_window_polls={window} cannot support stride "
                f"{widest} (needs >= {widest * MIN_OBSERVATIONS_PER_STRIDE}).")

        broker = self._intelligence_broker
        snapshots: list = []
        entered = False
        cycles = 0
        self._governor_result_summary["continuous_mode"] = True
        trail = self._governor_result_summary.setdefault("continuous_evidence", [])

        def _derive(win):
            outcome = replay_snapshots(win)
            if outcome.last_assessment is None:
                return None
            state = outcome.last_assessment.price_structure.structure_state
            return None if state == "UNKNOWN" else state

        while cycles < max_cycles:
            now = self._clock()
            if now.time() >= observe_until:
                break
            cycles += 1
            plan = WarmupPlan(
                polls=polls_per_cycle + (first_cycle_extra_polls if cycles == 1 else 0),
                interval_seconds=poll_interval, strides=(1,))
            snapshots.extend(poll_spot_series(
                fetch_spot=lambda: asyncio.run(broker.get_spot(self._root.underlying)),
                clock=self._clock, plan=plan, logger=self._logger,
            ))
            del snapshots[:-window]
            if snapshots:
                self._last_spot = getattr(snapshots[-1], "spot", None) or getattr(
                    snapshots[-1], "price", None)
            verdict = assess_stability(snapshots, _derive, strides=strides) if len(snapshots) >= window else None
            trail.append({
                "cycle": cycles, "at": now.isoformat(), "spots": len(snapshots),
                "stable": None if verdict is None else verdict.is_stable,
                "reason": None if verdict is None else verdict.reason,
            })
            if now.time() >= entry_cutoff:
                continue  # past the entry cutoff: observation only, all day
            if verdict is None or not verdict.is_stable:
                continue
            try:
                # D-5: the derivation record carries the cycle number and the
                # stability verdict that authorised this derivation, so a
                # day's file reads as the organism's reasoning over time.
                self._pending_cycle = cycles
                self._pending_stability = {"is_stable": verdict.is_stable, "reason": verdict.reason,
                                           "spots_in_window": len(snapshots)}
                self._regime_provider = self._build_market_thesis_regime_provider(
                    seeded_memory=replay_snapshots(snapshots).memory)
                trend_regime, volatility_regime = self._regime_provider.get_regime()
                self._persist_thesis(trend_regime, volatility_regime)
            except Exception as exc:  # noqa: BLE001 -- one failed derivation never ends the day
                self._logger.warning("continuous cycle %d: regime derivation failed (%s) -- observing on.",
                                     cycles, exc)
                continue
            self._logger.info("CONTINUOUS cycle %d -- STABLE evidence, regime trend=%s vol=%s",
                              cycles, trend_regime, volatility_regime)
            if self._attempt_entry(trend_regime, volatility_regime):
                entered = True
                break

        if entered:
            self._position_management()

        # Post-trade / post-cutoff observation: the position may be closed;
        # Bujji is not. The organism watches until the market ends.
        self._stage = RunnerStage.MARKET_SESSION
        phase = "POST_TRADE_OBSERVATION" if entered else "OBSERVATION"
        while cycles < max_cycles:
            now = self._clock()
            if now.time() >= observe_until:
                break
            cycles += 1
            plan = WarmupPlan(polls=polls_per_cycle, interval_seconds=poll_interval, strides=(1,))
            snapshots.extend(poll_spot_series(
                fetch_spot=lambda: asyncio.run(broker.get_spot(self._root.underlying)),
                clock=self._clock, plan=plan, logger=self._logger,
            ))
            del snapshots[:-window]
            trail.append({"cycle": cycles, "at": now.isoformat(), "phase": phase,
                          "spots": len(snapshots)})
        self._governor_result_summary["continuous_cycles"] = cycles

    def _entry_window(self) -> None:
        self._stage = RunnerStage.ENTRY_WINDOW
        trend_regime, volatility_regime = self._regime_provider.get_regime()
        self._logger.info("ENTRY_WINDOW -- regime trend=%s volatility=%s", trend_regime, volatility_regime)
        self._attempt_entry(trend_regime, volatility_regime)

    def _attempt_entry(self, trend_regime, volatility_regime) -> bool:
        """One complete entry attempt: selection -> Gate B'd risk pipeline ->
        fills -> registry -> canonical lifecycle. Returns True only when a
        position is actually OPEN. Shared verbatim by the single-shot entry
        window and the continuous session loop -- one entry path, two clocks."""
        selection = self._governor.select_and_lock_strategy(trend_regime, volatility_regime)
        self._governor_result_summary["strategy_selected"] = selection.selected_strategy
        if selection.selected_strategy is None:
            self._logger.info("No strategy for regime (trend=%s, vol=%s) -- no entry this cycle.",
                              trend_regime, volatility_regime)
            return False

        chain = self._market_data_provider.get_option_chain(self._as_of_date)
        spot = self._market_data_provider.get_spot()

        # PART 2 (shadow): record what the parity-based IV derivation WOULD
        # have chosen, beside what the canonical engine actually chooses.
        # Changes nothing -- see _record_iv_divergence.
        self._record_iv_divergence(chain, spot)

        # D-7: tell the paper broker what the market actually looks like
        # BEFORE any order is placed. Without this, every fill fell back to
        # the leg's own premium -- a short strangle was opened and closed at
        # the identical mid, so the bid-ask cost nothing and paper P&L was
        # optimistic by the full spread on every leg of every trade.
        self._sync_paper_market(chain)

        from bujji.trading_brain.risk_governor.capital_safety_governor import ProposedTradeEffect
        session_cfg = self._session_cfg
        proposed = session_cfg.get("proposed_trade_effect", {})

        cycle_result, entry_decision = self._governor.attempt_entry(
            chain=chain, spot=spot, as_of_date=self._as_of_date, timestamp=self._clock().isoformat(),
            desired_quantity=int(session_cfg.get("desired_quantity", 1)),
            requested_risk=float(session_cfg.get("requested_risk", 5000.0)),
            proposed_trade_effect=ProposedTradeEffect(
                additional_margin=proposed.get("additional_margin", 10000.0),
                additional_max_loss=proposed.get("additional_max_loss", 5000.0),
            ),
            contracts_by_client_order_id={}, sides_by_client_order_id={},
            reference_prices_by_client_order_id={}, risk_by_position_group_id={},
            direction=session_cfg.get("direction"), expected_move_pct=session_cfg.get("expected_move_pct"),
        )
        self._governor_result_summary["entry_allowed"] = entry_decision.allowed
        self._governor_result_summary["entry_filled"] = cycle_result.filled if cycle_result else False

        if not cycle_result or not cycle_result.filled:
            reason = cycle_result.governor_result.blocking_stage if cycle_result and cycle_result.governor_result else "not constructed"
            self._logger.info("Entry did not fill (reason=%s).", reason)
            return False

        from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract
        contracts = {}
        entry_prices: Dict[str, float] = {}
        for order_result in cycle_result.order_results:
            order_id = getattr(order_result, "client_order_id", None)
            if order_id:
                self._execution_order_ids.append(order_id)
        for leg, order_result in zip(cycle_result.proposal.legs, cycle_result.order_results):
            contract = _leg_to_core_contract(leg, self._root.underlying, self._root.exchange_lot_size)
            contracts[contract.symbol] = contract
            entry_prices[contract.symbol] = order_result.average_price

        pg_id = self._governor._position_group_id
        self._registry.register_entry(
            pg_id, cycle_result.proposal.strategy_family, list(contracts.keys()),
            float(session_cfg.get("requested_risk", 5000.0)), self._clock, contracts=contracts,
        )
        self._lifecycle_runtime.mark_open(pg_id)
        self._entry_prices = entry_prices
        self._contracts_by_symbol = contracts
        self._logger.info("Position OPEN: %s -- legs=%s", pg_id, list(contracts.keys()))

        # Record the entry in the CANONICAL lifecycle. Fill prices are
        # positional, matching the same zip() over proposal.legs and
        # order_results used above -- never keyed by leg.role, which
        # collides on an IRON_CONDOR's two SHORT legs.
        self._record_canonical_entry(
            pg_id, cycle_result, spot, trend_regime, selection,
        )

        return True

    def _record_iv_divergence(self, chain, spot) -> None:
        """Measure the two IV derivations against each other, at the exact
        moment strike selection is about to run.

        WHY THIS IS A RECORD AND NOT A SWAP. Strike selection ranks strikes by
        |delta - target|, and the two derivations produce different deltas:
        the canonical engine uses spot-based Black-Scholes with an ASSUMED
        6.5% rate, while bujji.options_analytics recovers the forward from the
        chain itself by put-call parity and assumes no rate at all. Measured
        over real captured chains they disagree on the chosen strike in
        roughly a quarter of comparisons, always in the same direction.

        Swapping them therefore changes WHICH STRIKES GET SOLD -- the
        canonical strategy authority, and an operator decision. This method
        accumulates the evidence for that decision on every real entry
        attempt and influences nothing.

        Never raises: diagnostics must not be able to end a trading session.
        """
        try:
            from bujji.msi_trade_construction import config as _mtc_config
            from bujji.options_analytics import compare_derivations
            from bujji.options_analytics.black76 import time_to_expiry_years

            if spot is None or not chain:
                return
            now = self._clock().isoformat()
            expiries = {getattr(row, "expiry", None) for row in chain}
            expiries.discard(None)
            if not expiries:
                return
            # The nearest expiry -- the one a weekly premium seller trades.
            expiry = min(expiries)
            t_years = time_to_expiry_years(now, expiry)
            if not t_years:
                return

            rows = [row for row in chain if getattr(row, "expiry", None) == expiry]
            dicts = [{"strike": getattr(row, "strike", None),
                      "option_type": getattr(row, "option_type", None),
                      "ltp": getattr(row, "close", None),
                      "bid": getattr(row, "bid", None),
                      "ask": getattr(row, "ask", None)} for row in rows]

            divergence = compare_derivations(
                rows=rows, chain_dicts=dicts, expiry=expiry, spot=float(spot),
                t_years=t_years, as_of=now,
                assumed_rate=_mtc_config.DEFAULT_RISK_FREE_RATE)

            trail = self._governor_result_summary.setdefault("iv_divergence", [])
            trail.append(divergence.to_dict())
            self._logger.info(
                "IV DIVERGENCE -- expiry=%s strikes_agree=%s disagreements=%d "
                "median|dIV|=%s max|d delta|=%s",
                expiry, divergence.strikes_agree, divergence.disagreements,
                None if divergence.median_abs_iv_diff is None
                else round(divergence.median_abs_iv_diff, 5),
                None if divergence.max_abs_delta_diff is None
                else round(divergence.max_abs_delta_diff, 4))
        except Exception as exc:  # noqa: BLE001 -- diagnostics never end a session
            self._logger.warning("IV DIVERGENCE record failed (%s) -- session continues.", exc)

    def _sync_paper_market(self, chain) -> None:
        """Push the real observed top-of-book, and the real capital, into
        the simulated broker.

        Never raises into the session: a sync failure must not kill a run.
        But it is never SILENT either -- coverage is logged and recorded in
        the session summary, because "quotes applied" that quietly applied
        nothing is precisely the failure this phase exists to remove. Zero
        coverage against a non-empty chain is logged as a WARNING: it means
        fills are still frictionless and the operator should know."""
        from bujji.production_runtime.paper_market_sync import sync_capital, sync_quotes_from_chain

        try:
            report = sync_quotes_from_chain(self._broker, chain, self._root.underlying)
            capital_applied = False
            snapshot = None
            provider = getattr(self._root, "capital_snapshot_provider", None)
            if provider is not None:
                try:
                    snapshot = provider()
                except Exception as exc:  # noqa: BLE001 -- capital reality is Gate B's authority, not this sync's
                    self._logger.warning("PAPER_MARKET_SYNC -- capital snapshot unavailable (%s); "
                                         "broker capital left unchanged.", exc)
                capital_applied = sync_capital(self._broker, snapshot)

            summary = report.as_dict()
            summary["capital_applied"] = capital_applied
            self._governor_result_summary["paper_market_sync"] = summary

            if report.rows_seen and report.quotes_applied == 0:
                self._logger.warning(
                    "PAPER_MARKET_SYNC -- 0 of %d chain rows produced a usable two-sided quote "
                    "(no_bid=%d no_ask=%d bad_key=%d). Fills remain FRICTIONLESS: every leg will "
                    "fill at its own reference premium.",
                    report.rows_seen, report.skipped_no_bid, report.skipped_no_ask,
                    report.skipped_unusable_key)
            else:
                self._logger.info(
                    "PAPER_MARKET_SYNC -- quotes=%d/%d depth=%d capital=%s",
                    report.quotes_applied, report.rows_seen, report.depth_applied, capital_applied)
        except Exception as exc:  # noqa: BLE001 -- a simulation-realism sync must never end a session
            self._logger.warning("PAPER_MARKET_SYNC failed (%s) -- continuing with reference-price fills.", exc)
            self._governor_result_summary["paper_market_sync"] = {"error": f"{type(exc).__name__}: {exc}"}

    def _record_canonical_entry(self, pg_id, cycle_result, spot, trend_regime, selection) -> None:
        """Open the canonical PositionLifecycle for a real, already-filled
        entry. Never raises into the session: a bookkeeping failure must
        not kill a live trading run that has real money-shaped state
        open, so it is logged and the session continues -- the loss is a
        missing learning record, which is strictly better than an
        abandoned open position."""
        from bujji.production_runtime.lifecycle_outcome_bridge import open_position
        try:
            fill_prices = [r.average_price for r in cycle_result.order_results]
            self._lifecycle_states, position_id, outcome = open_position(
                self._lifecycle_states, self._session_id,
                position_group_id=pg_id,
                strategy_family=cycle_result.proposal.strategy_family,
                legs=cycle_result.proposal.legs, fill_prices=fill_prices,
                entry_timestamp=self._clock().isoformat(),
                underlying_symbol=self._root.underlying,
                underlying_price=spot, lot_size=self._root.exchange_lot_size,
                market_regime=trend_regime,
                direction=self._session_cfg.get("direction"),
                thesis=getattr(selection, "reasoning", None),
                selection_confidence=getattr(selection, "confidence", None),
            )
            self._canonical_position_id = position_id
            self._governor_result_summary["canonical_position_id"] = position_id
            self._logger.info("Canonical lifecycle OPEN: position_id=%s outcome=%s", position_id, outcome)
        except Exception as exc:  # noqa: BLE001 -- see docstring.
            self._logger.exception("canonical lifecycle entry failed (session continues): %s", exc)

    def _run_one_management_pass(self, stage_label: str) -> None:
        if not self._entry_prices:
            return
        import asyncio
        from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import PositionHealthThresholds

        pg_id = self._governor._position_group_id
        as_of = self._clock().isoformat()
        prices, priced_from_ticks = self._current_leg_prices(as_of)
        if priced_from_ticks:
            self._priced_from_ticks_cycles += 1
            self._consecutive_blind_cycles = 0
        else:
            # LOUD, every time. A blind cycle is a cycle where this
            # position was revalued against its own ENTRY prices, so
            # unrealized P&L is 0 by construction and no stop, target or
            # thesis-invalidation could possibly fire. Historically this
            # degraded silently and a fully blind session was
            # indistinguishable from a healthy one in the logs.
            self._blind_cycles += 1
            # The blind-brake counts only cycles where a tick source EXISTS
            # and failed to price the book -- that is the dangerous state
            # (we expected sight and lost it). A session with NO tick source
            # configured is blind BY DESIGN (replay dates with no captured
            # ticks -- see _current_leg_prices' own docstring) and keeps the
            # long-established flagged-not-terminated behaviour.
            if self._price_provider is not None:
                self._consecutive_blind_cycles = getattr(self, "_consecutive_blind_cycles", 0) + 1
            self._logger.warning(
                "%s -- BLIND CYCLE: revalued from ENTRY prices, not market prices. "
                "Unrealized P&L is 0 by construction; stop-loss/profit-target CANNOT fire "
                "this cycle. (blind=%d priced_from_ticks=%d)",
                stage_label, self._blind_cycles, self._priced_from_ticks_cycles,
            )
        ts_map = {symbol: as_of for symbol in prices}
        valuations = asyncio.run(
            self._portfolio_engine.revalue_all(prices, self._clock, price_timestamps=ts_map)
        )
        valuation = valuations.get(pg_id)
        if valuation is not None and self._canonical_position_id is not None and priced_from_ticks:
            # Capture the excursion, pass by pass -- but ONLY for cycles
            # priced from real market data. A blind cycle's unrealized
            # P&L is 0 because the position was compared against its own
            # entry price, not because the market did not move; banking
            # that 0 would turn "we never looked" into "it never moved"
            # and produce an MFE/MAE that reads as measured when nothing
            # was measured. `None` from a partially-priced group is still
            # preserved -- unknown, not flat -- and compute_mfe_mae drops it.
            self._valuation_history.append(getattr(valuation, "total_unrealized_pnl", None))
        if valuation is None:
            self._logger.warning("%s -- no valuation available for %s", stage_label, pg_id)
            return

        # -- EMERGENCY BRAKE (Master Plan D-6) -- evaluated every pass,
        # BEFORE the ordinary exit policy, reusing the same mandatory
        # close-everything sequence _eod_close uses. Nothing new fires an
        # order; the brake only decides WHEN the existing close sequence runs.
        brake_reason = _emergency_brake(
            unrealized_pnl=getattr(valuation, "total_unrealized_pnl", None),
            realized_pnl=getattr(self._broker, "realized_pnl", 0.0),
            daily_loss_limit=self._config.get("capital_snapshot", {}).get("daily_loss_limit"),
            consecutive_blind_cycles=getattr(self, "_consecutive_blind_cycles", 0),
            max_consecutive_blind_cycles=self._config.get("position_management", {}).get(
                "max_consecutive_blind_cycles", 3),
        )
        if brake_reason is not None:
            self._logger.critical("%s -- EMERGENCY CLOSE: %s", stage_label, brake_reason)
            self._governor_result_summary["emergency_close_reason"] = brake_reason
            self._emergency_closed = True
            self._trading_brain_runtime.run_market_close_sequence()
            return

        # Snapshot the group's open positions BEFORE the exit runs. The
        # executor iterates exactly this list to build its reduce orders,
        # and `orders_submitted` comes back in the same order -- but by
        # the time the exit has executed, those positions are flat and
        # the registry returns nothing, so reading it afterwards yields
        # an empty list and every real exit fill is silently lost.
        positions_before_exit = asyncio.run(self._registry.positions_for_group(pg_id))
        symbols_before_exit = [p["symbol"] for p in positions_before_exit]

        selected = self._governor_result_summary.get("strategy_selected")
        result = asyncio.run(self._governor.evaluate_and_enforce_exit(
            valuation, selected, None, None, PositionHealthThresholds(),
            float(self._session_cfg.get("requested_risk", 5000.0)),
        ))
        self._logger.info(
            "%s -- D.4 action=%s exit_policy=%s forced_execution=%s",
            stage_label, result.lifecycle_evaluation.recommendation.action,
            result.policy_decision.decision, result.forced_execution is not None,
        )
        self._governor_result_summary.setdefault("management_passes", []).append({
            "stage": stage_label, "decision": result.policy_decision.decision,
            "forced_execution": result.forced_execution is not None,
        })
        self._capture_exit_fills(symbols_before_exit, result)


    def _current_leg_prices(self, as_of: str):
        """Live per-leg prices for this cycle, and whether they are real
        ticks. Returns `(prices, priced_from_ticks)`.

        Without a price provider this returns the ENTRY prices, which is
        the pre-tick-feed behaviour: unrealized P&L reads flat all
        session. That fallback is deliberate and logged rather than
        removed -- a replay date with no captured ticks genuinely has no
        intraday evidence, and inventing some would be worse than
        reporting flat. `priced_from_ticks` records which happened, so a
        session's MFE/MAE can never be mistaken for measured excursion
        when it was only ever the entry price echoed back.

        A leg the provider cannot price is dropped rather than backfilled
        from its entry price: `revalue()` already refuses to value a group
        whose legs are not all priced, and a half-real valuation is worse
        than an honestly absent one.
        """
        if self._price_provider is None or not self._contracts_by_symbol:
            return dict(self._entry_prices), False
        try:
            ticked = self._price_provider.get_prices(self._contracts_by_symbol, as_of)
        except Exception as exc:  # noqa: BLE001 -- market data never kills a session.
            self._logger.exception("tick provider failed, falling back to entry prices: %s", exc)
            return dict(self._entry_prices), False

        priced = {sym: px for sym, px in ticked.items() if px is not None}
        if len(priced) != len(self._entry_prices):
            self._logger.warning(
                "tick feed priced %d/%d legs at %s -- falling back to entry prices for this cycle "
                "(a partially-priced group cannot be valued honestly).",
                len(priced), len(self._entry_prices), as_of,
            )
            return dict(self._entry_prices), False
        return priced, True

    def _capture_exit_fills(self, exit_symbols, result) -> None:
        """Harvest REAL per-leg exit prices from an execution that
        actually happened. `orders_submitted` is appended inside the
        executor's own loop over `positions_for_group(pg_id)`, in that
        same order, so index i of each corresponds -- see
        `lifecycle_outcome_bridge.map_exit_fills_to_legs`.

        `exit_symbols` MUST be the pre-exit snapshot taken by the caller:
        after the exit executes those positions are flat and the registry
        returns an empty list, which would silently drop every real exit
        fill and leave the outcome record with no P&L.

        Accumulates across passes: a position can be reduced in one pass
        and closed in another, and each leg's latest real exit price is
        what the outcome record should carry. Never raises into the
        session (same reasoning as `_record_canonical_entry`)."""
        from bujji.production_runtime.lifecycle_outcome_bridge import map_exit_fills_to_legs

        execution = getattr(result, "forced_execution", None)
        if execution is None or not getattr(execution, "orders_submitted", ()):
            return
        if self._canonical_position_id is None:
            return
        lifecycle = self._lifecycle_states.get(self._canonical_position_id)
        if lifecycle is None:
            return
        try:
            mapped = map_exit_fills_to_legs(
                lifecycle, exit_symbols, execution.orders_submitted, self._contracts_by_symbol,
            )
            self._exit_prices_by_leg.update(mapped)
            for submitted in execution.orders_submitted:
                order_id = getattr(submitted, "client_order_id", None)
                if order_id:
                    self._execution_order_ids.append(order_id)
            self._logger.info("Captured %d real exit fill(s) for %s", len(mapped), self._canonical_position_id)
        except Exception as exc:  # noqa: BLE001 -- bookkeeping never kills a live session.
            self._logger.exception("exit-fill capture failed (session continues): %s", exc)

    def _position_management(self) -> None:
        """Monitor the open position ACROSS the session, not once.

        Previously this fired a single pass, so a position was revalued
        exactly twice per session (here and at EOD). With a tick source
        wired, two samples cannot express an excursion: stop-loss,
        profit-target and thesis-invalidation are all evaluated against
        price, and price only moves between passes. The loop is what
        makes those reachable -- none of the risk logic changes.

        Bounded by real clock time and a hard cycle cap, so a mis-set
        clock or a stuck provider can never spin: the cap, not the
        wall-clock, is the final authority on termination.
        """
        self._stage = RunnerStage.POSITION_MANAGEMENT
        mgmt_cfg = self._config.get("position_management", {})
        interval_s = int(mgmt_cfg.get("cycle_interval_seconds", 300))
        end_time_s = mgmt_cfg.get("monitor_until", "15:15:00")
        max_cycles = int(mgmt_cfg.get("max_cycles", 78))  # 6h15m / 5min, one session's worth.

        if not self._entry_prices:
            self._logger.info("POSITION_MANAGEMENT -- no open position; nothing to monitor.")
            return

        import time as _time
        end_t = dt_time.fromisoformat(end_time_s)
        cycles = 0
        self._logger.info(
            "POSITION_MANAGEMENT -- monitoring every %ds until %s (max %d cycles).",
            interval_s, end_time_s, max_cycles,
        )
        while cycles < max_cycles:
            if getattr(self, "_emergency_closed", False):
                self._logger.critical("POSITION_MANAGEMENT halted: emergency close executed.")
                break
            self._run_one_management_pass(f"POSITION_MANAGEMENT[{cycles + 1}]")
            cycles += 1
            if not self._entry_prices:
                self._logger.info("Position closed during management -- ending monitoring loop.")
                break
            now_t = self._clock().time()
            if now_t >= end_t:
                self._logger.info("Reached monitor_until=%s -- ending monitoring loop.", end_time_s)
                break
            if interval_s > 0:
                _time.sleep(interval_s)
        else:
            self._logger.warning(
                "POSITION_MANAGEMENT hit max_cycles=%d before monitor_until=%s -- "
                "monitoring stopped by the safety cap, not by the clock.", max_cycles, end_time_s,
            )
        self._governor_result_summary["management_cycles"] = cycles

    def _eod_close(self) -> None:
        self._stage = RunnerStage.EOD_CLOSE
        self._logger.info("EOD_CLOSE")
        self._run_one_management_pass("EOD_CLOSE")
        self._trading_brain_runtime.run_market_close_sequence()

    def _session_archive(self) -> None:
        self._stage = RunnerStage.SESSION_ARCHIVE
        self._governor.end_session()
        self._governor_result_summary["final_session_state"] = self._governor.state.value

        realized = 0.0
        for symbol in self._entry_prices:
            realized += self._broker.get_realized_pnl(symbol)
        self._recorder.finalize_session(final_positions=(), realized_pnl=realized, unrealized_pnl=0.0)
        self._governor_result_summary["realized_pnl"] = realized
        self._governor_result_summary["cycles_priced_from_ticks"] = self._priced_from_ticks_cycles
        self._governor_result_summary["cycles_blind"] = self._blind_cycles
        if self._priced_from_ticks_cycles == 0 and self._blind_cycles > 0:
            # The single most dangerous silent state: a session that
            # entered, exited and recorded an outcome without ever seeing
            # a market price move. Everything downstream (MFE/MAE, thesis
            # accuracy, management quality) is uninformative for this
            # session and must not be pooled with sighted sessions.
            self._governor_result_summary["session_blind"] = True
            self._logger.error(
                "SESSION WAS BLIND: %d/%d cycles revalued from entry prices, 0 from market data. "
                "MFE/MAE and any management-quality inference from this session are NOT evidence. "
                "Wire providers.tick_source before trusting this session's learning record.",
                self._blind_cycles, self._blind_cycles,
            )
        self._logger.info("SESSION_ARCHIVE -- final_state=%s realized_pnl=%s",
                           self._governor.state.value, realized)
        self._close_canonical_lifecycle()

    def _close_canonical_lifecycle(self) -> None:
        """The final two hops this runner never reached before: close the
        canonical lifecycle with real exit evidence, attribute the
        outcome, and write the durable OutcomeMemoryRecord that a future
        learning system reads.

        A leg with no captured exit price degrades to PNL_UNKNOWN inside
        `build_structured_exit` rather than being assigned an assumed
        price -- an incomplete-but-true record, never an invented one.
        Never raises into the session."""
        from bujji.production_runtime.lifecycle_outcome_bridge import (
            attribute_and_remember, close_position,
        )
        if self._canonical_position_id is None:
            return
        if not self._exit_prices_by_leg:
            # The position never actually exited -- no management pass
            # produced a real fill (e.g. no mandatory_exit_time is
            # configured and no stop/target was hit). Closing the
            # canonical lifecycle here anyway would record a CLOSED
            # position, and an outcome memory for it, describing an exit
            # that never happened. Leave it OPEN and say so: an absent
            # record is recoverable, a fictional one silently poisons
            # every future statistic computed over the campaign.
            self._logger.warning(
                "Position %s never exited (no real exit fills captured) -- leaving canonical "
                "lifecycle OPEN and recording NO outcome memory.", self._canonical_position_id,
            )
            self._governor_result_summary["canonical_close_outcome"] = "NEVER_EXITED"
            return
        try:
            exit_ts = self._clock().isoformat()

            # D-8: the broker computed real charges and slippage on every
            # fill and filed them in its execution reports; close_position
            # has accepted fees=/slippage= all along and nobody connected
            # them, so every outcome recorded a GROSS result as if it were
            # net. Absent measurements stay None -- never 0.0, which would
            # claim the trade was free and be indistinguishable from one
            # that genuinely was.
            from bujji.production_runtime.execution_costs import collect_execution_costs

            costs = collect_execution_costs(self._broker, self._execution_order_ids)
            self._governor_result_summary["execution_costs"] = costs.as_dict()
            if not costs.is_complete:
                self._logger.warning(
                    "EXECUTION COSTS INCOMPLETE -- %d/%d orders reported charges "
                    "(missing_report=%d no_charges=%d); the recorded outcome is NOT fully net.",
                    costs.orders_with_report, costs.orders_seen,
                    costs.orders_missing_report, costs.orders_without_charges)
            else:
                self._logger.info("EXECUTION COSTS -- fees=%.2f slippage=%.2f across %d orders",
                                  costs.fees, costs.slippage or 0.0, costs.orders_seen)

            self._lifecycle_states, close_outcome = close_position(
                self._lifecycle_states, self._session_id, self._canonical_position_id,
                exit_timestamp=exit_ts, exit_reason="SESSION_END",
                exit_prices=self._exit_prices_by_leg,
                fees=costs.fees, slippage=costs.slippage,
            )
            self._governor_result_summary["canonical_close_outcome"] = close_outcome
            if close_outcome != "ACCEPTED":
                self._logger.warning("Canonical close not accepted: %s", close_outcome)
                return

            self._lifecycle_states, record, mem_outcome = attribute_and_remember(
                self._lifecycle_states, self._session_id, self._canonical_position_id,
                recorded_at=exit_ts, valuation_history=tuple(self._valuation_history),
            )
            self._outcome_memory_record = record
            self._governor_result_summary["outcome_memory_outcome"] = mem_outcome

            # D-8: the record used to stop here, in a dict that died with the
            # process -- Bujji forgot every trade it ever made. The store is
            # the SAME EventStore that outcome_memory.recovery already
            # replays cross-session; no new persistence mechanism.
            persisted = self._persist_outcome_memory(record, recorded_at=exit_ts)
            self._governor_result_summary["outcome_memory_persisted"] = persisted
            if record is not None:
                self._governor_result_summary["outcome_memory_id"] = record.memory_id
                self._logger.info(
                    "OUTCOME MEMORY recorded: id=%s direction=%s realized_pnl=%s",
                    record.memory_id, record.outcome_direction, record.realized_pnl,
                )
            else:
                self._logger.info("No outcome memory recorded (%s) -- honest skip, never speculative.", mem_outcome)
        except Exception as exc:  # noqa: BLE001 -- bookkeeping never kills a live session.
            self._logger.exception("canonical lifecycle close failed (session continues): %s", exc)

    def _persist_outcome_memory(self, record, *, recorded_at: str) -> str:
        """Append the record to the durable cross-session outcome store.

        Never raises into the session: this runs at the very end of a real
        trading day, and a write failure must not turn a completed, correctly
        closed position into a crashed session. It is loud on failure -- a
        forgotten trade is exactly the write-only-memory failure this phase
        exists to end."""
        from bujji.production_runtime.outcome_memory_writer import persist_outcome_memory

        try:
            store = self._outcome_memory_store()
            outcome = persist_outcome_memory(
                store, record, session_id=self._session_id, recorded_at=recorded_at)
            if outcome == "PERSISTED":
                self._logger.info("OUTCOME MEMORY persisted durably: id=%s -> %s",
                                  record.memory_id, getattr(store, "path", "?"))
            return outcome
        except Exception as exc:  # noqa: BLE001 -- a completed trade must not become a crashed session
            self._logger.exception("OUTCOME MEMORY persistence FAILED -- this trade will be "
                                   "forgotten (session continues): %s", exc)
            return f"FAILED:{type(exc).__name__}"

    def _outcome_memory_store(self):
        """The durable store, built lazily from config. Deliberately its OWN
        path: outcome memory is cross-session by design, so it must not live
        inside a per-session artifact directory that a reader would have to
        enumerate to reconstruct the campaign."""
        from bujji.state_persistence.store import EventStore

        existing = getattr(self, "_outcome_store", None)
        if existing is not None:
            return existing
        artifacts_cfg = self._config.get("artifacts", {}) or {}
        path = REPO_ROOT / artifacts_cfg.get(
            "outcome_memory_store_path", "data/outcome_memory_events.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._outcome_store = EventStore(str(path))
        return self._outcome_store

    def _shutdown(self) -> None:
        self._stage = RunnerStage.SHUTDOWN
        self._logger.info("SHUTDOWN -- stage_reached=%s", self._stage)
        # No auto-resume, no retry, no hidden recovery: this method only
        # logs and releases whatever was constructed in _startup(). A
        # future invocation of this runner always begins a brand new
        # session from STARTUP -- it never reads back a prior one.


def run(argv=None) -> int:
    from bujji.production_runtime.market_data_provider import MarketDataUnavailableError
    from bujji.production_runtime.regime_provider import MissingRegimeInputError

    args = parse_args(argv)
    from datetime import date as _date

    as_of_date = args.as_of_date or _date.today().isoformat()

    # Trading-day gate BEFORE any lock or broker construction: a
    # non-trading day is a clean, expected skip (exit 0), never a
    # fabricated session and never a reported failure.
    if not args.skip_calendar_check:
        from bujji.market_calendar import MarketCalendar

        calendar = MarketCalendar()
        warning = calendar.verification_warning()
        if warning:
            logging.getLogger("bujji-options-os-shadow").warning("%s", warning)
        is_trading, reason = calendar.is_trading_day(_date.fromisoformat(as_of_date))
        if not is_trading:
            logging.getLogger("bujji-options-os-shadow").info(
                "Not a trading day (%s): %s -- skipping cleanly.", as_of_date, reason)
            return EXIT_OK

    # Single-instance lock on its OWN path. Two concurrent trading
    # sessions would both believe they own the one-strategy-per-day lock
    # and could double the book.
    from bujji.core.process_lock import LockAcquisitionError, ProcessLock

    lock = ProcessLock(args.lock_path)
    try:
        lock.acquire()
    except LockAcquisitionError as exc:
        logging.getLogger("bujji-options-os-shadow").error("Refusing to start: %s", exc)
        return EXIT_RUNTIME_ERROR

    try:
        return _run_session(args, as_of_date)
    finally:
        lock.release()


def _run_session(args, as_of_date: str) -> int:
    from bujji.production_runtime.market_data_provider import MarketDataUnavailableError
    from bujji.production_runtime.regime_provider import MissingRegimeInputError

    try:
        config = load_config(Path(args.config))
        if args.bhavcopy_path:
            config.setdefault("providers", {}).setdefault("market_data", {})["bhavcopy_path"] = args.bhavcopy_path
        if getattr(args, "skip_market_hours_check", False):
            config.setdefault("session", {})["skip_market_hours_check"] = True
        if args.trend_regime:
            config.setdefault("providers", {}).setdefault("regime", {})["trend_regime"] = args.trend_regime
        if args.volatility_regime:
            config.setdefault("providers", {}).setdefault("regime", {})["volatility_regime"] = args.volatility_regime

        namespace = config.get("logging", {}).get("namespace", "bujji-options-os-shadow")
        logger = build_logger(namespace)

        session_id = args.session_id or f"OPTIONS_OS_{as_of_date}_{uuid.uuid4().hex[:8]}"
        runner = OptionsOSRunner(config=config, as_of_date=as_of_date, session_id=session_id, logger=logger)
        summary = runner.run()
        logger.info("Session complete: %s", summary)
        return EXIT_OK
    except (ConfigurationError, MissingRegimeInputError, MarketDataUnavailableError) as exc:
        logging.getLogger("bujji-options-os-shadow").error("Configuration/input failure: %s", exc)
        return EXIT_CONFIG_ERROR
    except Exception as exc:  # noqa: BLE001 -- process boundary, never let this propagate as a raw traceback exit
        logging.getLogger("bujji-options-os-shadow").exception("Runtime failure: %s", exc)
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
