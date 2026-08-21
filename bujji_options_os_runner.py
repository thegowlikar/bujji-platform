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


def _entry_failure_reason(cycle_result) -> str:
    """Why an entry did not fill -- the stage that ACTUALLY blocked it.

    This used to read only `governor_result.blocking_stage` and fall back to
    "not constructed" whenever `governor_result` was None -- which is exactly
    what a Gate B veto produces. On 2026-08-20, the first live session ever to
    reach that gate, the log said "not constructed" while the session
    artifacts showed STRATEGY_PROPOSED followed immediately by
    GATE_B_MARGIN_VETO: construction had SUCCEEDED and the margin gate
    refused. A report that misstates the stage sends the next investigation
    to the wrong module, and cost an hour here.

    Order matters: `blocking_reason` is the most specific thing the runtime
    can say, so it wins whenever it is populated.
    """
    if cycle_result is None:
        return "no cycle result"
    if getattr(cycle_result, "blocking_reason", None):
        return cycle_result.blocking_reason
    governor_result = getattr(cycle_result, "governor_result", None)
    if governor_result is not None:
        return governor_result.blocking_stage
    if not getattr(cycle_result, "proposal", None):
        return "not constructed"
    return "constructed but not filled (no blocking reason reported)"


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
        # No verdict until a cycle assesses one. The entry gate treats None
        # as "not assessed" and refuses -- never as "fine".
        self._data_quality = None
        # Set when the intelligence broker is constructed -- LIVE or REPLAY.
        # None until then, which the gate reads as undeterminable rather than
        # as live.
        self._intelligence_origin = None
        # Set only when a partial entry leaves filled-but-unwound legs live.
        self._orphan_position_live = False
        # Set by _run_eod_closure; read by _session_archive so finalization
        # reports the broker's own view instead of a hardcoded flat.
        self._eod_closure_result = None
        self._exit_place_fn = None
        # Reconciliation state. Blocks default to FALSE only because no
        # position can exist before the first pass runs; the first
        # reconciliation sets the real value.
        self._last_reconciliation = None
        self._reconciliation_blocks_entry = False
        self._journal_path = None

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
        self._tick_feed = None  # set only by tick_source.type=websocket
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

        # Set EARLY, because the market-hours gate below reads it. It is
        # assigned again further down beside the other session fields; that
        # re-assignment is the same object and is left in place so the
        # original grouping still reads as one block.
        self._session_cfg = session_cfg

        # THE GATE RUNS BEFORE ANYTHING TOUCHES THE MARKET.
        #
        # OBSERVED LIVE, 2026-08-20. The gate was called from
        # `_pre_market_check`, which runs AFTER `_startup` -- but `_startup`
        # builds the regime provider, and that runs the warm-up: 16 spot polls
        # at 30s intervals. With the timer firing at 09:14 the session began
        # polling at 09:14:01, so its first two warm-up samples read a PRE-OPEN
        # book, and those samples feed the evidence window the stability gate
        # then judges. The capture units were unaffected (their first rows
        # landed at 09:15:00.866, exactly at the open) -- this was the trading
        # unit alone, sampling before the market existed.
        #
        # `_resolve_exchange_lot_size` below also reads the instrument master,
        # and the broker is constructed a few lines later, so this is the last
        # point at which the gate can precede every market touch.
        #
        # The call in `_pre_market_check` is DELIBERATELY KEPT. Both methods do
        # broker work, and once this one has waited the second is a no-op that
        # returns immediately -- a second net costs nothing and protects any
        # path that reaches `_pre_market_check` without coming through here.
        self._await_market_open()

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
        # EOD closure enumerates Bujji's own submitted orders from this file
        # to cancel any still working (the Broker interface exposes no
        # working-order enumeration -- see eod_closure's own docstring).
        self._journal_path = journal_path
        # STARTUP RECOVERY (Layer 11, 2026-08-21) -- run once, before any new
        # mint, per position_group_recovery's own stated contract, honoured
        # for the first time (it previously had zero callers). Any leg a
        # prior crash left SUBMIT_PENDING_UNKNOWN is resolved against broker
        # truth by its exact client_order_id. If legs remain unresolved the
        # session REFUSES to trade: an unresolved order is possibly a live
        # position, and minting new risk on top of unknown risk is the one
        # thing a restart must never do.
        import asyncio as _asyncio_rec

        from bujji.production_runtime.execution_journal_bridge import (
            recover_unresolved_at_startup)

        recovery = recover_unresolved_at_startup(
            self._journal, str(journal_path), self._broker, _asyncio_rec.run,
            self._logger, self._clock)
        self._governor_result_summary["startup_order_recovery"] = recovery
        if recovery["unresolved_after"]:
            raise ConfigurationError(
                "STARTUP RECOVERY could not resolve order state for group(s) "
                f"{recovery['unresolved_after']} -- refusing to trade on top of "
                "unknown in-flight orders. Inspect the position group journal and "
                "the broker order book, then resolve or operator-correct them.")

        capital_snapshot_provider = _make_capital_snapshot_provider(
            capital_cfg, self._clock)

        # THE READ END OF THE LEARNING LOOP (audit finding, 2026-08-20).
        #
        # This was `AdaptiveRiskMemory()` -- a fresh, empty memory every
        # session, forever. Meanwhile governor_context_builder asks that
        # memory real questions on every entry decision (all_entries(),
        # lookup(strategy_type=...)), and nothing anywhere ever called
        # append_observation. So the risk chain was interrogating a memory
        # that could not answer, and D-8's durable outcome records -- written
        # faithfully after every close -- were read by nobody.
        #
        # Hydrating it closes the loop. It is INERT until Bujji actually
        # trades: with no closed positions the store is empty and behaviour is
        # identical to before. From the first real trade onward, past outcomes
        # begin informing risk context, which is the point of having written
        # them down.
        from bujji.production_runtime.risk_memory_bridge import hydrate_risk_memory

        risk_memory, hydration = hydrate_risk_memory(
            str(self._outcome_memory_store().path), clock=self._clock)
        self._governor_result_summary["risk_memory_hydration"] = hydration.as_dict()
        self._logger.info(
            "RISK MEMORY -- hydrated %d entr(y|ies) from %d durable outcome record(s) [%s]",
            hydration.entries_loaded, hydration.records_seen, hydration.status)
        if hydration.records_skipped:
            self._logger.warning("RISK MEMORY -- %d record(s) skipped: %s",
                                 hydration.records_skipped, "; ".join(hydration.skip_reasons))

        # BROKER-TRUTH EXECUTION (2026-08-21). ExecutionEngine already
        # implemented idempotent placement, poll-to-terminal against a
        # deadline, cancel-on-timeout and (as of today) post-cancel
        # reconciliation -- and had ZERO production callers. Every reference
        # to it elsewhere was a comment in msi_execution_planning saying it
        # "reuses the CONCEPT of" it. This is the first time the real engine
        # runs.
        #
        # It wraps the SAME PaperBroker the composition root executes through
        # -- one terminal executor, no second broker instance and no second
        # order path.
        import asyncio as _asyncio_exec

        from bujji.execution.engine import ExecutionEngine as _ExecutionEngine

        from bujji.core.config import AppConfig as _AppConfig, BrokerConfig as _BrokerConfig

        # ExecutionEngine reads only `config.broker`'s four execution knobs.
        # Built from the session config so an operator can tune them, with the
        # BrokerConfig defaults (3 attempts / 1.5s backoff / 1s poll / 15s
        # timeout) when unset. No credentials are placed here: this engine
        # executes against the PaperBroker instance handed to it, and the
        # live data broker is a separate, execution-neutered instance.
        _exec_cfg = (self._config.get("execution", {})
                     if isinstance(self._config, dict) else {})
        execution_engine = _ExecutionEngine(
            self._broker,
            _AppConfig(broker=_BrokerConfig(
                name="paper",
                retry_attempts=int(_exec_cfg.get("retry_attempts", 3)),
                retry_backoff_seconds=float(_exec_cfg.get("retry_backoff_seconds", 1.5)),
                poll_interval_seconds=float(_exec_cfg.get("poll_interval_seconds", 1.0)),
                order_timeout_seconds=float(_exec_cfg.get("order_timeout_seconds", 15.0)),
            )),
            self._logger)
        self._logger.info(
            "EXECUTION: broker-truth lifecycle ENABLED -- poll-to-terminal "
            "(interval=%ss timeout=%ss), cancel-on-timeout, post-cancel "
            "reconciliation. A timeout means UNKNOWN, never 'not filled'.",
            _exec_cfg.get("poll_interval_seconds", 1.0),
            _exec_cfg.get("order_timeout_seconds", 15.0))
        self._root = build_trading_brain_composition_root(
            broker=self._broker, journal=self._journal,
            execution_engine=execution_engine,
            margin_provider=_make_margin_provider(providers_cfg),
            capital_snapshot_provider=capital_snapshot_provider, memory=risk_memory,
            clock=self._clock, underlying=underlying, exchange_lot_size=exchange_lot_size,
            initial_state=RuntimeState.ENTRY_ENABLED,
        )
        self._trading_brain_runtime = TradingBrainRuntime(self._root)

        self._registry = PositionRealityRegistry(self._broker)
        self._lifecycle_runtime = PositionLifecycleRuntime(self._registry, self._clock)
        self._portfolio_engine = PortfolioRealityEngine(self._registry, event_bus=self._root.event_bus)
        # EXITS USE THE SAME MACHINE AS ENTRIES (2026-08-21). The exit path
        # previously called broker.place_order directly: no journal, no
        # poll-to-terminal, no cancel-on-timeout. That is strictly more
        # dangerous than the entry case it mirrored, because an exit failure
        # happens while a naked position is already live -- a stop-loss that
        # times out and is read as "rejected" leaves a position Bujji has
        # stopped watching.
        from bujji.production_runtime.execution_journal_bridge import broker_truth_place_fn as _btpf

        _exit_place_fn = _btpf(execution_engine, _asyncio_exec.run, self._logger)
        # Held for EOD closure, which flattens whatever the management pass
        # did not -- through the SAME broker-truth placement function, so
        # there is exactly one order path for entry, exit and closure.
        self._exit_place_fn = _exit_place_fn
        self._executor = TradeLifecycleExecutor(self._broker, self._registry, self._lifecycle_runtime,
                                                place_fn=_exit_place_fn,
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

        # DEFINED-RISK MODE, derived from the execution mode and FAIL-CLOSED.
        # Anything other than a literal `shadow_mode: true` -- a missing key, a
        # typo, a string "false", or the real-money switch itself -- selects
        # defined-risk only. Operator decision 2026-08-20: real money never
        # sells a shape whose loss is bounded only by a 300-second heartbeat.
        # It is deliberately NOT its own config flag; a second switch is a
        # second thing to forget on the day it matters most.
        defined_risk_only = self._config.get("shadow_mode") is not True
        if defined_risk_only:
            self._logger.warning(
                "DEFINED-RISK MODE ACTIVE (shadow_mode=%r) -- naked sideways shapes are "
                "substituted for their winged twins.", self._config.get("shadow_mode"))
        self._governor_result_summary["defined_risk_only"] = defined_risk_only

        self._governor = TradingSessionGovernor(
            session_id=self._session_id, trading_brain_runtime=self._trading_brain_runtime,
            registry=self._registry, lifecycle_runtime=self._lifecycle_runtime, executor=self._executor,
            exit_policy_config=exit_policy_config, clock=self._clock, event_bus=self._root.event_bus,
            defined_risk_only=defined_risk_only,
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
            # PROVENANCE FROM REALITY, not from a default. MarketDataAdapter's
            # `source` defaults to "fyers_live" and describes the ADAPTER, not
            # the broker behind it -- so a replay broker produced snapshots
            # labelled live, and the data-quality gate would have read them as
            # LIVE. The runner is the only place that knows which broker it
            # built, so it is the only place that can say.
            self._intelligence_origin = "REPLAY"
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
            self._intelligence_origin = "LIVE"
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
        elif tick_type == "websocket":
            import asyncio as _asyncio
            import os as _os

            from bujji.broker.fyers_ws import FyersTickFeed, TickSilenceWatchdog
            from bujji.production_runtime.intraday_price_provider import (
                LiveTickProvider, WebsocketTickProvider)

            data_broker = getattr(self, "_intelligence_broker", None)
            if regime_type != "market_thesis_live" or data_broker is None:
                # The identical fail-closed guard type=broker carries, for the
                # identical reason: without the live FYERS data broker the only
                # fallback available would be the synthetic PaperBroker, and a
                # websocket feed backed by a random-walk fallback would price
                # real position management off fabricated numbers whenever the
                # socket went quiet -- silently, which is the worst way.
                raise ConfigurationError(
                    "providers.tick_source.type=websocket requires the live FYERS data "
                    "broker (providers.regime.type=market_thesis_live) as its REST "
                    "fallback. Declare type=paper_synthetic to opt into synthetic "
                    "ticks explicitly, or type=observation_store for replay."
                )
            app_id, token = _os.getenv("FYERS_APP_ID"), _os.getenv("FYERS_ACCESS_TOKEN")
            if not app_id or not token:
                raise ConfigurationError(
                    "FYERS_APP_ID / FYERS_ACCESS_TOKEN must be set for "
                    "tick_source.type=websocket -- the same credentials the live "
                    "regime path already requires."
                )
            # Verified live (fyers_ws module docstring): the websocket token
            # format is "{app_id}:{access_token}".
            self._tick_feed = FyersTickFeed(
                app_id, f"{app_id}:{token}", self._logger,
                log_path=str(REPO_ROOT / "logs"))
            self._tick_feed.start()
            watchdog = TickSilenceWatchdog(
                silence_threshold_seconds=float(tick_cfg.get("silence_threshold_seconds", 120.0)),
                logger=self._logger)
            self._price_provider = WebsocketTickProvider(
                self._tick_feed, watchdog,
                LiveTickProvider(data_broker, _asyncio.run),
                max_tick_age_seconds=float(tick_cfg.get("max_tick_age_seconds", 90.0)),
            )
            self._logger.info(
                "Tick source: FYERS WEBSOCKET (certified ~2.4 ticks/s) with "
                "per-symbol REST fallback via the execution-neutered data broker. "
                "max_tick_age=%ss silence_threshold=%ss",
                tick_cfg.get("max_tick_age_seconds", 90.0),
                tick_cfg.get("silence_threshold_seconds", 120.0))
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
        # The adapter is told what the broker ACTUALLY is. Its `source` default
        # ("fyers_live") describes the adapter, not the data behind it -- so a
        # replay broker silently produced snapshots labelled live. Origin is
        # now carried from the construction site that knows the truth.
        origin = getattr(self, "_intelligence_origin", None)
        adapter = MarketDataAdapter(
            broker, self._clock, underlying=self._root.underlying,
            **({"source": f"fyers_{origin.lower()}"} if origin else {}))

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
        evidence_integrity = self._persist_cycle_evidence(snapshot, cycle_record)
        self._pending_thesis_artifact = build_thesis_artifact(
            thesis=thesis, cycle_record=cycle_record,
            stability=getattr(self, "_pending_stability", None),
            cycle=getattr(self, "_pending_cycle", None),
            recorded_at=self._clock().isoformat(),
            level_context=self._level_context_dict(),
            depth_observation=self._depth_observation(snapshot, thesis),
            evidence_integrity=evidence_integrity,
            data_quality=self._assess_data_quality(snapshot, evidence_integrity),
        )
        return MarketThesisRegimeProvider(thesis, volatility_regime)

    def _assess_data_quality(self, snapshot, evidence_integrity) -> Optional[Dict[str, Any]]:
        """Grade this cycle's market data, and remember the verdict for the
        entry gate.

        `MarketDataAdapter` has always computed `health_status` and
        `missing_fields`, and `IntelligenceCycleRecorder` has always recorded
        the value -- where nothing read it. The signal existed and was wired
        to a log line. This reads it, and `_entry_window` can now refuse on it.

        Never raises. A FAILED assessment is stored as a verdict that does NOT
        permit trading: if the gate itself is broken we do not know whether
        the data is sound, and the entire value of a safety boundary is that
        it refuses when it does not know.
        """
        try:
            from bujji.production_runtime.market_data_gate import assess_market_data

            gate_cfg = (self._config.get("market_data_gate", {})
                        if isinstance(getattr(self, "_config", None), dict) else {})
            verdict = assess_market_data(
                snapshot, evidence_integrity=evidence_integrity,
                required_origin=gate_cfg.get("required_origin", "LIVE"),
                require_whole_evidence_trail=bool(
                    gate_cfg.get("require_whole_evidence_trail", False)),
            )
            self._data_quality = verdict
            if not verdict.may_trade:
                self._logger.warning(
                    "DATA QUALITY %s -- entry will be REFUSED this cycle. reasons=%s",
                    verdict.quality, list(verdict.reasons))
            elif verdict.quality != "GOOD":
                self._logger.info("DATA QUALITY %s -- trading permitted. reasons=%s",
                                  verdict.quality, list(verdict.reasons))
            return verdict.to_dict()
        except Exception as exc:  # noqa: BLE001 -- fail CLOSED, never open
            self._logger.warning("data-quality gate failed, refusing entry: %s: %s",
                                 type(exc).__name__, exc)
            self._data_quality = None
            return {"quality": "INVALID", "may_trade": False,
                    "reasons": [f"GATE_FAILED:{type(exc).__name__}"], "error": str(exc)}

    def _evidence_path(self) -> Optional[str]:
        """Where this session's decision evidence lives.

        Derived from the session store rather than defaulting to a shared
        path -- the same lesson the outcome-memory store learned the hard
        way on 2026-08-20, when a default production path let 78 synthetic
        test records into the real store.
        """
        store = getattr(self, "_store", None)
        session_dir = getattr(store, "session_dir", None)
        if session_dir is None:
            return None
        return str(session_dir / "decision_evidence.jsonl")

    def _persist_polled_evidence(self, snapshots) -> int:
        """Persist evidence for observations as they are POLLED, not only when
        they produce a thesis.

        The rolling window is what PSI/MSSI cite from. Writing evidence only
        on stable cycles left the window's own observations unpersisted, so
        cited ids from earlier cycles dangled. Returns how many NEW records
        were written (append_evidence dedupes by content hash, so re-polling
        the same fact costs nothing).

        Never raises: an audit trail must not be able to end a session.
        """
        if not snapshots:
            return 0
        path = self._evidence_path()
        if path is None:
            return 0
        try:
            from bujji.shadow_observatory import evidence_store

            written = 0
            for snapshot in snapshots:
                written += evidence_store.append_evidence(
                    path, evidence_store.evidence_records_for_snapshot(snapshot))
            self._evidence_records_written = getattr(self, "_evidence_records_written", 0) + written
            return written
        except Exception as exc:  # noqa: BLE001 -- audit never ends a session
            self._logger.warning("polled evidence persistence failed: %s: %s",
                                 type(exc).__name__, exc)
            return 0

    def _persist_cycle_evidence(self, snapshot, cycle_record) -> Optional[Dict[str, Any]]:
        """Persist the observations this cycle's decision actually used, and
        measure whether every cited id now resolves.

        WHY THIS EXISTS. The decision path builds its own observations from
        its own snapshot, cites their ids in `supporting_observation_ids` /
        `evidence_ids` / `which_observations_support_it`, and dropped them at
        end of cycle. The ids were honest identifiers for facts nobody wrote
        down: 346 cited, 0 resolvable, on the first live continuous session.

        WHY REBUILDING IS SOUND. `build_observation()` mints its id as a
        content hash over identity and value, reading no clock and no random
        source, so rebuilding from the SAME snapshot reproduces byte-identical
        ids. The decision path is therefore left completely untouched -- not
        intercepted, not given a sink -- and this re-derives what it built.

        THIS method runs only on cycles that derive a regime -- the stability
        gate makes that ~8% of them. The rolling window's own observations are
        persisted separately by `_persist_polled_evidence`, at the poll, every
        cycle, which is what makes the ids PSI/MSSI cite from earlier cycles
        resolvable. An earlier version of this docstring claimed THIS call
        covered every cycle. It never did.

        Never raises: an audit trail must not be able to end a session that
        may hold an open position.
        """
        path = self._evidence_path()
        if path is None:
            return None
        try:
            from bujji.shadow_observatory import evidence_store

            written = evidence_store.append_evidence(
                path, evidence_store.evidence_records_for_snapshot(snapshot))
            integrity = evidence_store.evidence_integrity(path, cycle_record)
            integrity["records_written_this_cycle"] = written
            if integrity["all_cited_ids_resolve"] is False:
                # Loud, every cycle it happens. A broken evidence trail is
                # the condition that makes every downstream outcome
                # unauditable, so it must never be discoverable only by
                # someone going looking for it.
                self._logger.warning(
                    "EVIDENCE TRAIL INCOMPLETE: %d of %d cited observation ids do not "
                    "resolve in %s -- decisions on this cycle cannot be fully "
                    "reconstructed. First unresolved: %s",
                    integrity["unresolved_count"], integrity["cited_count"],
                    path, integrity["unresolved_ids"][:3],
                )
            return integrity
        except Exception as exc:  # noqa: BLE001 -- audit must not end a session
            self._logger.warning("evidence persistence failed: %s: %s",
                                 type(exc).__name__, exc)
            return {"status": f"FAILED:{type(exc).__name__}", "error": str(exc),
                    "all_cited_ids_resolve": False}

    def _depth_observation(self, snapshot, thesis) -> Optional[Dict[str, Any]]:
        """Order-book pressure, recorded beside the direction it did not inform.

        Depth is fetched every cycle (operator directive) and is deliberately
        NOT a direction lens: see the note in thesis_artifact for the
        reconcile_lenses mechanism that makes a thin fourth opinion harmful.
        This records the raw imbalance next to what direction actually
        concluded on the same cycle, so the promotion decision can be made
        from a trail.

        Never raises: a diagnostic must not end a session.
        """
        try:
            from bujji.futures_observation.engine import compute_depth_imbalance

            futures = getattr(snapshot, "futures", None)
            if futures is None:
                return {"status": "NO_FUTURES_SNAPSHOT", "consumed_by_direction": False}
            buy = getattr(futures, "total_buy_qty", None)
            sell = getattr(futures, "total_sell_qty", None)
            return {
                "status": "OK" if buy is not None and sell is not None else "NOT_OBSERVED",
                "futures_symbol": getattr(futures, "symbol", None),
                "total_buy_qty": buy,
                "total_sell_qty": sell,
                # None, never 0.0, when unobserved -- a zero imbalance is a
                # measured balanced book and absence is not.
                "imbalance": compute_depth_imbalance(buy, sell),
                # What direction concluded on THIS cycle, for the comparison
                # this record exists to enable.
                "direction_concluded": getattr(thesis, "directional_bias", None),
                "thesis_confidence": getattr(thesis, "confidence", None),
                # Explicit in the record itself, so no reader can mistake a
                # recorded observation for an input to the decision.
                "consumed_by_direction": False,
            }
        except Exception as exc:  # noqa: BLE001 -- diagnostics never end a session
            return {"status": f"FAILED:{type(exc).__name__}", "error": str(exc),
                    "consumed_by_direction": False}

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
            _fresh = poll_spot_series(
                fetch_spot=lambda: asyncio.run(broker.get_spot(self._root.underlying)),
                clock=self._clock, plan=plan, logger=self._logger,
            )
            snapshots.extend(_fresh)
            # EVIDENCE FOR EVERY POLLED OBSERVATION (audit, 2026-08-21).
            #
            # My own commit claimed evidence was "written every cycle, not
            # only cycles that produced a thesis". It was not:
            # _persist_cycle_evidence has exactly one call site, inside
            # _build_market_thesis_regime_provider, reached only after the
            # stability gate -- historically ~8% of cycles.
            #
            # That defeated the fix's own purpose. PSI and MSSI cite
            # observations from across this rolling window, so on a stable
            # cycle they cite observations polled during the ~92% of cycles
            # whose evidence was never written -- and those ids dangle, which
            # is the exact defect the evidence store exists to remove.
            #
            # Persisting at the poll makes the claim true: every observation
            # that can later be cited is on disk before it can be cited.
            self._persist_polled_evidence(_fresh)
            del snapshots[:-window]
            if snapshots:
                # MarketSnapshot.spot is a SpotSnapshot OBJECT; the number is
                # its .ltp. Passing the object through made every level-context
                # build fail all day on 2026-08-20 with "float() argument must
                # be a string or a real number, not 'SpotSnapshot'" -- non-fatal
                # and observation-only, so it degraded silently and L-5
                # recorded nothing for the whole session.
                spot_snapshot = getattr(snapshots[-1], "spot", None)
                self._last_spot = getattr(spot_snapshot, "ltp", None)
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

        # An ORPHANED partial entry is a live position even though
        # _attempt_entry returned False. Without this it was never managed at
        # all: the loop simply moved on to post-cutoff observation while a
        # naked leg sat at the broker.
        if entered or getattr(self, "_orphan_position_live", False):
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
            _fresh = poll_spot_series(
                fetch_spot=lambda: asyncio.run(broker.get_spot(self._root.underlying)),
                clock=self._clock, plan=plan, logger=self._logger,
            )
            snapshots.extend(_fresh)
            # EVIDENCE FOR EVERY POLLED OBSERVATION (audit, 2026-08-21).
            #
            # My own commit claimed evidence was "written every cycle, not
            # only cycles that produced a thesis". It was not:
            # _persist_cycle_evidence has exactly one call site, inside
            # _build_market_thesis_regime_provider, reached only after the
            # stability gate -- historically ~8% of cycles.
            #
            # That defeated the fix's own purpose. PSI and MSSI cite
            # observations from across this rolling window, so on a stable
            # cycle they cite observations polled during the ~92% of cycles
            # whose evidence was never written -- and those ids dangle, which
            # is the exact defect the evidence store exists to remove.
            #
            # Persisting at the poll makes the claim true: every observation
            # that can later be cited is on disk before it can be cited.
            self._persist_polled_evidence(_fresh)
            del snapshots[:-window]
            trail.append({"cycle": cycles, "at": now.isoformat(), "phase": phase,
                          "spots": len(snapshots)})
        self._governor_result_summary["continuous_cycles"] = cycles

    def _entry_window(self) -> None:
        self._stage = RunnerStage.ENTRY_WINDOW
        trend_regime, volatility_regime = self._regime_provider.get_regime()
        self._logger.info("ENTRY_WINDOW -- regime trend=%s volatility=%s", trend_regime, volatility_regime)
        # THE DATA-QUALITY BOUNDARY. Before this existed, nothing stood
        # between market data and a trading decision: a forensic trace of
        # this runner for any quality gate found one string, in one branch,
        # for one condition. Stale, gapped, degraded and non-LIVE data all
        # reached strategy selection stamped completeness 1.0.
        #
        # A decision is BLOCKED here, not warned about.
        if not self._data_quality_permits_entry():
            return
        self._attempt_entry(trend_regime, volatility_regime)

    def _data_quality_permits_entry(self) -> bool:
        """Fail closed WHERE THE GATE APPLIES, and say so plainly where it
        does not.

        SCOPE. This gate grades a MarketSnapshot. Only a snapshot-deriving
        regime provider (`market_thesis` / `market_thesis_live`, which build
        an intelligence broker and set `_intelligence_origin`) produces one.
        A config that supplies a fixed regime instead never creates a
        snapshot, so there is nothing here to grade -- and refusing such a
        session would not be strictness, it would be this gate answering a
        question it was never asked.

        Where the gate DOES apply, a missing verdict is a refusal. An
        intelligence broker was built, a snapshot should have been graded,
        and its absence means we do not know whether the data was sound.
        Refusing when we do not know is the entire purpose.

        The not-applicable case is recorded on the summary rather than
        passing silently: a session trading without a data-quality boundary
        is a fact an operator must be able to read afterwards.
        """
        # RECONCILIATION IS A SAFETY CONTROL, not an observability feature.
        # Taking new risk while the broker holds exposure Bujji is not
        # managing -- or while position truth cannot be established at all --
        # compounds an already-unmanaged position with a fresh one.
        if getattr(self, "_reconciliation_blocks_entry", False):
            last = getattr(self, "_last_reconciliation", None)
            self._logger.warning(
                "ENTRY REFUSED -- position reconciliation %s. %s",
                getattr(last, "verdict", "UNKNOWN"), getattr(last, "detail", ""))
            self._governor_result_summary["entry_blocked_by"] = "POSITION_RECONCILIATION"
            return False

        verdict = getattr(self, "_data_quality", None)
        if verdict is None:
            if getattr(self, "_intelligence_origin", None) is None:
                # No snapshot-deriving regime provider in this config.
                self._logger.info(
                    "Data-quality gate NOT APPLICABLE -- this config derives no "
                    "MarketSnapshot (fixed regime), so there is no market data for "
                    "it to grade. The session proceeds WITHOUT a data-quality "
                    "boundary; that is a property of the config, not a clean bill "
                    "of health.")
                self._governor_result_summary["data_quality"] = "NOT_APPLICABLE"
                return True
            self._logger.warning(
                "ENTRY REFUSED -- a snapshot-deriving regime provider is configured "
                "(origin=%s) but no data-quality verdict exists for this cycle. A "
                "gate that permits when it has not assessed is not a gate.",
                self._intelligence_origin)
            self._governor_result_summary["entry_blocked_by"] = "DATA_QUALITY_NOT_ASSESSED"
            return False
        if not verdict.may_trade:
            self._logger.warning(
                "ENTRY REFUSED -- data quality %s. reasons=%s missing=%s",
                verdict.quality, list(verdict.reasons), list(verdict.missing_fields))
            self._governor_result_summary["entry_blocked_by"] = (
                f"DATA_QUALITY_{verdict.quality}")
            self._governor_result_summary["data_quality_reasons"] = list(verdict.reasons)
            return False
        return True

    def _risk_budget(self) -> Dict[str, float]:
        """The rupee figures for THIS position, scaled by the lots taken.

        WHY THIS EXISTS (audit finding, 2026-08-20). Three rupee amounts were
        configured as flat per-POSITION totals while the size was configured
        separately:

            desired_quantity: 1
            requested_risk: 5000.0                    -> initial_risk (the stop)
            proposed_trade_effect.additional_margin   -> capital check
            proposed_trade_effect.additional_max_loss -> risk budget governor

        `strategy_risk_adapter` takes desired_quantity and requested_risk side
        by side and never relates them, so the caller is the only place that
        can. Nothing did. Raise desired_quantity to 5 and the position's real
        risk quintuples while the stop stays at Rs 5,000 -- it would fire on
        noise, and the risk-budget and capital checks would be sized for a
        position one fifth the size of the one actually taken.

        The three figures are now read as PER LOT and multiplied by the lots.
        At the current desired_quantity: 1 every value is identical to before,
        so this changes no behaviour today; it makes the numbers correct the
        moment anyone changes the size, which is exactly when a silent
        mis-scaling would be most expensive.
        """
        cfg = self._session_cfg
        lots = max(1, int(cfg.get("desired_quantity", 1)))
        per_lot_risk = float(cfg.get("requested_risk", 5000.0))
        effect = cfg.get("proposed_trade_effect", {}) or {}
        per_lot_margin = float(effect.get("additional_margin", 10000.0))
        per_lot_max_loss = float(effect.get("additional_max_loss", 5000.0))

        budget = {
            "lots": lots,
            "requested_risk": per_lot_risk * lots,
            "additional_margin": per_lot_margin * lots,
            "additional_max_loss": per_lot_max_loss * lots,
            "per_lot_requested_risk": per_lot_risk,
        }

        # A single trade whose stop is wider than the whole day's loss limit is
        # incoherent: the daily limit would halt the session before the
        # position's own stop could ever fire. Reachable only by raising the
        # size, which is precisely the case this scaling exists for.
        daily_limit = (self._config.get("capital_snapshot", {}) or {}).get("daily_loss_limit")
        if daily_limit and budget["requested_risk"] > float(daily_limit):
            self._logger.warning(
                "RISK BUDGET -- this trade's stop (Rs %.0f = %d lot(s) x Rs %.0f) exceeds the "
                "daily loss limit (Rs %.0f). The daily limit would halt the session before the "
                "position's own stop could fire.",
                budget["requested_risk"], lots, per_lot_risk, float(daily_limit))

        return budget

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

        budget = self._risk_budget()
        self._governor_result_summary["risk_budget"] = dict(budget)
        self._logger.info(
            "RISK BUDGET -- %d lot(s): requested_risk=Rs %.0f margin=Rs %.0f max_loss=Rs %.0f "
            "(per lot Rs %.0f)",
            budget["lots"], budget["requested_risk"], budget["additional_margin"],
            budget["additional_max_loss"], budget["per_lot_requested_risk"])

        cycle_result, entry_decision = self._governor.attempt_entry(
            chain=chain, spot=spot, as_of_date=self._as_of_date, timestamp=self._clock().isoformat(),
            desired_quantity=budget["lots"],
            requested_risk=budget["requested_risk"],
            proposed_trade_effect=ProposedTradeEffect(
                additional_margin=budget["additional_margin"],
                additional_max_loss=budget["additional_max_loss"],
            ),
            contracts_by_client_order_id={}, sides_by_client_order_id={},
            reference_prices_by_client_order_id={}, risk_by_position_group_id={},
            direction=session_cfg.get("direction"), expected_move_pct=session_cfg.get("expected_move_pct"),
        )
        self._governor_result_summary["entry_allowed"] = entry_decision.allowed
        self._governor_result_summary["entry_filled"] = cycle_result.filled if cycle_result else False

        if not cycle_result or not cycle_result.filled:
            reason = _entry_failure_reason(cycle_result)
            # ORPHANED PARTIAL (Layer 11, 2026-08-21): some legs filled, the
            # containment unwind did NOT fill, and those legs are LIVE
            # positions. They are registered for management here -- an orphan
            # with a stop-loss is a contained problem; an invisible one has
            # nothing. Before this, a partial fill was logged as "did not
            # fill" and the filled legs simply vanished from the runner's
            # model while remaining real at the broker.
            if isinstance(reason, str) and reason.startswith("PARTIAL_ORPHANED:"):
                self._register_orphaned_legs(cycle_result, reason)
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
            budget["requested_risk"], self._clock, contracts=contracts,
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

    def _register_orphaned_legs(self, cycle_result, reason: str) -> None:
        """Register the filled-but-not-unwound legs of a partial entry so
        position management owns them. Never raises: failing to register
        must not also lose the CRITICAL log that names the orphans."""
        try:
            orphan_coids = set(reason.split(":", 1)[1].split(","))
            from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract

            contracts = {}
            entry_prices = {}
            for leg, order_result in zip(cycle_result.proposal.legs, cycle_result.order_results):
                if getattr(order_result, "client_order_id", None) in orphan_coids:
                    contract = _leg_to_core_contract(leg, self._root.underlying,
                                                     self._root.exchange_lot_size)
                    contracts[contract.symbol] = contract
                    entry_prices[contract.symbol] = order_result.average_price
            if not contracts:
                self._logger.critical(
                    "ORPHAN REGISTRATION FAILED -- reason named %s but no matching "
                    "order results were found. The broker book must be inspected "
                    "manually NOW.", sorted(orphan_coids))
                return
            pg_id = f"{cycle_result.proposal.assessment_id}-ORPHAN"
            self._registry.register_entry(
                pg_id, cycle_result.proposal.strategy_family, list(contracts.keys()),
                0.0, self._clock, contracts=contracts)
            self._lifecycle_runtime.mark_open(pg_id)
            # THE ORPHAN NEEDS A MANAGEMENT IDENTITY (audit, 2026-08-21).
            #
            # An orphan only occurs when filled=False, so the governor never
            # set its own _position_group_id -- it stayed None. The management
            # pass reads `pg_id = self._governor._position_group_id` and then
            # `valuations.get(None)`, which is always None, so every pass hit
            # "no valuation available for None" and returned. The leg this
            # runner had just logged as "LIVE ... Management cycles will
            # revalue and exit it" was therefore never valued and never
            # exited. That log line was false telemetry I wrote.
            #
            # Pointing the governor at the orphan group is what makes the
            # claim true. Same private attribute the entry path and the
            # management pass already read.
            self._governor._position_group_id = pg_id
            # Continuous mode only starts management when _attempt_entry
            # returns True, and an orphan returns False. This flag is what
            # tells it a live position exists regardless.
            self._orphan_position_live = True
            self._entry_prices = entry_prices
            self._contracts_by_symbol = contracts
            self._logger.critical(
                "ORPHANED LEGS REGISTERED FOR MANAGEMENT: %s -- position %s is LIVE "
                "with legs %s. Management cycles will revalue and exit it; treat "
                "this session as an incident regardless.",
                sorted(orphan_coids), pg_id, list(contracts.keys()))
        except Exception as exc:  # noqa: BLE001
            self._logger.critical(
                "ORPHAN REGISTRATION RAISED %s: %s -- the broker book holds live "
                "legs this runner is NOT managing. Manual intervention required NOW.",
                type(exc).__name__, exc)

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
        # BEFORE the ordinary exit policy.
        #
        # WHAT WAS WRONG (audit, 2026-08-21). This block set a flag, called
        # `run_market_close_sequence()` and returned. That method is four
        # lines: transition(POSTMARKET), transition(COMPLETE). It places NO
        # order. The comment here claimed it "reus[ed] the same mandatory
        # close-everything sequence _eod_close uses" -- but _eod_close is a
        # management pass PLUS that transition, and the brake called only the
        # half that fires nothing. The `return` then skipped
        # `evaluate_and_enforce_exit` below, which is the ONLY call on this
        # path that submits. So on the exact event the daily-loss brake
        # exists for, the position stayed open, nothing was submitted, and
        # "EMERGENCY CLOSE" was logged.
        #
        # It now drives the same forced-exit path a hard limit uses -- the
        # one that goes through the canonical broker-truth execution boundary
        # -- and verifies the result against the broker before anything is
        # allowed to call itself closed.
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
            self._execute_emergency_close(brake_reason, valuation, stage_label)
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
            # initial_risk for the exit policy: the SAME scaled figure the entry
            # was sized against, so the stop stays proportional to the position.
            self._risk_budget()["requested_risk"],
        ))
        # EXIT STATUS IS CONSUMED, NOT DISCARDED (2026-08-21).
        #
        # This read `result.forced_execution is not None` -- a BOOLEAN "did an
        # execution object come back" -- and logged and recorded only that.
        # The object carries an honest terminal status (EXECUTED / PARTIAL /
        # BROKER_TRUTH_UNKNOWN / REJECTED / FAILED_VALIDATION) computed from
        # broker truth, and every bit of it was thrown away at this line. A
        # stop-loss that the broker REJECTED and one that filled produced the
        # identical log line and the identical summary entry:
        # `forced_execution=True`.
        #
        # Closure decisions were never wrong because of this -- the governor
        # transitions EXITED only on STATUS_EXECUTED, the executor marks
        # closed only when broker reality reports no open leg, and
        # map_exit_fills_to_legs skips a leg with no average_price. What was
        # lost was the ability to SEE a failed exit, and any escalation from
        # one. An operator reading the session could not tell whether the
        # stop-loss worked.
        # CONTINUOUS RECONCILIATION (2026-08-21). Broker truth was consulted
        # at placement, at startup and at EOD -- never in between. The only
        # in-session position read went through PositionRealityRegistry, which
        # intersects broker positions with an in-memory table of registered
        # symbols, so a position at a symbol Bujji never registered was
        # mathematically undiscoverable: never valued, never stop-lossed,
        # never escalated, unnoticed until EOD.
        self._reconcile_broker_positions(stage_label)

        execution = getattr(result, "forced_execution", None)
        exit_status = getattr(execution, "status", None) if execution is not None else None
        self._logger.info(
            "%s -- D.4 action=%s exit_policy=%s forced_execution=%s exit_status=%s",
            stage_label, result.lifecycle_evaluation.recommendation.action,
            result.policy_decision.decision, execution is not None, exit_status,
        )
        self._governor_result_summary.setdefault("management_passes", []).append({
            "stage": stage_label, "decision": result.policy_decision.decision,
            "forced_execution": execution is not None,
            "exit_status": exit_status,
        })
        self._record_exit_outcome(stage_label, exit_status, execution)
        self._capture_exit_fills(symbols_before_exit, result)


    def _expected_symbols(self) -> set:
        """Every symbol Bujji believes it holds, from the registry's own
        public surface. Memory -- deliberately the WEAKER side of the
        comparison; the broker always wins."""
        expected = set()
        registry = getattr(self, "_registry", None)
        if registry is None:
            return expected
        import asyncio as _asyncio

        for pg_id in registry.all_group_ids():
            try:
                reality = _asyncio.run(registry.get_group_reality(pg_id))
            except Exception:  # noqa: BLE001 -- one unreadable group never blinds the rest
                continue
            if reality.is_open:
                expected.update(reality.symbols)
        return expected

    def _reconcile_broker_positions(self, stage_label: str):
        """Compare belief against an UNFILTERED broker read, every pass.

        Never raises: a reconciliation that cannot run must not end a session
        that may hold an open position. But it also never reports success it
        did not establish -- a failure is recorded as UNKNOWN, which blocks
        new risk exactly as a CRITICAL divergence does.
        """
        import asyncio as _asyncio

        try:
            from bujji.production_runtime.eod_closure import discover_broker_positions
            from bujji.production_runtime.position_reconciliation import (
                SEVERITY_CRITICAL, SEVERITY_WARNING, UNKNOWN, reconcile)

            observed, read_detail = discover_broker_positions(self._broker, _asyncio.run)
            result = reconcile(self._expected_symbols(), observed)
        except Exception as exc:  # noqa: BLE001 -- fail CLOSED, never silently open
            self._logger.critical(
                "%s -- RECONCILIATION FAILED TO RUN (%s: %s). Treating position truth as "
                "UNKNOWN and blocking new risk.", stage_label, type(exc).__name__, exc)
            self._reconciliation_blocks_entry = True
            self._governor_result_summary["reconciliation_blocked"] = True
            return None

        self._last_reconciliation = result
        self._reconciliation_blocks_entry = result.blocks_new_risk
        record = result.to_dict()
        record["stage"] = stage_label
        record["at"] = self._clock().isoformat()
        record["broker_read"] = read_detail
        self._governor_result_summary.setdefault("reconciliations", []).append(
            {"stage": stage_label, "verdict": result.verdict,
             "severity": result.severity, "detail": result.detail})
        if result.blocks_new_risk:
            self._governor_result_summary["reconciliation_blocked"] = True
        self._persist_reconciliation(record)

        if result.severity == SEVERITY_CRITICAL and result.verdict == UNKNOWN:
            self._logger.critical(
                "%s -- POSITION TRUTH UNKNOWN (%s). New risk is BLOCKED: a read we could "
                "not perform is not evidence of safety.", stage_label, result.detail)
        elif result.severity == SEVERITY_CRITICAL:
            self._logger.critical(
                "%s -- %s. The broker holds exposure Bujji is NOT managing: it is not "
                "valued, has no stop, and no exit is scheduled for it. New risk BLOCKED.",
                stage_label, result.detail)
        elif result.severity == SEVERITY_WARNING:
            self._logger.warning("%s -- reconciliation divergence: %s",
                                 stage_label, result.detail)
        return result

    def _persist_reconciliation(self, record) -> None:
        """Durable evidence, beside the session's other artifacts. Never
        raises -- an audit trail must not end a session."""
        try:
            import json as _json

            store = getattr(self, "_store", None)
            session_dir = getattr(store, "session_dir", None)
            if session_dir is None:
                return
            path = session_dir / "position_reconciliation.jsonl"
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(_json.dumps(record, sort_keys=True, default=str) + "\n")
        except Exception as exc:  # noqa: BLE001
            self._logger.warning("reconciliation record not persisted: %s", exc)

    def _record_exit_outcome(self, stage_label: str, exit_status, execution) -> None:
        """Record what an attempted exit actually did, and escalate when it
        did not close the position.

        An exit was ATTEMPTED whenever an execution object exists. Whether it
        SUCCEEDED is `status`. Only STATUS_EXECUTED means the position was
        closed; everything else leaves exposure standing, and the session must
        carry that fact rather than a boolean that reads as success.

        This never mutates position state -- the executor and governor own
        that, and both already gate on broker truth. This is the observability
        and escalation half that was missing.
        """
        if execution is None:
            return
        from bujji.production_runtime.trade_lifecycle_executor import STATUS_EXECUTED

        record = {
            "stage": stage_label,
            "status": exit_status,
            "position_group_id": getattr(execution, "position_group_id", None),
            "action": getattr(execution, "action", None),
            "reason": getattr(execution, "reason", None),
            "orders_submitted": len(getattr(execution, "orders_submitted", ()) or ()),
        }
        if exit_status == STATUS_EXECUTED:
            self._governor_result_summary.setdefault("exits_confirmed", []).append(record)
            return

        # NOT closed. Loudly, every time -- an exit that did not close is the
        # condition under which a position keeps running against its thesis
        # while the session believes it acted.
        self._governor_result_summary.setdefault("exits_unresolved", []).append(record)
        self._governor_result_summary["has_unresolved_exit"] = True
        self._logger.critical(
            "%s -- EXIT DID NOT CLOSE THE POSITION: status=%s (%s). Exposure REMAINS. "
            "Management continues and EOD closure will attempt it again against "
            "broker truth.", stage_label, exit_status, record["reason"])

    def _execute_emergency_close(self, brake_reason, valuation, stage_label: str) -> None:
        """Actually flatten, then prove it against the broker.

        Reuses the governor's own forced-exit path (the hard-limit branch),
        which submits through the canonical broker-truth execution boundary.
        Nothing new is built here -- the brake previously called a method that
        only advanced a state machine.

        The session is NOT allowed to call itself closed unless the broker
        reports the group flat. If it does not, the summary carries
        CRITICAL_UNFLATTENED_POSITION and the runtime is deliberately left
        short of COMPLETE.
        """
        import asyncio as _asyncio

        selected = self._governor_result_summary.get("strategy_selected")
        result = None
        try:
            result = _asyncio.run(self._governor.evaluate_and_enforce_exit(
                valuation, selected, None, None, PositionHealthThresholds(),
                self._risk_budget()["requested_risk"],
                force_exit_reason=brake_reason,
            ))
        except Exception as exc:  # noqa: BLE001 -- a failed brake must still report, never crash silently
            self._logger.critical(
                "%s -- EMERGENCY CLOSE FAILED TO EXECUTE (%s: %s). The position may still "
                "be OPEN.", stage_label, type(exc).__name__, exc)
            self._governor_result_summary["emergency_close_execution_error"] = f"{type(exc).__name__}: {exc}"

        execution = getattr(result, "forced_execution", None)
        status = getattr(execution, "status", None)
        self._governor_result_summary["emergency_close_status"] = status
        self._logger.critical("%s -- EMERGENCY CLOSE execution status=%s", stage_label, status)

        flat, detail = self._broker_reports_flat()
        self._governor_result_summary["emergency_close_broker_flat"] = flat
        self._governor_result_summary["emergency_close_flat_detail"] = detail

        if flat is True:
            self._logger.critical("%s -- EMERGENCY CLOSE CONFIRMED FLAT by the broker.", stage_label)
            try:
                self._trading_brain_runtime.run_market_close_sequence()
            except Exception as exc:  # noqa: BLE001 -- already flat; a transition error must not mask that
                self._logger.warning("close sequence transition failed after a confirmed flat: %s", exc)
            return

        # flat is False (open legs) or None (could not establish). Both are
        # refusals to declare the session closed. None is NOT treated as flat.
        self._governor_result_summary["session_closed"] = False
        self._governor_result_summary["closure_reason"] = "CRITICAL_UNFLATTENED_POSITION"
        self._logger.critical(
            "%s -- CRITICAL_UNFLATTENED_POSITION: broker flat=%s (%s) after an emergency "
            "close. The session is NOT complete and the position is NOT confirmed closed. "
            "Operator intervention required.", stage_label, flat, detail)

    def _broker_reports_flat(self):
        """(True|False|None, detail). None means we could not establish it.

        UNFILTERED. Every other position read on this path goes through
        PositionRealityRegistry, which intersects broker positions with an
        in-memory table of registered symbols -- so a position Bujji never
        registered is invisible to it by construction. This asks the broker
        what it actually holds.

        A read that fails returns None, never False: "I could not ask" must
        never become "there is nothing there".
        """
        import asyncio as _asyncio

        try:
            positions = _asyncio.run(self._broker.get_open_positions())
        except Exception as exc:  # noqa: BLE001
            return None, f"position read failed: {type(exc).__name__}: {exc}"
        if positions is None:
            return None, "broker returned no position list"
        open_legs = [p for p in positions if int(p.get("qty", 0) or 0) > 0]
        if not open_legs:
            return True, "broker reports no open legs"
        return False, "open legs: " + ", ".join(
            f"{p.get('symbol')}x{p.get('qty')}" for p in open_legs)

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

    def _position_is_undefined_risk(self) -> bool:
        """Does the open position's shape bound its own loss?

        FAIL-CLOSED: an unrecognised or missing family is treated as
        UNDEFINED risk, so an unknown shape gets the tighter loop rather than
        the looser one. Being wrong in that direction costs a few extra quote
        calls; being wrong the other way costs an unwatched naked position.
        """
        from bujji.msi_trade_construction import taxonomy as _mtc

        family = self._governor_result_summary.get("strategy_selected")
        if not family:
            self._logger.warning(
                "POSITION_MANAGEMENT -- no strategy family recorded for the open position; "
                "assuming UNDEFINED risk and using the tighter cadence.")
            return True
        if family in _mtc.DEFINED_RISK_FAMILIES:
            return False
        if family not in _mtc.UNDEFINED_RISK_FAMILIES:
            self._logger.warning(
                "POSITION_MANAGEMENT -- family %r is in neither risk list; assuming "
                "UNDEFINED risk.", family)
        return True

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
        end_time_s = mgmt_cfg.get("monitor_until", "15:15:00")

        if not self._entry_prices:
            self._logger.info("POSITION_MANAGEMENT -- no open position; nothing to monitor.")
            return

        import datetime as _datetime
        import math
        import time as _time

        # CADENCE BY RISK CLASS (operator decision, 2026-08-20).
        #
        # The stop-loss, the daily loss limit and the emergency brake are all
        # evaluated once per pass, so the interval IS the width of the window
        # in which an unbounded loss can run unchecked. Measured over 168,194
        # real 5-minute NIFTY bars, the worst single 5-minute bar ranged 611.8
        # points -- ~Rs 39,764 against a real 240.95-point straddle credit, 2.5x
        # the stop, inside ONE interval. A defined-risk shape does not need the
        # tighter loop: its wings cap the loss whatever happens between passes.
        #
        # 60s is the floor worth asking for -- the tick source itself polls at
        # 60s, so a faster loop would revalue the same prices. Making it
        # genuinely continuous needs the websocket (certified, ~2.4 ticks/s,
        # still unwired).
        #
        # Cost against the shared FYERS budget is negligible: a pass quotes one
        # price per leg, so a 2-leg strangle at 60s is ~0.03 calls/s against a
        # host-wide ~8.3/s.
        naked = self._position_is_undefined_risk()
        base_interval_s = int(mgmt_cfg.get("cycle_interval_seconds", 300))
        # TIGHTER, NEVER WIDER. min() rather than reading a separate key
        # outright, for two reasons. A naked position must never be revalued
        # LESS often than a winged one whatever the two keys say -- the whole
        # point is a narrower unchecked window. And a caller who deliberately
        # lowers the base must not have that silently overridden by the other
        # key's default: a bare .get(..., 60) ignored an explicit
        # cycle_interval_seconds: 0 and made the suite sleep.
        interval_s = (min(base_interval_s,
                          int(mgmt_cfg.get("undefined_risk_cycle_interval_seconds", 60)))
                      if naked else base_interval_s)

        end_t = dt_time.fromisoformat(end_time_s)

        # THE CAP IS DERIVED, NOT A CONSTANT. `max_cycles: 78` was documented
        # as "6h15m / 5min, one session's worth" -- a number that silently
        # means something different the moment the interval changes. At 60s it
        # would have ended management after 78 MINUTES, leaving an open naked
        # position unwatched until the mandatory exit: strictly worse than the
        # cadence it was meant to improve. Same failure class as the token
        # fire-time constant and the four market-close constants.
        #
        # The configured value stays a FLOOR, so it still bounds a runaway
        # loop, but the effective cap always covers the real remaining window.
        configured_cap = int(mgmt_cfg.get("max_cycles", 78))
        max_cycles = configured_cap
        if interval_s > 0:
            now = self._clock()
            end_dt = _datetime.datetime.combine(now.date(), end_t, tzinfo=now.tzinfo)
            window_s = max(0.0, (end_dt - now).total_seconds())
            needed = math.ceil(window_s / interval_s) + 5   # +5: clock drift and a slow pass
            max_cycles = max(configured_cap, needed)

        cycles = 0
        self._governor_result_summary["management_cadence"] = {
            "interval_seconds": interval_s, "undefined_risk": naked,
            "max_cycles": max_cycles, "configured_max_cycles": configured_cap,
        }
        self._logger.info(
            "POSITION_MANAGEMENT -- monitoring every %ds until %s (max %d cycles); "
            "risk_class=%s.",
            interval_s, end_time_s, max_cycles,
            "UNDEFINED (naked -- tightened loop)" if naked else "DEFINED (wings cap the loss)",
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
        """Close the session against BROKER TRUTH, never against memory.

        This was: one management pass, then run_market_close_sequence() --
        four lines that transition POSTMARKET then COMPLETE. Nothing
        discovered broker positions, nothing cancelled working orders, and
        nothing asked the broker whether the account was flat before the
        session declared itself COMPLETE and the process exited. A position
        the management pass did not close -- a rejected exit, a timed-out
        exit, or a position the in-memory registry could not see -- carried
        overnight with nothing watching it.

        The management pass still runs first: it is the strategy-aware exit
        and it prices legs from the valuation. The closure machine then
        handles whatever it did not, from the broker's own account state.
        """
        self._stage = RunnerStage.EOD_CLOSE
        self._logger.info("EOD_CLOSE")
        self._run_one_management_pass("EOD_CLOSE")
        self._run_eod_closure()

    def _run_eod_closure(self) -> None:
        """Drive the closure state machine and gate COMPLETE on its verdict."""
        import asyncio as _asyncio

        from bujji.production_runtime.eod_closure import run_eod_closure

        try:
            result = run_eod_closure(
                broker=self._broker, place_fn=self._exit_place_fn, run_async=_asyncio.run,
                journal=self._journal, journal_db_path=str(self._journal_path),
                underlying=self._root.underlying, lot_size=self._root.exchange_lot_size,
                session_id=self._session_id, logger=self._logger,
                max_attempts=int(self._config.get("exit_policy", {}).get("eod_flatten_attempts", 2)),
            )
        except Exception as exc:  # noqa: BLE001
            # A closure machine that itself failed has NOT proven flatness.
            # Fail closed: never fall through to COMPLETE.
            self._logger.critical(
                "EOD CLOSURE RAISED (%s: %s) -- flatness is UNPROVEN. The session is NOT "
                "complete.", type(exc).__name__, exc)
            self._eod_closure_result = None
            self._governor_result_summary["eod_closure"] = {
                "state": "BROKER_TRUTH_UNKNOWN", "flat": None, "session_closed": False,
                "detail": f"closure raised: {type(exc).__name__}: {exc}",
            }
            self._governor_result_summary["session_closed"] = False
            self._governor_result_summary["closure_reason"] = "CRITICAL_UNFLATTENED_POSITION"
            return

        self._eod_closure_result = result
        self._governor_result_summary["eod_closure"] = result.to_dict()
        self._governor_result_summary["session_closed"] = result.session_closed

        if result.session_closed:
            # THE ONLY PATH TO COMPLETE. Positively verified flatness.
            self._trading_brain_runtime.run_market_close_sequence()
            return

        self._governor_result_summary["closure_reason"] = result.state
        self._logger.critical(
            "SESSION NOT CLOSED -- %s. Broker flat=%s. %s. The runtime is deliberately "
            "NOT advanced to COMPLETE: doing so would assert a flatness nothing has "
            "proven.", result.state, result.flat, result.detail)

    def _session_archive(self) -> None:
        self._stage = RunnerStage.SESSION_ARCHIVE
        self._governor.end_session()
        self._governor_result_summary["final_session_state"] = self._governor.state.value

        realized = 0.0
        for symbol in self._entry_prices:
            realized += self._broker.get_realized_pnl(symbol)

        # FINALIZATION REPORTS REALITY (2026-08-21). This passed a literal
        # empty tuple and a literal 0.0, so every summary.json asserted "no
        # open positions, no unrealized exposure" -- including on a session
        # that ended with a position still open. The per-session artifact of
        # record was structurally incapable of reporting the one condition an
        # operator most needs to see.
        #
        # The closure machine's own broker read is authoritative here. When it
        # could not establish truth, the positions are reported as UNKNOWN
        # rather than as empty.
        closure = getattr(self, "_eod_closure_result", None)
        final_positions = tuple(getattr(closure, "positions_after", ()) or ())
        if closure is not None and closure.flat is None:
            self._governor_result_summary["final_positions_status"] = "UNKNOWN"
        elif final_positions:
            self._governor_result_summary["final_positions_status"] = "OPEN"
        else:
            self._governor_result_summary["final_positions_status"] = (
                "FLAT" if closure is not None and closure.flat is True else "UNKNOWN")
        self._governor_result_summary["final_positions"] = list(final_positions)
        # RULE 8: an unresolved exit must survive into the session artifact.
        # A session whose stop-loss was REJECTED and whose EOD closure could
        # not prove flat must not read as a clean close just because the
        # closure machine happened to return.
        if self._governor_result_summary.get("has_unresolved_exit"):
            unresolved = self._governor_result_summary.get("exits_unresolved", [])
            self._logger.critical(
                "SESSION HAD %d UNRESOLVED EXIT(S): %s. final_positions_status=%s",
                len(unresolved), [u.get("status") for u in unresolved],
                self._governor_result_summary.get("final_positions_status"))
        self._recorder.finalize_session(
            final_positions=final_positions, realized_pnl=realized, unrealized_pnl=0.0)
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
        configured = artifacts_cfg.get("outcome_memory_store_path")
        if configured:
            path = REPO_ROOT / configured
        else:
            # DEFAULT BESIDE THE JOURNAL, not at a fixed production path.
            #
            # WHY (found by audit, 2026-08-20): this used to default to
            # `data/outcome_memory_events.jsonl` under REPO_ROOT regardless of
            # where the rest of the session's artifacts went. Tests point
            # `artifacts.journal_path` and `shadow_sessions_root` at a tmp_path
            # but had no reason to know about this third path, so from the
            # moment D-8 started writing outcome records, every test that ran a
            # full session with a closed position wrote into the REAL durable
            # store. 78 synthetic records (sessions OUTCOME-1/2/6, a
            # SHORT_STRANGLE family that is not even in SUPPORTED_FAMILIES) had
            # accumulated there, and they would have been the first thing a
            # freshly-hydrated risk memory learned from.
            #
            # Deriving from the journal's own directory fixes it structurally:
            # production journal lives in data/, so the production store stays
            # exactly where it was, while any caller that sandboxes its
            # artifacts sandboxes this too, automatically and forever.
            journal_default = "data/options_os_shadow_position_group_journal.db"
            journal_path = Path(artifacts_cfg.get("journal_path", journal_default))
            if not journal_path.is_absolute():
                journal_path = REPO_ROOT / journal_path
            path = journal_path.parent / "outcome_memory_events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._outcome_store = EventStore(str(path))
        return self._outcome_store

    def _shutdown(self) -> None:
        self._stage = RunnerStage.SHUTDOWN
        self._logger.info("SHUTDOWN -- stage_reached=%s", self._stage)
        # The tick feed owns a background OS thread and a real socket; a
        # session must not leave either running behind it. stop() is
        # idempotent and refuses restarts, so this is safe whatever state
        # the feed reached.
        feed = getattr(self, "_tick_feed", None)
        if feed is not None:
            try:
                feed.stop()
                self._logger.info("Tick feed stopped (connect_count=%s).",
                                  getattr(feed, "connect_count", None))
            except Exception as exc:  # noqa: BLE001 -- teardown must not mask the session result
                self._logger.warning("tick feed stop failed: %s", exc)
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
