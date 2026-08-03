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
    parser.add_argument("--as-of-date", required=True, help="YYYY-MM-DD -- the historical session date to replay.")
    parser.add_argument("--bhavcopy-path", default=None, help="Override providers.market_data.bhavcopy_path.")
    parser.add_argument("--trend-regime", default=None, help="Override regime.trend_regime.")
    parser.add_argument("--volatility-regime", default=None, help="Override regime.volatility_regime.")
    parser.add_argument("--session-id", default=None, help="Override the generated session_id.")
    return parser.parse_args(argv)


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

    def __init__(self, config: Dict[str, Any], as_of_date: str, session_id: str, logger: logging.Logger) -> None:
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

        self._entry_prices: Dict[str, float] = {}
        self._governor_result_summary: Dict[str, Any] = {}

    def _clock(self):
        from bujji.core.clock import now_ist
        return now_ist()

    # ------------------------------------------------------------ #
    # Lifecycle stages
    # ------------------------------------------------------------ #

    def run(self) -> Dict[str, Any]:
        try:
            self._startup()
            self._pre_market_check()
            self._market_session()
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
        exchange_lot_size = int(session_cfg.get("exchange_lot_size", 75))

        self._broker = PaperBroker()

        journal_path = REPO_ROOT / artifacts_cfg.get("journal_path", "data/options_os_shadow_position_group_journal.db")
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._journal = PositionGroupJournal(str(journal_path))

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
                timestamp=self._clock(),
            )

        self._root = build_trading_brain_composition_root(
            broker=self._broker, journal=self._journal, margin_provider=SimulatedMarginProvider(),
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
        bhavcopy_path = market_data_cfg.get("bhavcopy_path")
        if not bhavcopy_path:
            raise ConfigurationError(
                "providers.market_data.bhavcopy_path is required (config or --bhavcopy-path) -- refusing to guess."
            )
        self._market_data_provider = ReplayChainProvider(bhavcopy_path=bhavcopy_path, underlying=underlying)

        regime_cfg = providers_cfg.get("regime", {})
        self._regime_provider = HumanSuppliedRegimeProvider(
            trend_regime=regime_cfg.get("trend_regime"), volatility_regime=regime_cfg.get("volatility_regime"),
        )

        self._session_cfg = session_cfg

    def _pre_market_check(self) -> None:
        self._stage = RunnerStage.PRE_MARKET_CHECK
        self._logger.info("PRE_MARKET_CHECK")

        import asyncio
        asyncio.run(self._broker.connect())

        # Fail closed HERE, before the session ever starts, rather than
        # discovering a missing input mid-session. get_trend_regime()/
        # get_volatility_regime() already raise MissingRegimeInputError
        # on their own -- this call surfaces that as early as possible.
        self._regime_provider.get_regime()

        try:
            self._market_data_provider.get_option_chain(self._as_of_date)
        except Exception as exc:  # noqa: BLE001 -- re-raise as ConfigurationError, uniform exit code
            raise ConfigurationError(f"market data provider failed pre-market check: {exc}") from exc

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

    def _entry_window(self) -> None:
        self._stage = RunnerStage.ENTRY_WINDOW
        trend_regime, volatility_regime = self._regime_provider.get_regime()
        self._logger.info("ENTRY_WINDOW -- regime trend=%s volatility=%s", trend_regime, volatility_regime)

        selection = self._governor.select_and_lock_strategy(trend_regime, volatility_regime)
        self._governor_result_summary["strategy_selected"] = selection.selected_strategy
        if selection.selected_strategy is None:
            self._logger.info("No strategy selected for this regime -- NO_TRADE day. Ending session.")
            return

        chain = self._market_data_provider.get_option_chain(self._as_of_date)
        spot = self._market_data_provider.get_spot()

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
            self._logger.info("Entry did not fill (reason=%s). No position this session.", reason)
            return

        from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract
        contracts = {}
        entry_prices: Dict[str, float] = {}
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
        self._logger.info("Position OPEN: %s -- legs=%s", pg_id, list(contracts.keys()))

    def _run_one_management_pass(self, stage_label: str) -> None:
        if not self._entry_prices:
            return
        import asyncio
        from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import PositionHealthThresholds

        pg_id = self._governor._position_group_id
        ts_map = {symbol: self._clock().isoformat() for symbol in self._entry_prices}
        valuations = asyncio.run(
            self._portfolio_engine.revalue_all(self._entry_prices, self._clock, price_timestamps=ts_map)
        )
        valuation = valuations.get(pg_id)
        if valuation is None:
            self._logger.warning("%s -- no valuation available for %s", stage_label, pg_id)
            return

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

    def _position_management(self) -> None:
        self._stage = RunnerStage.POSITION_MANAGEMENT
        self._logger.info("POSITION_MANAGEMENT -- single-pass (Phase-1 has no intraday tick feed).")
        self._run_one_management_pass("POSITION_MANAGEMENT")

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
        self._logger.info("SESSION_ARCHIVE -- final_state=%s realized_pnl=%s",
                           self._governor.state.value, realized)

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
    try:
        config = load_config(Path(args.config))
        if args.bhavcopy_path:
            config.setdefault("providers", {}).setdefault("market_data", {})["bhavcopy_path"] = args.bhavcopy_path
        if args.trend_regime:
            config.setdefault("providers", {}).setdefault("regime", {})["trend_regime"] = args.trend_regime
        if args.volatility_regime:
            config.setdefault("providers", {}).setdefault("regime", {})["volatility_regime"] = args.volatility_regime

        namespace = config.get("logging", {}).get("namespace", "bujji-options-os-shadow")
        logger = build_logger(namespace)

        session_id = args.session_id or f"OPTIONS_OS_{args.as_of_date}_{uuid.uuid4().hex[:8]}"
        runner = OptionsOSRunner(config=config, as_of_date=args.as_of_date, session_id=session_id, logger=logger)
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
