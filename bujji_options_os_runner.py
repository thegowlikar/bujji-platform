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
import signal as _signal
import sys
import threading as _threading
import uuid
import time as _time
from datetime import time as dt_time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

REPO_ROOT = Path(__file__).resolve().parent

EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_RUNTIME_ERROR = 2
# A session that RAN but cannot prove its position is closed. Distinct from
# EXIT_RUNTIME_ERROR on purpose: nothing crashed, the machinery worked, and
# the answer it produced is "the book may still be open". systemd treats any
# non-zero exit as a unit failure, which is what fires OnFailure= ->
# bujji-alert@ -> ALERTS.jsonl + the operator's phone.
EXIT_UNSAFE_SESSION = 3

# PENDING_EVIDENCE: nothing is known to be wrong, but the session could not
# PROVE what it saw -- a verification that had to run did not reach a verdict.
# A session that cannot produce its own evidence is not a successful session,
# so it does not exit 0; a distinct code from UNSAFE so the operator reading
# the alert does not have to guess which of the two they have.
EXIT_PENDING_EVIDENCE = 4


class ConfigurationError(Exception):
    """Raised for any missing/invalid input required BEFORE a session
    may begin -- always maps to EXIT_CONFIG_ERROR, never silently
    patched with a guessed value."""


# The NIFTY option strike grid, in index points. Passed EXPLICITLY to
# `build_capture_universe` rather than relying on its default, and asserted
# equal to that default by test_chain_band_is_contained_by_construction -- so
# the tier arithmetic here and the universe the builder actually constructs
# can never disagree silently about how wide a strike is.
_UNIVERSE_GRID_STEP = 50


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


# Why a cycle's prices may not be used. A typed reason, because "blind" was
# one word covering four different operational situations.
PRICE_REASON_OK = "OK"
PRICE_REASON_NO_PROVIDER = "NO_PRICE_PROVIDER"
PRICE_REASON_PROVIDER_FAILED = "PRICE_PROVIDER_FAILED"
PRICE_REASON_QUALITY_REFUSED = "PRICE_QUALITY_REFUSED"


class LegPriceView:
    """This cycle's price evidence, and whether a decision may rest on it.

    A PLAIN CLASS, deliberately. `@dataclass` resolves annotations through
    `sys.modules[cls.__module__]`, which is None when this module is loaded by
    path -- as several test modules do -- and the whole file then fails to
    import. A dataclass here would buy nothing and cost the suite.

    `prices` is EMPTY unless `valid`. There is deliberately no field holding a
    fallback: the type itself makes "priced from something else" impossible to
    express, which is stronger than a convention a reader has to know.
    """

    __slots__ = ("valid", "reason", "detail", "quotes", "prices",
                 "priced_from_ticks", "quality")

    def __init__(self, valid, reason, detail="", quotes=None, prices=None,
                 priced_from_ticks=False, quality=None):
        self.valid = bool(valid)
        self.reason = reason
        self.detail = detail
        self.quotes = dict(quotes or {})
        # Enforced, not merely documented: an invalid view carries no prices,
        # whatever a caller passed.
        self.prices = dict(prices or {}) if valid else {}
        self.priced_from_ticks = bool(priced_from_ticks) and bool(valid)
        self.quality = quality

    def summary(self) -> dict:
        return {"valid": self.valid, "reason": self.reason,
                "detail": str(self.detail)[:300],
                "legs_quoted": len(self.quotes),
                "legs_priced": len(self.prices),
                "priced_from_ticks": self.priced_from_ticks}


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


_REAL_BROKER_MARGIN_MODES = ("fyers_uncertified", "fyers_certified")

# market_data types whose chain rows are NOT the execution venue's own
# symbols. Keyed on the config value, not on a provenance read at runtime:
# this must fail at STARTUP, before a chain is ever fetched.
_NON_BROKER_VOCABULARY_MARKET_DATA = {
    "replay_chain": "NSE bhavcopy FinInstrmNm (SOURCE_AUTHORITATIVE) -- real at NSE, "
                    "never shown valid at the FYERS execution venue",
    "observation_store": "no captured broker symbol at all (ABSENT) -- the store keys "
                         "options as 'NIFTY|<expiry>|<strike>|<type>'",
}


def _guard_provider_vocabulary(providers_cfg: dict, log=None) -> None:
    """Refuse a session that would send a non-broker symbol to a real FYERS
    endpoint.

    FOUND 2026-08-21, read-only audit. `providers.market_data` and
    `providers.margin` are selected INDEPENDENTLY -- nothing coupled them --
    and `config/options_os_shadow.yaml` ships `market_data: replay_chain`
    together with `margin: fyers_certified`. Gate B is provider-agnostic: it
    takes whatever chain it is handed and sends those symbols to the live
    SPAN endpoint. So a shadow session was one `--bhavcopy-path` away from
    quoting NSE-form symbols at FYERS.

    It has not caused a visible incident, and that is the uncomfortable part:
    FYERS almost certainly rejects the un-prefixed form, so the path fails
    closed BY ACCIDENT (verified=False -> MARGIN_NOT_CERTIFIED -> VETO)
    rather than by design. That is precisely how the original vocabulary
    split survived for months.

    The resolver already refuses the symbol (operator decision (a)). This
    refuses the SESSION, at startup, with the pairing named -- because an
    entry blocked leg-by-leg deep inside Gate B reads like a market
    condition, while a config that cannot start reads like what it is.
    """
    market_data = str((providers_cfg.get("market_data") or {}).get("type") or "replay_chain").strip().lower()
    margin_block = providers_cfg.get("margin", {"type": "simulated"})
    margin = str((margin_block or {}).get("type", "simulated")).strip().lower() \
        if isinstance(margin_block, dict) else ""
    if margin in _REAL_BROKER_MARGIN_MODES and market_data in _NON_BROKER_VOCABULARY_MARKET_DATA:
        raise ConfigurationError(
            f"providers.market_data.type={market_data!r} with providers.margin.type={margin!r} "
            f"would send non-broker symbols to the live FYERS SPAN endpoint: "
            f"{_NON_BROKER_VOCABULARY_MARKET_DATA[market_data]}. "
            f"Use providers.margin: simulated for this market_data type, or "
            f"providers.market_data: fyers_live for real margin. Refusing to start."
        )


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
    master_dir = Path(cache_dir) if cache_dir else REPO_ROOT / "data" / "instrument_master"
    master = InstrumentMaster(master_dir, log)

    # FRESHNESS IS ASSERTED, NOT ASSUMED.
    #
    # `lot_size_for()` is cache-only and synchronous BY DESIGN -- its docstring
    # says the trading startup path "must not grow a network dependency" -- and
    # it delegates refreshing to "the capture path's `_ensure_fresh()`", which
    # is a DIFFERENT systemd unit. Nothing checked that the other unit had
    # actually run. If the capture path failed for a week, this read a
    # week-old master and said nothing.
    #
    # Lot size multiplies EVERY order. This exact harm has already been paid
    # once: the 2026-07-19 audit found the config saying 75 while the master
    # said 65, and every constructed quantity was 15.4% oversized. A stale
    # master reintroduces it across an exchange lot-size revision, which is
    # precisely when the number changes.
    #
    # The window defaults to 96h rather than 24h because the refresher runs on
    # WEEKDAYS: a Monday session legitimately reads a master last written on
    # Friday morning, ~72h earlier. 96h covers that plus one holiday. It is not
    # a comfort setting -- past it, the session refuses to size orders.
    max_age_hours = float(session_cfg.get("instrument_master_max_age_hours", 96.0))
    master_file = master_dir / f"fyers_fo_{session_cfg.get('exchange', 'NSE')}.csv"
    if master_file.exists():
        import time as _time

        age_hours = (_time.time() - master_file.stat().st_mtime) / 3600.0
        log.info("INSTRUMENT MASTER -- %s is %.1fh old (limit %.0fh).",
                 master_file.name, age_hours, max_age_hours)
        if age_hours > max_age_hours:
            raise RuntimeError(
                f"instrument master {master_file} is {age_hours:.1f}h old, past the "
                f"{max_age_hours:.0f}h limit -- refusing to size orders from a stale "
                f"lot size. Lot size multiplies every order and changes at exchange "
                f"revisions. Refresh the master (the capture path does this daily) "
                f"or raise session.instrument_master_max_age_hours deliberately."
            )

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


def _position_truth_for(owner):
    """This process's one reader for "what does the account hold".

    Module-level rather than a method so it is reachable from a minimal
    stand-in for the runner. OptionsOSRunner assembles config, feeds, a broker
    and a state machine; the position read is three lines of decision, and the
    existing tests exercise it by calling the unbound method against a stub.
    A read that can only be tested by constructing the whole runner is a read
    that will stop being tested.

    The reader is cached on the owner so every caller in a session shares one
    labelled source; a stand-in that refuses attribute assignment simply pays
    for a new one.
    """
    reader = getattr(owner, "_broker_truth_reader", None)
    if reader is None:
        from bujji.broker_truth import for_broker

        reader = for_broker(owner._broker)
        try:
            owner._broker_truth_reader = reader
        except AttributeError:      # a slotted or frozen stand-in
            pass
    return reader


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
        # Position groups a PRIOR process filled today. Snapshotted at
        # startup before any mint; see where it is assigned.
        self._prior_fills_today = []
        self._prior_fills_unreadable = False
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
        self._analytical_snapshot_ref = None
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
        self._tick_journal = None
        # Set by _run_eod_closure; read by _session_archive so finalization
        # reports the broker's own view instead of a hardcoded flat.
        self._eod_closure_result = None
        self._exit_place_fn = None
        # Reconciliation state. Blocks default to FALSE only because no
        # position can exist before the first pass runs; the first
        # reconciliation sets the real value.
        self._last_reconciliation = None
        # UNKNOWN UNTIL ESTABLISHED, never the other way round.
        #
        # This started False, so before any reconciliation had run the gate in
        # `_data_quality_permits_entry` read "not blocked" and the first entry
        # of a session could be taken with NO broker-truth check at all. That
        # is UNKNOWN silently treated as FLAT, at the one moment it matters
        # most: a process that crashed yesterday, or was restarted mid-session,
        # begins with an empty in-memory registry and no knowledge of what the
        # broker is already holding. Taking a fresh position on top of an
        # unmanaged one is the compounding case reconciliation exists to stop.
        #
        # `_continuous_session` does reconcile at the top of every cycle, so
        # production happened to be covered -- but `_entry_window` never
        # reconciles at all, and this runner has twice shipped a gate that was
        # live on one branch and dead on the other. The default is now the safe
        # one, and `_data_quality_permits_entry` establishes truth on demand.
        self._reconciliation_blocks_entry = True
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
        # Optional intraday tick source. When absent, `_current_leg_quotes`
        # returns an INVALID view carrying no prices at all -- it does not
        # fall back to entry prices, and there is no longer a second accessor
        # that could. (This comment described the removed P0 as current
        # behaviour until 2026-08-24.)
        self._price_provider = None
        self._tick_feed = None  # set only by tick_source.type=websocket
        # Evidence only: how much of the REST chain the tick path could
        # have priced, and where they disagreed. Never a decision input.
        self._tick_rest_coverage = None
        # UNIVERSE-FIRST SUBSCRIPTION (see _ensure_universe_subscribed).
        self._universe = None
        self._universe_requested = ()
        self._universe_error = None
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
            # Only after it RETURNED. If _eod_close raises partway, the abort
            # path below re-runs it deliberately: run_eod_closure discovers
            # broker positions before it submits anything, so a retry exits
            # only what is STILL open. Re-entering is therefore safe, and
            # skipping the retry would strand exactly the position this whole
            # path exists to catch.
            self._eod_close_completed = True
            self._session_archive()
        except BaseException:
            # _eod_close() IS THE ONLY BROKER-TRUTH FLATTEN, and it sat in
            # this try body. Any exception raised by the session -- from the
            # entry window, either management loop, the continuous loop, or
            # _eod_close itself -- jumped straight past it to `finally`, and
            # `_shutdown()` deliberately does not flatten ("this method only
            # logs and releases whatever was constructed in _startup()").
            #
            # So an unhandled exception at 11:00 with a naked short open left
            # the position at the broker with NO flatten ever attempted. The
            # unit did fail and the operator was alerted -- but an autonomous
            # system's answer to "I crashed while short" cannot be to leave it
            # and send a message.
            #
            # Attempting is strictly better than not attempting, and this
            # cannot make anything worse: it is idempotent (guarded on
            # _eod_close_completed), it never runs when no position was
            # opened, and it never masks the original exception.
            self._flatten_on_abort()
            raise
        finally:
            self._shutdown()
        return self._governor_result_summary

    def _flatten_on_abort(self) -> None:
        """Last-resort broker-truth flatten when the session is dying.

        Never raises: it is called from an except block that is about to
        re-raise the real failure, and a secondary exception here would
        replace the diagnosis with its own.
        """
        if getattr(self, "_eod_close_completed", False):
            return  # the ordinary close already ran; nothing to redo
        opened = bool(getattr(self, "_entry_prices", None)) or bool(
            getattr(self, "_canonical_position_id", None))
        if not opened:
            return  # nothing was ever opened, so nothing can be left open

        self._governor_result_summary["aborted_before_eod_close"] = True
        self._logger.critical(
            "SESSION ABORTED WITH A POSITION OPEN -- attempting the broker-truth "
            "flatten that the normal EOD path never reached.")
        try:
            self._eod_close()
        except BaseException as exc:  # noqa: BLE001 -- must not replace the real failure
            self._governor_result_summary["abort_flatten_error"] = (
                f"{type(exc).__name__}: {exc}")
            self._logger.critical(
                "ABORT FLATTEN FAILED (%s: %s). THE POSITION MAY STILL BE OPEN "
                "AT THE BROKER. Operator intervention required.",
                type(exc).__name__, exc)

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
        # Before ANY provider is constructed -- see _guard_provider_vocabulary.
        _guard_provider_vocabulary(providers_cfg, self._logger)
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

        # PRIOR TICK EVIDENCE, BEFORE ANY ENTRY.
        #
        # AFTER the unresolved-order refusal above: if this session is going to
        # refuse outright over in-flight orders, there is nothing to inspect
        # for. (It also keeps that raise adjacent to the recovery call it
        # belongs to, which a windowed source assertion in
        # tests/test_journaled_execution.py depends on -- the same 1200-char
        # window this insertion pushed it out of once already.)
        #
        # A journal an earlier process left UNSEALED is that process's death
        # certificate: `_shutdown()` runs from a `finally` and covers orderly
        # termination only, so an unsealed file means SIGKILL, power loss or an
        # OOM kill. `recover_unsealed` reports it INCOMPLETE by construction --
        # what survived is real, but no inspection can establish it is all of
        # it.
        #
        # Attached to the evidence chain before entry, so a session can never
        # take new risk while silently carrying an unexplained prior death. A
        # CORRUPT prior journal additionally makes THIS session unsafe: see
        # session_safety_verdict.
        from bujji.production_runtime.tick_evidence import inspect_prior_journals

        self._prior_tick_evidence = inspect_prior_journals(
            REPO_ROOT / "data" / "tick_journal", self._session_id, self._logger)
        self._governor_result_summary["prior_tick_journals"] = (
            self._prior_tick_evidence.to_dict())

        # HISTORICAL UNRESOLVED EXPOSURE, AS A RECOVERY STATE.
        #
        # The journal can still record open exposure from an earlier trading
        # day -- an exit that was never journaled leaves its group OPEN
        # forever. Until now that surfaced as a margin-subsystem crash:
        # `read_all_group_ids` has no date scope, the runner passes empty
        # contract maps, `project_whole_book_to_margin_legs` raises on legs it
        # cannot map, and the session reported "margin_snapshot unavailable".
        #
        # The refusal was right; the shape was wrong. It named nothing an
        # operator could act on and it happened by accident. This makes it
        # explicit, names the groups and legs, and carries the reconciliation
        # a person has to perform -- and it runs HERE, at startup, so entry is
        # refused before the margin path is ever reached.
        from bujji.production_runtime.historical_exposure import (
            inspect as _inspect_historical_exposure)

        self._historical_exposure = _inspect_historical_exposure(
            self._journal, self._as_of_date,
            _position_truth_for(self).read(), self._logger)
        self._governor_result_summary["historical_exposure"] = (
            self._historical_exposure.to_dict())

        # ORPHAN EXPOSURE, RECONCILED BEFORE ENTRY.
        #
        # A previous process may have found the broker holding a position no
        # journal group claims, flattened it, and died before the flatten was
        # confirmed. That record is session-scoped -- there is no position
        # group to attach it to -- so `historical_exposure` above, which walks
        # position groups, cannot see it. This runs beside it and asks the
        # same question of the other scope.
        #
        # It reads EVERY session scope, not just this one's: an orphan is
        # exactly the thing a prior session failed to finish.
        from bujji.production_runtime.orphan_exposure import (
            inspect as _inspect_orphan_exposure)

        self._orphan_exposure = _inspect_orphan_exposure(
            self._journal, _position_truth_for(self).read(), self._logger)
        self._governor_result_summary["orphan_exposure"] = (
            self._orphan_exposure.to_dict())

        # THE DAY'S ONE STRATEGY, ACROSS A RESTART.
        #
        # AFTER the unresolved-order refusal above, deliberately: if this
        # session is going to refuse outright over in-flight orders, there is
        # nothing to ask about prior fills.
        #
        # Read ONCE, here, before this session can mint anything. A live query
        # later would match this session's OWN fills and refuse its legitimate
        # retries. This is a snapshot of what a PRIOR process did.
        #
        # Reconciliation already covers the still-open case (empty registry ->
        # BROKER_ONLY -> CRITICAL -> blocks). This covers the one it cannot:
        # the position was entered AND exited before the crash, so the account
        # is genuinely flat and the broker cannot tell "never traded today"
        # from "traded and closed today". See prior_fills.py for the rest.
        from bujji.production_runtime.prior_fills import prior_fills_snapshot

        self._prior_fills_today, self._prior_fills_unreadable = prior_fills_snapshot(
            self._journal, self._as_of_date, self._logger)
        self._governor_result_summary["prior_fills_today"] = list(self._prior_fills_today)
        if self._prior_fills_today:
            self._logger.critical(
                "STARTUP -- this account already filled position group(s) %s on %s. "
                "This process is a RESTART after the day's strategy was already "
                "deployed. Entry will be refused; management and closure continue.",
                self._prior_fills_today, self._as_of_date)

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
        # journal + session_id: every exit this executor places is journaled
        # BEFORE placement and reconciled against prior attempts. Without them
        # it falls back to unjournaled placement, which is the defect M4b
        # closes -- tests/test_executor_journals_exits.py ratchets that the
        # production runner supplies them.
        self._executor = TradeLifecycleExecutor(self._broker, self._registry, self._lifecycle_runtime,
                                                place_fn=_exit_place_fn,
                                                journal=self._journal,
                                                session_id=self._session_id,
                                                logger=self._logger,
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

        # M4: BIND THE DURABLE LIFECYCLE AUTHORITY.
        #
        # The governor's in-memory tracker is now a cache. Every transition it
        # makes is journaled FIRST, into the same position_group_events stream
        # that carries position lifecycle, as a SESSION_TRANSITION under a
        # SESSION: identity. Without this binding the governor still runs, but
        # nothing is durable and the session cannot be reconstructed after a
        # restart -- which `lifecycle_unjournaled_count()` reports and the
        # safety verdict refuses to certify.
        self._governor.bind_lifecycle_journal(
            self._journal, self._session_id, self._clock, self._logger)

        # The session's own first transition, journaled like every other.
        from bujji.production_runtime.session_lifecycle import record_transition
        from bujji.production_runtime.trading_session_governor.session_trading_state import (
            TradingSessionState as _TSS)

        try:
            record_transition(
                self._journal, self._session_id, _TSS.INITIALIZING,
                _TSS.ANALYSING_MARKET, cause="session_start",
                evidence_ref=f"startup:{self._as_of_date}",
                clock=self._clock, logger=self._logger)
        except Exception as exc:  # noqa: BLE001 -- recorded, never fatal to startup
            self._logger.critical(
                "LIFECYCLE -- could not journal the session's first transition "
                "(%s: %s). This session cannot be reconstructed after a restart.",
                type(exc).__name__, exc)
            self._governor_result_summary["lifecycle_journal_error"] = (
                f"{type(exc).__name__}: {exc}")

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
            # OBSOLETE KEY, REFUSED RATHER THAN IGNORED. `strike_count` used
            # to BE the eligible selection band: a chain-request constant that
            # decided, independently of anything subscribed, what a strategy
            # could range over. It is replaced by the universe's own
            # `selection_band_points`. Leaving it readable would let a stale
            # config silently keep the old authority, so a config that still
            # carries it fails to start and says what to write instead.
            if "strike_count" in market_data_cfg:
                raise ConfigurationError(
                    "providers.market_data.strike_count is obsolete -- the chain "
                    "request now derives its width from the canonical universe. "
                    "Replace it with providers.market_data.selection_band_points "
                    f"(index points; {int(market_data_cfg['strike_count']) * 50} "
                    "preserves the current width)."
                )
            self._market_data_provider = LiveChainProvider(
                chain_broker, underlying=underlying,
                # THE UNIVERSE DECIDES THE REQUEST. Resolved lazily: the
                # universe is centred on spot, which is not known when this
                # provider is constructed.
                universe_source=lambda: self._universe,
                logger=self._logger,
                # How old the live book may be. Defaults live on the provider;
                # an operator may tighten them, and max_age below refresh_after
                # is rejected there rather than silently accepted.
                refresh_after_seconds=market_data_cfg.get("chain_refresh_after_seconds"),
                max_age_seconds=market_data_cfg.get("chain_max_age_seconds"),
                # WIRED, not merely available. Without this the resolver would
                # be one more thing this codebase built and never called.
                expiry_resolver=self._master_expiry_resolver(),
            )
        else:
            bhavcopy_path = market_data_cfg.get("bhavcopy_path")
            if not bhavcopy_path:
                raise ConfigurationError(
                    "providers.market_data.bhavcopy_path is required (config or --bhavcopy-path) -- refusing to guess."
                )
            self._market_data_provider = ReplayChainProvider(bhavcopy_path=bhavcopy_path, underlying=underlying)

        # DEFERRED, NOT SKIPPED. The thesis derivation used to run here, inside
        # this block. It now runs after the tick source exists and the universe
        # is subscribed -- see the block at the end of this method. Declared
        # before the branch so no path can reach the check with it unset.
        derive_thesis_regime = False
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
            derive_thesis_regime = True
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
            derive_thesis_regime = True
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
            # THE BARE TOKEN. FyersTickFeed builds the "{app_id}:{token}" form
            # ITSELF at the SDK boundary (fyers_ws.py, FyersDataSocket
            # construction). Passing the joined form here produced
            # "APPID:APPID:TOKEN", which the SDK cannot authenticate: the
            # symbol-token lookup fails, NO symbol is ever subscribed, and the
            # feed sits there reporting itself connected while delivering
            # nothing.
            #
            # That is exactly what 2026-08-21 looked like: tick_feed_error at
            # 09:52:35, "tick feed priced 0/2 legs" on EVERY cycle,
            # cycles_priced_from_ticks=0, session_blind=true. Three blind
            # cycles then tripped the emergency brake on a naked short
            # strangle -- so this one argument is the head of that whole
            # incident chain.
            #
            # The comment that used to sit here quoted the fyers_ws module
            # docstring correctly and applied it one layer too high. The two
            # callers that get it right pass the bare token:
            # run_live_shadow.py:195 hands over live_tick_credentials()
            # verbatim, and scripts that drive FyersDataSocket directly add
            # exactly one prefix -- which is the format the 2026-08-20
            # websocket certification proved against REST (ws ltp == REST ltp,
            # 0.0000% deviation).
            # THE EVIDENCE LAYER IS OPENED BEFORE THE FEED IS. A session that
            # trades on ticks it did not record cannot reconstruct its own
            # decisions afterwards, which is the property the whole safety
            # contract rests on -- so the journal is not optional here, and a
            # journal that cannot be opened fails the session rather than
            # quietly running blind.
            from bujji.tick_journal import TickJournal

            journal_dir = REPO_ROOT / "data" / "tick_journal" / self._as_of_date
            self._tick_journal = TickJournal(
                journal_dir / f"{self._session_id}.jsonl",
                session_id=self._session_id, logger=self._logger)
            self._logger.info(
                "TICK JOURNAL open at %s -- every payload is recorded verbatim "
                "before any field is read from it.", self._tick_journal.path)

            # ONE FEED, ONE JOURNAL, ONE PROJECTION.
            #
            # `journal` makes the verbatim callback durable BEFORE any field is
            # read; `session_id` travels onto every typed quote so a decision's
            # evidence can be tied back to the session that captured it. No
            # second feed, no second store: the same callback produces both the
            # journal record and the quote.
            self._tick_feed = FyersTickFeed(
                app_id, token, self._logger,
                log_path=str(REPO_ROOT / "logs"),
                journal=self._tick_journal,
                session_id=self._session_id)
            self._tick_feed.start()
            watchdog = TickSilenceWatchdog(
                silence_threshold_seconds=float(tick_cfg.get("silence_threshold_seconds", 120.0)),
                logger=self._logger)
            self._price_provider = WebsocketTickProvider(
                self._tick_feed, watchdog,
                LiveTickProvider(data_broker, _asyncio.run),
                max_tick_age_seconds=float(tick_cfg.get("max_tick_age_seconds", 90.0)),
                # THE SESSION LOGGER, not the provider's private default.
                #
                # WebsocketTickProvider falls back to
                # logging.getLogger("bujji.websocket_tick_provider"), which
                # this session never configures -- so everything it says goes
                # nowhere. The watchdog on the line above was already given
                # the session logger; the provider was not, and that
                # inconsistency was invisible until it mattered.
                #
                # It mattered on 2026-08-21. The feed priced 0 of 2 legs for
                # three consecutive cycles and the provider's own explanation
                # -- "websocket subscribe failed (...); REST fallback covers
                # ..." (intraday_price_provider.py:230) and "websocket priced
                # 0/2 legs; falling back to REST for ..." -- never reached the
                # journal. Three blind cycles and an emergency brake, with the
                # diagnosis sitting in an unrouted logger. A component that
                # cannot explain itself fails silently.
                logger=self._logger,
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
                "Tick source: NONE. Every management cycle will be BLIND -- no price "
                "can be obtained, so the cycle produces NO valuation, suspends "
                "price-dependent management and escalates to the emergency brake. "
                "Positions are NOT revalued against their entry prices; that was the "
                "P0 removed in 21742eb, and this warning described it as current "
                "behaviour until 2026-08-24. Set providers.tick_source.type."
            )

        self._session_cfg = session_cfg

        # ---- THE FIRST CYCLE IS NO LONGER TICK-BLIND. ----
        #
        # The thesis derivation used to run inside the regime block ABOVE,
        # before the tick source was constructed and long before the universe
        # was subscribed -- subscription happened in the entry gate. So the
        # first MarketSnapshot of every session was built with no tick path in
        # existence, and `tick_rest_coverage()` could only ever report zero
        # coverage on it. That was structural, not a timing accident: no amount
        # of waiting would help, because nothing had subscribed.
        #
        # WHY THIS IS SAFE TO MOVE. The tick block above is documented as
        # constructed "AFTER the regime block, on purpose", and that is true --
        # but the dependency is on `self._intelligence_broker`, which is built
        # in the regime block and STILL IS. It was never a dependency on the
        # regime having been DERIVED. Only the derivation call moved; the
        # broker construction did not.
        #
        # Ordering now: intelligence broker -> tick source -> universe
        # subscribed -> derivation. The derivation runs a warm-up of spaced
        # spot polls (minutes, when configured), so ticks accumulate while it
        # warms rather than arriving after every decision was already made.
        # `_await_market_open()` has already run by here (early in _startup),
        # so this subscribes into an open market, not a closed one.
        if derive_thesis_regime:
            self._pre_subscribe_for_first_derivation()
            self._regime_provider = self._build_market_thesis_regime_provider()

    def _pre_subscribe_for_first_derivation(self) -> None:
        """Subscribe the universe before the first snapshot, WITHOUT letting a
        startup-time failure latch.

        `_ensure_universe_subscribed` is idempotent by an early return on
        `_universe_error` being set, which is exactly right at entry time and
        exactly wrong here: a transient failure at startup would latch the
        error and refuse entry for the whole session, on a path that
        previously had no opportunity to fail at all. So a failed pre-subscribe
        is rolled back to UNATTEMPTED and the entry gate retries it on its own
        terms, reaching the identical behaviour this method never had.

        A SUCCESSFUL pre-subscribe is deliberately left latched: that is the
        idempotency doing its job, and the entry gate's later call becomes the
        no-op it should be rather than a second subscription.
        """
        try:
            self._ensure_universe_subscribed()
        except Exception as exc:  # noqa: BLE001 -- see below; never fatal at startup
            self._logger.warning(
                "PRE-SUBSCRIBE -- universe subscription raised at startup (%s: %s). "
                "The entry gate will attempt it again.", type(exc).__name__, exc)
        if self._universe is None:
            # Roll back to UNATTEMPTED so the entry gate is not answering a
            # question this earlier, more fragile attempt already failed.
            if self._universe_error is not None:
                self._logger.warning(
                    "PRE-SUBSCRIBE -- could not subscribe the universe before the "
                    "first derivation (%s). Cleared so the entry gate retries; the "
                    "first snapshot will be tick-blind, which is recorded rather "
                    "than assumed.", self._universe_error)
                self._universe_error = None
            return
        self._logger.info(
            "PRE-SUBSCRIBE -- universe subscribed BEFORE the first market snapshot; "
            "the first derivation can see tick evidence.")

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
        # ONE MARKET-DATA PATH, MEASURED BEFORE IT IS MERGED.
        #
        # Bujji reads the market twice: this REST-fed snapshot feeds regime
        # derivation and strike selection, and a websocket tick path feeds
        # position pricing. `quote_source` is what would let the adapter see
        # the tick path -- it has existed on MarketDataAdapter all along, and
        # was never supplied, so `live_quotes()` had zero callers anywhere in
        # the repository and the two paths could not even be compared.
        #
        # IT IS WIRED FOR EVIDENCE, NOT FOR DECISIONS. Nothing in the snapshot
        # is built from it; `build_snapshot()` is unchanged. It exists so
        # `tick_rest_coverage()` can answer the question that has to be
        # answered before a merge is honest: of the chain REST returned, how
        # many symbols did the tick path hold, and did they agree?
        #
        # LAZY ON PURPOSE. `self._tick_feed` is assigned AFTER this method
        # first runs (the regime provider is built at setup, the tick provider
        # some 60 lines later), and the universe is not subscribed until the
        # entry gate. So the first derivation of a session is tick-blind by
        # construction and this returns {}. On a continuous session's later
        # cycles the feed exists and has been subscribed, and the same closure
        # then reports real coverage. Reading the attribute at call time
        # rather than binding it here is what makes both true.
        #
        # THE FEED, NEVER THE PROVIDER. `WebsocketTickProvider.get_quotes()`
        # falls back to REST and labels the result REST_FALLBACK. Sourcing
        # this from the provider would compare REST against REST and report
        # perfect agreement -- the most misleading possible answer.
        def _tick_quotes(symbols):
            feed = getattr(self, "_tick_feed", None)
            if feed is None:
                return {}
            try:
                everything = feed.all_quotes()
            except Exception:  # noqa: BLE001 -- evidence collection never ends a session
                return {}
            wanted = set(symbols)
            return {sym: q for sym, q in everything.items() if sym in wanted}

        adapter = MarketDataAdapter(
            broker, self._clock, underlying=self._root.underlying,
            quote_source=_tick_quotes,
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
        # Measured beside the snapshot, never folded into it. On the first
        # derivation this records tick_covered=0 with tick_source_wired=True,
        # which is the honest reading: a feed was available to ask and had
        # nothing yet, as distinct from no feed being wired at all.
        try:
            self._tick_rest_coverage = adapter.tick_rest_coverage(snapshot)
            self._governor_result_summary["tick_rest_coverage"] = self._tick_rest_coverage
        except Exception as exc:  # noqa: BLE001 -- a measurement never ends a session
            self._logger.warning("tick/REST coverage measurement failed (%s)", exc)
            self._tick_rest_coverage = None
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
        # THE BACK-REFERENCE THAT MAKES REPLAY POSSIBLE. The thesis artifact
        # already records `regime_handed_to_selector`, so thesis -> regime is
        # linked. The reverse was not: a selection record named a regime and
        # nothing else, and matching it back to the thesis meant guessing by
        # regime VALUE -- ambiguous exactly when it matters, since a session
        # routinely records several cycles carrying the identical regime (the
        # real 2026-08-20 package has three UNKNOWN/EXPANSION selections).
        # Carrying the assessment_id forward makes the link an identity rather
        # than a coincidence. Nothing decides from it; it is evidence only.
        self._analytical_snapshot_ref = getattr(thesis, "assessment_id", None)
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

        # THE UNIVERSE IS BUILT BEFORE THE FIRST CHAIN IS REQUESTED, because
        # the request derives its width, its expiry and its admissible
        # contracts from the universe. This used to run the other way round --
        # a chain pulled here at startup, and a universe built lazily hours
        # later at the first entry attempt -- so the pre-market book was
        # requested against nothing authoritative.
        #
        # Spot for centring comes from the broker's own endpoint, not from this
        # chain: deriving spot from the chain and the chain from the universe
        # is circular.
        self._ensure_universe_built()
        if self._universe is None:
            raise ConfigurationError(
                f"the canonical universe could not be built pre-market "
                f"({self._universe_error}) -- refusing to start a session whose "
                f"chain requests would have nothing authoritative behind them.")

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
            # RECONCILE ON EVERY CYCLE OF THE LOOP THAT ACTUALLY RUNS ALL DAY.
            #
            # This loop breaks at observe_until (15:30), NOT at entry_cutoff --
            # past the cutoff it `continue`s, "observation only, all day". So
            # on a no-entry day it occupies the ENTIRE session, and
            # _position_management() (whose own loop ends at monitor_until,
            # 15:15) runs only after it, when that deadline has already
            # passed. Making _position_management unconditional was therefore
            # necessary but nowhere near sufficient: the clock that ticks all
            # day lives HERE.
            #
            # Proven live 2026-08-21: a NO_TRADE session produced zero
            # position_reconciliation.jsonl records.
            #
            # Cost is one unfiltered broker read per decision cycle (300s),
            # against a host-wide ~8.3/s budget.
            self._reconcile_broker_positions(f"CONTINUOUS[{cycles}]")
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
            # A REGISTERED ORPHAN IS A LIVE POSITION AND MUST NOT WAIT FOR
            # THIS LOOP TO END.
            #
            # `_orphan_position_live` was written in three places and read in
            # NONE -- its own comment says it is "what tells [continuous mode]
            # a live position exists regardless", and nothing consulted it.
            # This is the reader.
            #
            # It matters because this loop breaks at observe_until (15:30),
            # not at entry_cutoff: past the cutoff it `continue`s, observing
            # all day. So an orphan at 09:35 left naked legs sitting while the
            # loop ran on, and `_position_management()` -- which follows the
            # loop -- would then start at 15:30 with monitor_until already
            # past (15:15) and mandatory_exit_time (15:15) already missed. It
            # runs one pass before its own deadline check ends it.
            if self._orphan_position_live:
                self._logger.critical(
                    "ORPHANED LEGS ARE LIVE -- leaving the entry loop at cycle %d so "
                    "position management starts NOW rather than at observe_until.",
                    cycles)
                break

        # An ORPHANED partial entry is a live position even though
        # _attempt_entry returned False. Without this it was never managed at
        # all: the loop simply moved on to post-cutoff observation while a
        # naked leg sat at the broker.
        # THE MONITORING LOOP RUNS WHETHER OR NOT WE ENTERED (2026-08-21).
        #
        # This was gated on `entered or _orphan_position_live`, so on a
        # no-entry day the loop never started -- and with it, reconciliation.
        # Proven live today: a NO_TRADE session produced ZERO
        # position_reconciliation.jsonl records, because the only caller of
        # _run_one_management_pass (which reconciles first) is this loop.
        #
        # That is precisely backwards. The case reconciliation exists for is
        # "Bujji believes it holds nothing while the broker holds something",
        # and `entered` is False in exactly that case. Moving reconciliation
        # to the top of the pass (0d681f8) fixed the inner gate and left this
        # outer one closed, so the detector still could not run.
        #
        # Each pass with no position costs ONE unfiltered broker read and then
        # returns at the `if not self._entry_prices` guard -- no valuation, no
        # exit evaluation, no order path. One call per cadence against a
        # host-wide ~8.3/s budget.
        self._position_management()

        # Post-trade / post-cutoff observation: the position may be closed;
        # Bujji is not. The organism watches until the market ends.
        self._stage = RunnerStage.MARKET_SESSION
        phase = "POST_TRADE_OBSERVATION" if entered else "OBSERVATION"
        while cycles < max_cycles:
            now = self._clock()
            if now.time() >= observe_until:
                break
            if termination_requested():
                # SIGTERM lands HERE, in the loop that occupies the whole
                # day, and exits it the normal way -- so control falls
                # through to _eod_close() and the position is flattened and
                # archived rather than abandoned. See
                # install_termination_handlers.
                self._logger.critical(
                    "ORDERLY STOP requested -- leaving the observation loop and proceeding "
                    "to EOD closure with the position (if any) still open.")
                break
            cycles += 1
            # RECONCILE ON EVERY CYCLE OF THE LOOP THAT ACTUALLY RUNS ALL DAY.
            #
            # This loop breaks at observe_until (15:30), NOT at entry_cutoff --
            # past the cutoff it `continue`s, "observation only, all day". So
            # on a no-entry day it occupies the ENTIRE session, and
            # _position_management() (whose own loop ends at monitor_until,
            # 15:15) runs only after it, when that deadline has already
            # passed. Making _position_management unconditional was therefore
            # necessary but nowhere near sufficient: the clock that ticks all
            # day lives HERE.
            #
            # Proven live 2026-08-21: a NO_TRADE session produced zero
            # position_reconciliation.jsonl records.
            #
            # Cost is one unfiltered broker read per decision cycle (300s),
            # against a host-wide ~8.3/s budget.
            self._reconcile_broker_positions(f"CONTINUOUS[{cycles}]")
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
        # A decision is BLOCKED, not warned about -- but the gate now lives
        # inside _attempt_entry, the choke point BOTH the single-shot and the
        # continuous paths share. Calling it here too would give one gate two
        # homes and let them drift.
        self._attempt_entry(trend_regime, volatility_regime)

    def _leg_tick_age(self, symbol):
        """One symbol's newest-tick age in seconds, or None for never/unreadable.

        THE STAGE-2 GATE'S FRESHNESS INPUT, injected rather than plumbed. The
        trading-brain composition root deliberately holds no feed -- it owns
        the PaperBroker, the journal and the clock -- so `process_entry_cycle`
        cannot read tick ages itself. Passing this callable keeps the feed
        dependency where it already lives (this runner) and leaves the gate's
        policy pure.

        None is not zero. An unreadable age is silence, and silence is never
        treated as freshness.
        """
        if self._tick_feed is None:
            return None
        try:
            return self._tick_feed.tick_age_seconds(symbol)
        except Exception:  # noqa: BLE001 -- unreadable is silent, never fresh
            return None

    def _master_expiry_resolver(self):
        """symbol -> ISO expiry, from the FYERS instrument master.

        WHY THE MASTER AND NOT THE PAYLOAD. The chain response carries ONE
        `expiryData` list and no per-row expiry, so `_build` had to stamp a
        single value on every row. The master knows each real contract's real
        expiry, which makes the stamp a verified per-row fact instead of an
        inference from a subscript.

        LAZY, so constructing the provider does not pay a 14 MB CSV read, and
        so a session that never fetches a chain never loads it at all.

        FAILS CLOSED, DELIBERATELY. If the master cannot be read, every symbol
        resolves to None, every row is dropped, and `_fetch` refuses with
        "produced zero usable rows" rather than trading on unverified expiries.
        That is consistent with the rest of the runner: the master is already a
        hard startup requirement -- lot size refuses to be guessed from YAML
        (2026-07-19 audit) -- so its absence here is an anomaly, not a mode.
        """
        state = {"rows": None, "failed": False}

        def resolve(symbol):
            if state["rows"] is None and not state["failed"]:
                try:
                    from bujji.broker.instrument_master import InstrumentMaster

                    master = InstrumentMaster(
                        REPO_ROOT / "data" / "instrument_master", self._logger)
                    underlying = self._session_cfg.get("underlying", "NIFTY")
                    state["rows"] = {
                        row.symbol: row.expiry_date.isoformat()
                        for row in master._rows_for(underlying)
                    }
                    self._logger.info(
                        "CHAIN EXPIRY -- %d %s contracts loaded from the instrument "
                        "master; each chain row's expiry is resolved against it "
                        "rather than stamped from expiryData.",
                        len(state["rows"]), underlying)
                except Exception as exc:  # noqa: BLE001 -- see docstring
                    state["failed"] = True
                    self._logger.critical(
                        "CHAIN EXPIRY -- the instrument master could not be read "
                        "(%s: %s). Every chain row will be dropped and the session "
                        "will refuse rather than trade on unverified expiries.",
                        type(exc).__name__, exc)
            if state["failed"] or not state["rows"]:
                return None
            return state["rows"].get(symbol)

        return resolve

    def _ensure_universe_subscribed(self) -> None:
        """Build the session universe ONCE and subscribe to all of it.

        THE ORDER THIS FIXES. Subscription used to be a CONSEQUENCE of
        trading: `WebsocketTickProvider.get_prices()` subscribes
        `list(contracts_by_symbol)`, and that dict is populated only after the
        entry orders fill. So the session could select strikes, size them and
        place them without a single live price having arrived for anything --
        and a dead feed was first noticed as a BLIND CYCLE warning logged
        AFTER a naked short strangle was already open.

        The universe comes from `capture_universe.build_capture_universe`,
        which is the single authority: it selects REAL rows from the exchange
        symbol master rather than formatting symbol strings, bands in index
        POINTS rather than a strike count (the real master steps by 50 near
        expiry and 1500 for LEAPS, so "20 strikes each side" means different
        widths on different expiries), and resolves expiry ROLES instead of
        taking `expiryData[0]` on faith.

        Never raises: a universe that cannot be built leaves `_universe` None,
        which the coverage gate reads as UNKNOWN and refuses to enter on.
        """
        self._ensure_universe_built()
        if self._universe is None:
            return
        if self._tick_feed is None:
            # Offline by design. The universe was still built -- the chain
            # request needs it -- but nothing can prove per-symbol coverage,
            # and that is recorded rather than implied.
            self._universe_error = "NOT_APPLICABLE: no websocket tick feed configured"
            return
        self._subscribe_universe()

    def _ensure_universe_built(self) -> None:
        """Build the canonical universe. NO TICK FEED REQUIRED.

        WHY THIS IS SEPARATE FROM SUBSCRIBING. The two were one method, and
        that conflated two different needs: the CHAIN REQUEST derives its
        width, its expiry and its admissible contracts from the universe
        whether or not a websocket exists, while SUBSCRIBING obviously needs
        one. A replay or store-backed session has no feed by design, and used
        to get no universe either -- so its chain request had nothing
        authoritative behind it.

        SPOT COMES FROM THE BROKER, NOT FROM THE CHAIN. Centering the universe
        needs a spot; deriving that spot from the chain, and the chain from the
        universe, is circular. The broker's own spot endpoint breaks it.
        """
        if self._universe is not None or self._universe_error is not None:
            return
        spot = getattr(self, "_last_spot", None)
        if not spot:
            # THE CIRCULARITY BREAKER. Ask the broker directly rather than
            # waiting for a chain that cannot be requested without a universe.
            try:
                import asyncio as _aio_spot
                spot = _aio_spot.run(
                    self._broker.get_spot(self._session_cfg.get("underlying", "NIFTY")))
            except Exception as exc:  # noqa: BLE001 -- no spot is a refusal, never a guess
                self._universe_error = (
                    f"NO_SPOT: universe cannot be centred without a spot "
                    f"({type(exc).__name__}: {exc})")
                return
        if not spot:
            self._universe_error = "NO_SPOT: universe cannot be centred without a spot"
            return

        try:
            import datetime as _dt
            from pathlib import Path as _Path

            from bujji.broker.instrument_master import InstrumentMaster
            from bujji.capture_universe.builder import (
                DEFAULT_SELECTION_BAND_POINTS, build_capture_universe,
            )

            master = InstrumentMaster(REPO_ROOT / "data" / "instrument_master", self._logger)
            # `_rows_for` is the accessor the existing caller uses
            # (scripts/build_capture_universe.py:58). Kept identical rather
            # than adding a public alias in this commit.
            rows = master._rows_for(self._session_cfg.get("underlying", "NIFTY"))

            # CONTAINMENT BY CONSTRUCTION, not by coincidence.
            #
            # The eligible selection band is bounded by the chain FETCH
            # (`providers.market_data.strike_count`), while the subscription is
            # bounded by this tier table. They are configured independently, so
            # today's fit -- band +/-1000 inside a +/-1500 front tier -- is an
            # accident of two numbers, not a guarantee. Raising strike_count
            # alone would silently push band contracts outside the universe and
            # the band gate would refuse the session every day with
            # BAND_NOT_SUBSCRIBED.
            #
            # Deriving the floor from the same knob removes the coincidence.
            #
            # FRONT AND SECOND ONLY. `select_expiry` picks the nearest expiry
            # with DTE >= 1, which is FRONT, or SECOND on expiry day when FRONT
            # is the 0-DTE contract. MONTHLY can never be that expiry -- it
            # deliberately reaches FORWARD past any weekly already taken -- so
            # widening it would subscribe symbols no selection can ever range
            # over. Today the floor is 1000 and neither tier moves.
            # CAPTURE WIDE, SELECT NARROW -- and the universe owns both.
            #
            # This used to derive the capture TIERS from `strike_count`, which
            # had the authority backwards: a chain-request constant decided how
            # much of the book was subscribed. Now the universe states its own
            # selection band, the builder enforces SELECTION <= CAPTURE(FRONT),
            # and the chain request derives its width from the selection band.
            md_cfg = (self._config.get("providers", {}).get("market_data", {}) or {})
            selection_band = int(md_cfg.get("selection_band_points",
                                            DEFAULT_SELECTION_BAND_POINTS))
            universe = build_capture_universe(
                rows, float(spot), _dt.date.fromisoformat(self._as_of_date),
                step=_UNIVERSE_GRID_STEP, selection_band_points=selection_band)
        except Exception as exc:  # noqa: BLE001 -- an unbuilt universe blocks entry, never ends the session
            self._universe_error = f"BUILD_FAILED: {type(exc).__name__}: {exc}"
            self._logger.critical(
                "UNIVERSE -- could not be constructed (%s). No entry can be "
                "permitted: coverage of an unknown universe cannot be proven.",
                self._universe_error)
            return

        self._universe = universe

    def _subscribe_universe(self) -> None:
        """Subscribe every contract the universe selected. Requires a feed.

        NO FEED IS NOT AN UNBUILT UNIVERSE. Replay and store-backed sessions
        are deliberately offline, so per-symbol coverage does not apply to
        them -- but the universe still exists and still decides the chain
        request. The two used to be conflated, and an offline session got
        neither.
        """
        universe = self._universe
        symbols = list(universe.symbols)
        try:
            self._tick_feed.subscribe(symbols)
            self._universe_requested = tuple(symbols)
        except Exception as exc:  # noqa: BLE001
            self._universe_error = f"SUBSCRIBE_FAILED: {type(exc).__name__}: {exc}"
            self._logger.critical(
                "UNIVERSE -- subscribe failed for %d symbols (%s). Entry blocked.",
                len(symbols), self._universe_error)
            return

        self._governor_result_summary["universe"] = {
            "symbols": len(symbols),
            "atm_strike": getattr(universe, "atm_strike", None),
            "spot": getattr(universe, "spot", None),
            "roles_resolved": list(getattr(universe, "roles_resolved", ()) or ()),
            "expiries_available": getattr(universe, "expiries_available", None),
            "expiries_excluded": getattr(universe, "expiries_excluded", None),
        }
        self._logger.info(
            "UNIVERSE -- %d symbols subscribed BEFORE entry (ATM %s, roles %s).",
            len(symbols), getattr(universe, "atm_strike", None),
            ",".join(getattr(universe, "roles_resolved", ()) or ()))

    def _block_entry(self, reason: str) -> None:
        """Record an entry refusal so the SESSION VERDICT can see it.

        `entry_blocked_by` was written at six sites and read at NONE.
        `session_safety_verdict` -- the only consumer of this summary, and the
        thing that sets the process exit code -- read
        `position_truth_established` and never this. So a session could be
        turned away from every entry it attempted, exit 0, and leave systemd
        green and the operator's phone silent. That is the 2026-08-21 shape.

        BOTH FORMS ARE KEPT. `entry_blocked_by` is LAST-WRITE-WINS across up
        to 96 decision cycles, which on its own cannot answer "did this
        session ever refuse for a reason that means it was blind?" -- a defect
        at cycle 5 followed by a different refusal at cycle 90 leaves only the
        later one showing. `entry_blocked_reasons` accumulates, and that is
        what the verdict grades.
        """
        self._governor_result_summary["entry_blocked_by"] = reason
        recorded = self._governor_result_summary.setdefault("entry_blocked_reasons", [])
        if reason not in recorded:
            recorded.append(reason)

    def _max_tick_age(self) -> float:
        return float(
            (self._config.get("providers", {}).get("tick_source", {}) or {})
            .get("max_tick_age_seconds", 90.0))

    def _tick_ages_for(self, symbols) -> Dict[str, Optional[float]]:
        """symbol -> seconds since its newest tick. Unreadable is None, never 0."""
        ages: Dict[str, Optional[float]] = {}
        for symbol in symbols:
            try:
                ages[symbol] = self._tick_feed.tick_age_seconds(symbol)
            except Exception:  # noqa: BLE001 -- unreadable is silent, never fresh
                ages[symbol] = None
        return ages

    def _record_universe_coverage(self) -> None:
        """Grade the WIDE capture universe, and never block on it.

        THIS USED TO BLOCK, AND THAT WAS THE RULE INVERTED (operator decision
        2026-08-22). It required a fresh tick from every one of ~242 symbols
        before any entry. The tiers are drawn on OPEN INTEREST and are
        deliberately wider than trading alone justifies --
        `capture_universe.builder`'s own measurement records six of eighteen
        expiries trading zero contracts all day -- so one legitimately quiet
        far strike refused the whole session. A naturally inactive contract is
        silence, and silence out there proves nothing about the feed.

        The operator's rule: capture wide, require narrow. The wide universe
        is still evaluated and still WRITTEN, because "which symbols went
        quiet today" is exactly the evidence the tick journal and the Gate 1
        measurement need. It simply is not a veto. What vetoes is
        `_band_coverage_permits_entry`, scoped to the contracts the decision
        actually rests on.
        """
        from bujji.production_runtime.universe_coverage import evaluate_coverage

        if self._universe_error is not None:
            state = ("NOT_APPLICABLE" if self._universe_error.startswith("NOT_APPLICABLE")
                     else "UNKNOWN")
            self._governor_result_summary["universe_coverage"] = {
                "state": state, "blocking": False, "detail": self._universe_error}
            return

        intended = list(getattr(self._universe, "symbols", ()) or ())
        verdict = evaluate_coverage(intended, self._universe_requested,
                                    self._tick_ages_for(intended), self._max_tick_age())
        payload = verdict.as_dict()
        # RECORDED, NOT ENFORCED -- and the artifact says so in its own words,
        # so a later reader cannot mistake a wide-universe verdict for a veto.
        payload["blocking"] = False
        self._governor_result_summary["universe_coverage"] = payload
        self._logger.info(
            "UNIVERSE COVERAGE (recorded, not blocking) -- %s: %d/%d fresh, "
            "%d silent, %d stale.",
            verdict.state, verdict.fresh, verdict.intended,
            len(verdict.silent), len(verdict.stale))

    def _band_coverage_permits_entry(self, chain) -> bool:
        """THE BLOCKING SCOPE: every contract the selector may choose from.

        The operator's rule, verbatim: "Before Bujji selects a strike, it must
        have fresh market data for the underlying, VIX where relevant, and
        every contract in the configured eligible selection band -- not merely
        a pair it has not selected yet."

        NOT MERELY THE LEGS. `_build_strike_evidence` ranks the whole band and
        `_candidates_for_type` picks from it, so a stale price on a contract
        that is NOT chosen still corrupts the choice: it changes which strike
        looked closest to the target delta. Checking only the chosen legs would
        validate the answer while leaving the question corrupt.

        VIX IS NOT REQUIRED TODAY, and that is a measured claim rather than an
        omission. `bujji_options_os_runner.py` never reads VIX; the production
        regime path constructs `vix=VixSnapshot(value=None)` and lists "vix" in
        its own `_ABSENT` tuple (bujji/regime_stability/warmup.py). Nothing in
        selection, sizing or risk consumes it, so by the operator's own
        relaxation it cannot block a decision that never relied on it.
        `test_vix_is_still_not_an_entry_input` fails the day that stops being
        true, which is what keeps this an assertion instead of an assumption.
        """
        from bujji.capture_universe.builder import KIND_SPOT
        from bujji.production_runtime.selection_band import selection_band
        from bujji.production_runtime.universe_coverage import evaluate_coverage

        # NO FEED IS NOT A SILENT FEED. Replay and store sources have no
        # websocket by design; there is no per-symbol evidence to demand.
        # Mirrors the universe gate rather than implying the question away.
        if self._universe_error is not None and \
                self._universe_error.startswith("NOT_APPLICABLE"):
            self._governor_result_summary["band_coverage"] = {
                "state": "NOT_APPLICABLE", "detail": self._universe_error}
            return True

        band = selection_band(chain, self._as_of_date)
        self._governor_result_summary["selection_band"] = band.as_dict()
        if not band.usable:
            self._block_entry("SELECTION_BAND")
            self._logger.critical(
                "ENTRY BLOCKED -- the eligible selection band could not be "
                "determined (%s): %s", band.state, band.detail)
            return False

        # THE UNDERLYING IS PART OF THE DECISION, not context around it: every
        # delta in `_build_strike_evidence` is computed against spot, so a
        # stale spot mis-ranks the entire band at once.
        required = list(band.symbols)
        for inst in (getattr(self._universe, "instruments", ()) or ()):
            if getattr(inst, "kind", None) == KIND_SPOT and inst.symbol not in required:
                required.append(inst.symbol)

        # CONTAINMENT IS CHECKED, NOT ASSUMED. The band comes from the chain
        # FYERS returns; the subscription comes from the instrument master.
        # The two agree today (verified byte-identical across every live
        # expiry), but they are independently configured -- `strike_count`
        # widens the band, the tier table widens the universe -- so a band
        # symbol that was never subscribed is a CONFIGURATION defect, and it
        # would otherwise surface as a permanent, unexplained refusal.
        unsubscribed = [s for s in required if s not in set(self._universe_requested)]
        if unsubscribed:
            self._governor_result_summary["band_coverage"] = {
                "state": "BAND_NOT_SUBSCRIBED",
                "missing_count": len(unsubscribed),
                "missing_sample": unsubscribed[:10],
                "band": len(required),
            }
            self._block_entry("BAND_NOT_SUBSCRIBED")
            self._logger.critical(
                "ENTRY BLOCKED -- %d of %d contracts in the eligible selection band "
                "were never subscribed (e.g. %s). The capture universe does not "
                "cover the band the selector ranges over; this gate cannot be "
                "satisfied until that is reconciled.",
                len(unsubscribed), len(required), ", ".join(unsubscribed[:5]))
            return False

        verdict = evaluate_coverage(required, self._universe_requested,
                                    self._tick_ages_for(required), self._max_tick_age())
        payload = verdict.as_dict()
        payload["blocking"] = True
        payload["expiry"] = band.expiry
        self._governor_result_summary["band_coverage"] = payload
        if verdict.permits_entry:
            self._logger.info(
                "BAND COVERAGE -- %s: all %d eligible contracts at expiry %s are "
                "fresh within %.0fs.",
                verdict.state, verdict.intended, band.expiry, self._max_tick_age())
            return True

        self._block_entry("BAND_COVERAGE")
        self._logger.critical(
            "ENTRY BLOCKED -- band coverage %s (%d/%d fresh at expiry %s): %s",
            verdict.state, verdict.fresh, verdict.intended, band.expiry,
            " | ".join(verdict.reasons))
        return False

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
        # UNIVERSE FIRST. Subscribe to the whole configured universe and prove
        # it is actually ticking BEFORE anything selects a strike. Placed at
        # the top of the choke point BOTH entry modes share, ahead of position
        # truth and data quality, because a universe that is not covered makes
        # every later judgement rest on prices that never arrived.
        self._ensure_universe_subscribed()
        self._record_universe_coverage()

        # ESTABLISH POSITION TRUTH BEFORE THE FIRST ENTRY, ON DEMAND.
        #
        # `_continuous_session` reconciles at the top of every cycle;
        # `_entry_window` never reconciles at all. Rather than rely on which
        # branch the config selects -- a bet this runner has already lost twice
        # -- the choke point BOTH modes share establishes it itself if nothing
        # has yet. On a restart this is what discovers a position the broker is
        # holding and this process knows nothing about.
        if getattr(self, "_last_reconciliation", None) is None and \
                not getattr(self, "_reconciliation_ran", False):
            self._reconciliation_ran = True
            self._logger.info(
                "PRE_ENTRY -- no reconciliation has run yet this session; "
                "establishing position truth from the broker before any entry.")
            self._reconcile_broker_positions("PRE_ENTRY")

        self._governor_result_summary["position_truth_established"] = (
            getattr(self, "_last_reconciliation", None) is not None)

        # RECONCILIATION IS A SAFETY CONTROL, not an observability feature.
        # Taking new risk while the broker holds exposure Bujji is not
        # managing -- or while position truth cannot be established at all --
        # compounds an already-unmanaged position with a fresh one.
        if getattr(self, "_reconciliation_blocks_entry", True):
            last = getattr(self, "_last_reconciliation", None)
            self._logger.warning(
                "ENTRY REFUSED -- position reconciliation %s. %s",
                getattr(last, "verdict", "UNKNOWN"), getattr(last, "detail", ""))
            self._block_entry("POSITION_RECONCILIATION")
            return False

        # ONE STRATEGY PER DAY, ENFORCED ACROSS A RESTART.
        #
        # Deliberately AFTER reconciliation, which is a safety control that
        # must run and record position truth whatever this gate decides, and
        # BEFORE everything else, because if the day is already spent no
        # later gate's answer can matter.
        #
        # This blocks entry; it does not end the session. The process still
        # manages and closes whatever it holds, and still produces its
        # report -- the same shape the deprecated bot's DONE_FOR_DAY had.
        # HISTORICAL EXPOSURE FIRST. An account that cannot be reconciled
        # against its own record may not take new risk, whatever every later
        # gate would say.
        historical = getattr(self, "_historical_exposure", None)
        if historical is not None and historical.blocks_entry:
            self._logger.critical(
                "ENTRY REFUSED -- historical unresolved exposure. %s",
                historical.operator_instructions()
                or f"inspection did not run ({historical.error})")
            self._block_entry("HISTORICAL_UNRESOLVED_EXPOSURE")
            return False

        # ORPHAN EXPOSURE, beside historical exposure and for the same reason:
        # an account that cannot be reconciled against its own record may not
        # take new risk. The two gates cover different scopes -- position
        # groups above, session-scoped orphan records here -- and neither
        # substitutes for the other.
        orphans = getattr(self, "_orphan_exposure", None)
        if orphans is not None and orphans.blocks_entry:
            self._logger.critical(
                "ENTRY REFUSED -- unresolved orphan exposure. %s",
                orphans.operator_instructions()
                or orphans.detail
                or f"inspection did not run ({orphans.error})")
            self._block_entry("ORPHAN_EXPOSURE_UNRESOLVED")
            return False

        prior_fills = getattr(self, "_prior_fills_today", None)
        if prior_fills:
            self._logger.critical(
                "ENTRY REFUSED -- position group(s) %s already filled on %s. The "
                "day's one strategy was deployed by an earlier process; the "
                "in-memory tracker that normally refuses this did not survive "
                "the restart, and a flat account is not evidence that nothing "
                "was traded.", prior_fills, self._as_of_date)
            # TWO DIFFERENT REFUSALS, and the difference is the exit code.
            # "The day's strategy was already deployed" is a DISCIPLINED
            # outcome reached with full sight -- it exits 0, exactly as "no
            # strategy fit today" does. "I could not read the journal" is a
            # failure to establish something, and a session that could not
            # look must not present as one that looked and declined.
            self._block_entry(
                "PRIOR_FILLS_UNREADABLE"
                if getattr(self, "_prior_fills_unreadable", False)
                else "STRATEGY_ALREADY_DEPLOYED_TODAY")
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
            self._block_entry("DATA_QUALITY_NOT_ASSESSED")
            return False
        if not verdict.may_trade:
            self._logger.warning(
                "ENTRY REFUSED -- data quality %s. reasons=%s missing=%s",
                verdict.quality, list(verdict.reasons), list(verdict.missing_fields))
            self._block_entry(f"DATA_QUALITY_{verdict.quality}")
            self._governor_result_summary["data_quality_reasons"] = list(verdict.reasons)
            return False
        return True

    def _chain_snapshot(self):
        """`(chain, spot, age_seconds)` for the entry path.

        Live providers answer `snapshot()` atomically and enforce their own
        freshness limits, raising MarketDataUnavailableError rather than
        serving a book past `max_age`. Dated providers (replay, store) have no
        `snapshot` and no notion of age; they return None for it, and nothing
        downstream treats that as "fresh" -- it means "freshness does not
        apply to this source", which is only ever true off the live path.
        """
        provider = self._market_data_provider
        snapshot = getattr(provider, "snapshot", None)
        if callable(snapshot):
            return snapshot(self._as_of_date)
        return (provider.get_option_chain(self._as_of_date), provider.get_spot(), None)

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
        """One complete entry attempt: gates -> selection -> Gate B'd risk
        pipeline -> fills -> registry -> canonical lifecycle. Returns True
        only when a position is actually OPEN. Shared verbatim by the
        single-shot entry window and the continuous session loop -- one entry
        path, two clocks.

        THE GATES LIVE HERE, NOT IN _entry_window (fixed 2026-08-21).
        _entry_window called _data_quality_permits_entry() and was the only
        caller. But run() takes _entry_window ONLY when session.continuous is
        unset -- and the production config sets it, so production runs
        _continuous_session(), which calls THIS method directly. Both the hard
        data-quality boundary and the reconciliation block were therefore
        unreachable in production while being reported as wired. Two gates I
        built, certified, and never verified were on the executed branch.

        Putting them at this choke point makes the branch irrelevant: every
        entry in either mode passes the same gates, and a third caller added
        later inherits them automatically instead of silently bypassing them.
        """
        if not self._data_quality_permits_entry():
            return False

        selection = self._governor.select_and_lock_strategy(
            trend_regime, volatility_regime,
            analytical_snapshot_ref=getattr(self, "_analytical_snapshot_ref", None))
        self._governor_result_summary["strategy_selected"] = selection.selected_strategy
        if selection.selected_strategy is None:
            self._logger.info("No strategy for regime (trend=%s, vol=%s) -- no entry this cycle.",
                              trend_regime, volatility_regime)
            return False

        # ONE SNAPSHOT, NOT TWO READS.
        #
        # These were separate calls. With a provider that can refresh, a
        # refresh landing between them pairs a chain from fetch N with a spot
        # from fetch N+1 -- an internally inconsistent book, and strike
        # selection is the last place that should run on one. `snapshot()`
        # returns the pair from a single fetch, plus its age.
        #
        # A provider without `snapshot` is a deliberately DATED source
        # (ReplayChainProvider, StoreChainProvider): its book is historical by
        # construction, freshness is not a meaningful question, and the two
        # reads cannot disagree because nothing refreshes.
        # STALE DATA BLOCKS THE ENTRY, IT DOES NOT END THE SESSION.
        #
        # `_chain_snapshot()` raises MarketDataUnavailableError when the live
        # book is past its hard age limit. Letting that propagate would kill a
        # session that may be holding a position and still managing it from
        # tick data -- so it is caught HERE, at the same choke point
        # `_data_quality_permits_entry` uses, and turned into "no entry this
        # cycle". A later cycle whose refresh succeeds may still enter.
        #
        # The import is function-local because `MarketDataUnavailableError` is
        # imported that way throughout this runner; a name used outside the
        # function that imported it is the exact NameError shape that took the
        # emergency brake down on 2026-08-21 (see tools/undefined_name_guard.py).
        from bujji.production_runtime.market_data_provider import (
            MarketDataUnavailableError,
        )

        try:
            chain, spot, chain_age = self._chain_snapshot()
        except MarketDataUnavailableError as exc:
            self._logger.critical(
                "ENTRY BLOCKED -- the option chain is not fresh enough to trade on "
                "(%s). Constructing an order from a stale book would pick strikes "
                "against a spot the market has already left.", exc)
            self._governor_result_summary["entry_allowed"] = False
            # A SECOND write-only key, singular, used only here -- so this
            # refusal was invisible even to a reader that knew about
            # `entry_blocked_by`. The detail is kept; the classification is
            # now recorded where the verdict looks.
            self._governor_result_summary["entry_blocked_reason"] = (
                f"STALE_MARKET_DATA: {exc}")
            self._block_entry("STALE_MARKET_DATA")
            return False

        if chain_age is not None:
            self._governor_result_summary["chain_age_seconds_at_entry"] = round(chain_age, 1)
            self._logger.info("ENTRY -- option chain is %.0fs old at strike selection.",
                              chain_age)

        # STAGE 1, AND IT HAS TO BE HERE. The band is derived from THIS chain
        # -- the one `construct_trade` is about to range over -- so the gate
        # cannot check a different set than the selector uses. That is only
        # possible after the snapshot, which is why this is not up with the
        # other data-quality checks.
        #
        # CHAIN AGE IS NOT PER-CONTRACT FRESHNESS. The age above is the age of
        # the FETCH. A contract that has not traded in an hour returns an
        # hour-old price inside a book fetched five seconds ago, and the bulk
        # age cannot tell the two apart. Only per-symbol tick evidence can.
        if not self._band_coverage_permits_entry(chain):
            return False

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
            # STAGE 2. `None` when there is no websocket at all (replay,
            # store), which the gate records as NOT_APPLICABLE rather than
            # reading every leg as silent. Wired here because a gate nothing
            # calls is the defect class this campaign exists to remove.
            tick_age_fn=(self._leg_tick_age if self._tick_feed is not None else None),
            max_tick_age_seconds=self._max_tick_age(),
        )
        self._governor_result_summary["entry_allowed"] = entry_decision.allowed
        self._governor_result_summary["entry_filled"] = cycle_result.filled if cycle_result else False
        # Checked whether or not the entry filled: an order that asked for a
        # symbol the quote book does not hold is worth knowing about even if
        # it was ultimately rejected.
        self._check_quote_book_agreement()

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
            elif isinstance(reason, str) and reason.startswith("BROKER_TRUTH_UNKNOWN:"):
                # GAP 1. An UNKNOWN leg may or may not be a live position --
                # that is exactly what UNKNOWN means -- and this branch used
                # to register nothing, so if it HAD filled it became a
                # position nothing owned: unvalued, unstopped, and surfacing
                # only as a BROKER_ONLY reconciliation finding hours later.
                #
                # Registering is safe in both worlds. It is a MEMORY WRITE
                # that places no order, and PositionGroupReality.is_open is
                # derived from a live broker read -- so a leg that does not
                # exist at the broker simply never reads open and costs
                # nothing. A leg that DOES exist is now managed. Refusing to
                # register because we are unsure is the one choice that is
                # wrong in both worlds.
                self._register_orphaned_legs(cycle_result, reason,
                                             label="BROKER_TRUTH_UNKNOWN")
            self._logger.info("Entry did not fill (reason=%s).", reason)
            return False

        contracts = {}
        entry_prices: Dict[str, float] = {}
        for order_result in cycle_result.order_results:
            order_id = getattr(order_result, "client_order_id", None)
            if order_id:
                self._execution_order_ids.append(order_id)
        # THE AUTHORITATIVE CONTRACT, keyed by client_order_id (2026-08-21).
        #
        # This rebuilt each contract with _leg_to_core_contract, re-deriving
        # broker identity from strategy-leg fields AFTER the broker had
        # already been told a specific symbol. The rebuild is what keeps two
        # symbol vocabularies alive.
        #
        # It also paired legs to results POSITIONALLY, which is wrong on a
        # reachable path: when SUBMIT_INTENT cannot be journaled,
        # journaled_entry records the pair and `continue`s WITHOUT appending
        # to `results`, so order_results can be shorter than proposal.legs and
        # the zip then pairs leg[0] with results[1] -- one leg's contract
        # against another leg's fill.
        for order_result in cycle_result.order_results:
            coid = getattr(order_result, "client_order_id", None)
            contract = cycle_result.contract_for(coid) if coid else None
            if contract is None:
                # NEVER REBUILD. A missing contract means the propagation
                # boundary was not crossed; rebuilding would resurrect the
                # defect this removes, and silently.
                self._logger.critical(
                    "CONTRACT PROPAGATION MISSING for %s -- the order was placed but its "
                    "contract did not reach registration. This leg cannot be registered "
                    "and will NOT be managed. Inspect the broker book NOW.", coid)
                continue
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

    def _mark_orphan_deployed(self) -> None:
        """Move the session to POSITION_ACTIVE because orphaned legs may be live.

        Kept separate from the `_orphan_position_live = True` assignments so
        those stay literally where they are: two regression tests match on
        that exact source text, and a refactor that moved them would break
        the pins without changing behaviour.

        NEVER RAISES. `_register_orphaned_legs` documents that it never
        raises -- failing to register must not also lose the CRITICAL log
        that names the orphans -- and this must not weaken that. An illegal
        transition is reported and swallowed.
        """
        try:
            self._governor.mark_position_deployed("orphaned_legs_registered")
        except Exception as exc:  # noqa: BLE001 -- see docstring
            self._logger.critical(
                "ORPHAN STATE TRANSITION FAILED (%s: %s) -- the session may still "
                "permit a SECOND entry on top of live orphaned legs. Inspect the "
                "broker book NOW.", type(exc).__name__, exc)

    def _register_orphaned_legs(self, cycle_result, reason: str,
                                label: str = "PARTIAL_ORPHANED") -> None:
        """Register legs that may be live positions, so management owns them.

        THE PRINCIPLE: register a SUPERSET and let broker truth filter it.
        Registration places no order and `PositionGroupReality.is_open` is
        derived from a live `get_open_positions()` read, so a symbol that does
        not exist at the broker never reads open. Registering a leg we are
        unsure about therefore costs nothing, while declining to register one
        that turns out to be real costs an unmanaged naked position. The
        asymmetry only points one way.

        Never raises: failing to register must not also lose the CRITICAL log
        that names the orphans.
        """
        try:
            orphan_coids = set(
                c for c in reason.split(":", 1)[1].split(",") if c)

            contracts = {}
            entry_prices = {}
            # Authoritative contracts only -- see the note at the post-fill
            # registration site. Keyed by client_order_id, which is also what
            # `orphan_coids` names, so the match is on the same identity the
            # runtime used rather than on position.
            for order_result in cycle_result.order_results:
                coid = getattr(order_result, "client_order_id", None)
                if coid not in orphan_coids:
                    continue
                contract = cycle_result.contract_for(coid)
                if contract is None:
                    self._logger.critical(
                        "ORPHAN CONTRACT PROPAGATION MISSING for %s -- this leg may be a "
                        "live position and cannot be registered from an authoritative "
                        "contract. It is NOT rebuilt. Inspect the broker book NOW.", coid)
                    continue
                contracts[contract.symbol] = contract
                entry_prices[contract.symbol] = order_result.average_price
            if not contracts:
                # GAP 3. This logged CRITICAL and returned, registering
                # nothing -- so the legs it had just declared unaccounted-for
                # stayed invisible to management, which is the state the log
                # was warning about.
                #
                # Fall back to EVERY leg of the proposal. It is a superset:
                # some of these may never have reached the broker, and those
                # simply never read open. What it guarantees is that no leg
                # that IS live is left unowned because we could not match a
                # client order id.
                self._logger.critical(
                    "ORPHAN REGISTRATION -- reason named %s but no matching order "
                    "results were found. Falling back to registering ALL %d "
                    "ORDERED contract(s); broker truth filters the ones that do "
                    "not exist. These are the contracts the broker was actually "
                    "handed, never rebuilt ones. Inspect the broker book.",
                    sorted(orphan_coids), len(cycle_result.order_contracts))
                for coid, contract in cycle_result.order_contracts:
                    contracts[contract.symbol] = contract
                    entry_prices.setdefault(contract.symbol, None)
            if not contracts:
                self._logger.critical(
                    "ORPHAN REGISTRATION IMPOSSIBLE -- the proposal carries no legs. "
                    "The broker book must be inspected manually NOW.")
                return
            # Label-qualified: BROKER_TRUTH_UNKNOWN and PARTIAL_ORPHANED can
            # both fire for one assessment, and register_entry refuses a
            # duplicate group id (registration is one-time by design).
            pg_id = f"{cycle_result.proposal.assessment_id}-{label}"
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
            self._mark_orphan_deployed()
            self._entry_prices = entry_prices
            self._contracts_by_symbol = contracts
            self._logger.critical(
                "ORPHANED LEGS REGISTERED FOR MANAGEMENT: %s -- position %s is LIVE "
                "with legs %s. Management cycles will revalue and exit it; treat "
                "this session as an incident regardless.",
                sorted(orphan_coids), pg_id, list(contracts.keys()))
        except Exception as exc:  # noqa: BLE001
            # GAP 4: this handler logged and returned, so a leg that may be a
            # live position was lost entirely -- no registration, and the
            # management loop never started because _orphan_position_live was
            # never set. A log alone does not stop a naked short.
            #
            # Whatever failed above, force the two things that keep the
            # position VISIBLE: management runs, and reconciliation therefore
            # performs its unfiltered broker read every pass. Both cost
            # nothing if the position turns out not to exist, and are the
            # difference between a contained problem and an invisible one if
            # it does.
            self._orphan_position_live = True
            self._mark_orphan_deployed()
            self._governor_result_summary["orphan_registration_failed"] = {
                "reason": reason, "label": label,
                "error": f"{type(exc).__name__}: {exc}",
            }
            self._logger.critical(
                "ORPHAN REGISTRATION RAISED %s: %s -- the broker book may hold live "
                "legs this runner is NOT managing. Management is forced ON so "
                "reconciliation still reads the broker every pass, but inspect the "
                "broker book NOW.", type(exc).__name__, exc)

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
            report = sync_quotes_from_chain(self._broker, chain)
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

    def _check_quote_book_agreement(self) -> None:
        """Alarm when an order looked up a symbol the quote book does not hold.

        WHY THIS IS NOT COVERED BY THE EXISTING ALARM. PAPER_MARKET_SYNC warns
        only when `quotes_applied == 0`. That catches "no quotes at all" and
        misses the more dangerous case entirely: quotes WERE applied, under
        names nothing ever looks up. Then every leg fills at its own reference
        premium with no spread cost, every downstream number stays plausible,
        and nothing reports it. A frictionless fill is indistinguishable from
        a good one unless someone counts the misses.

        That is precisely what a symbol-vocabulary mismatch produces, and it
        is why this exists BEFORE any vocabulary change: a migration error
        must present as an alarm, not as a successful-looking fill.

        Never raises: this is a realism check on a paper broker, not a
        safety gate on a real position.
        """
        try:
            misses = getattr(self._broker, "quote_lookup_misses", ())
            if not misses:
                return
            self._governor_result_summary["quote_lookup_misses"] = list(misses)
            self._logger.critical(
                "QUOTE BOOK MISMATCH -- %d order lookup(s) found no quote while the book was "
                "POPULATED: %s. Those legs filled at their own reference premium with NO spread "
                "cost, which is indistinguishable from a real fill in every number downstream. "
                "The order symbols and the quote-book symbols are not the same vocabulary.",
                len(misses), sorted(set(misses)))
        except Exception as exc:  # noqa: BLE001 -- a realism check never ends a session
            self._logger.warning("quote-book agreement check failed: %s", exc)

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

    def _session_realized_pnl(self):
        """Realized P&L so far this session, or None if it cannot be read.

        This was `getattr(self._broker, "realized_pnl", 0.0)`. NO broker
        defines that attribute -- PaperBroker keeps a private `_realized_pnl`
        dict behind a `get_realized_pnl()` method, and FyersBroker has
        neither -- so the getattr default was taken on EVERY call and the
        realized half of the daily-loss brake was permanently 0.0.

        The brake computes `realized + unrealized <= -daily_loss_limit`.
        With realized pinned at zero, a session that banks a large loss on a
        closed leg and then opens another is measured only on the new
        position's unrealized loss: with a 25000 limit, realized -20000 plus
        unrealized -10000 is a 30000 breach that the brake scored as -10000
        and held.

        Returns None rather than 0.0 when it cannot be determined. Zero is a
        claim that nothing was realized; None says we could not ask, and the
        caller discloses that rather than silently trading on it.
        """
        getter = getattr(self._broker, "get_realized_pnl", None)
        if callable(getter):
            try:
                value = getter()
            except Exception as exc:  # noqa: BLE001 -- a P&L read must not end a session
                self._logger.warning(
                    "realized P&L read failed (%s); the daily-loss brake will "
                    "see UNREALIZED P&L only this cycle.", exc)
                return None
            if value is not None:
                return float(value)
        value = getattr(self._broker, "realized_pnl", None)
        if value is not None:
            return float(value)

        # Disclosed ONCE per session: an alarm on every cycle trains the
        # operator to ignore it, and this is a standing property of the
        # broker, not a per-cycle event.
        if not getattr(self, "_realized_pnl_gap_disclosed", False):
            self._realized_pnl_gap_disclosed = True
            self._governor_result_summary["daily_loss_brake_realized_source"] = (
                f"UNAVAILABLE ({type(self._broker).__name__} exposes neither "
                f"get_realized_pnl() nor realized_pnl)")
            self._logger.warning(
                "DAILY-LOSS BRAKE IS INCOMPLETE: %s exposes no realized P&L, so "
                "the brake measures UNREALIZED P&L only. A loss already banked "
                "this session does not count toward the daily loss limit.",
                type(self._broker).__name__)
        return None

    def _run_one_management_pass(self, stage_label: str) -> None:
        # RECONCILE BEFORE ANY EARLY RETURN (defect in d6fbfaf, mine).
        #
        # This method opens with `if not self._entry_prices: return`, and
        # _entry_prices is populated only when an entry FILLS. Reconciliation
        # was called far below that guard, so it never ran unless Bujji
        # already believed it held a position -- which is the exact opposite
        # of the case it exists to detect. A position at the broker that Bujji
        # does not know about means, by definition, that no entry of ours
        # filled: _entry_prices is empty, the pass returns at line one, and
        # the unfiltered read never happens. The detector was dead precisely
        # where it mattered.
        #
        # Two further early returns below (no valuation; emergency brake) also
        # preceded it. Reconciliation depends on none of that state: it needs
        # the broker and the registry, both available from the first pass.
        self._reconcile_broker_positions(stage_label)

        if not self._entry_prices:
            return
        import asyncio
        from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import PositionHealthThresholds

        pg_id = self._governor._position_group_id
        as_of = self._clock().isoformat()
        view = self._current_leg_quotes(as_of)
        priced_from_ticks = view.priced_from_ticks
        self._governor_result_summary["last_price_view"] = view.summary()

        if view.valid:
            self._priced_from_ticks_cycles += 1
            self._consecutive_blind_cycles = 0
        else:
            # NO SUBSTITUTION. Previously this cycle revalued the position
            # against its own ENTRY prices, which made unrealized P&L zero by
            # construction, made every stop and target incapable of firing,
            # and -- through `leg.current_price` -- handed entry prices to the
            # governor as the reference price for a forced exit.
            #
            # The cycle is now simply INVALID for price-dependent decisions.
            # Nothing is valued, nothing is priced, and the blind counter
            # advances toward the emergency brake below, which is the
            # already-hardened escalation for exactly this state.
            self._blind_cycles += 1
            if self._price_provider is not None:
                self._consecutive_blind_cycles = getattr(self, "_consecutive_blind_cycles", 0) + 1
            self._logger.warning(
                "%s -- PRICE PATH INVALID (%s): %s. No valuation, no stop, no "
                "target, no adjustment this cycle. Entry prices are NOT "
                "substituted. (blind=%d priced_from_ticks=%d consecutive=%d)",
                stage_label, view.reason, view.detail, self._blind_cycles,
                self._priced_from_ticks_cycles,
                getattr(self, "_consecutive_blind_cycles", 0))
            self._governor_result_summary.setdefault("price_path_invalid", []).append(
                {"at": as_of, "reason": view.reason, "detail": view.detail[:200]})

        valuation = None
        if view.valid:
            ts_map = {symbol: as_of for symbol in view.prices}
            valuations = asyncio.run(
                self._portfolio_engine.revalue_all(view.prices, self._clock,
                                                   price_timestamps=ts_map)
            )
            valuation = valuations.get(pg_id)
            if valuation is not None and self._canonical_position_id is not None:
                # Excursion is recorded only for cycles priced from real
                # market data -- which is now the only kind of cycle that
                # produces a valuation at all.
                self._valuation_history.append(
                    getattr(valuation, "total_unrealized_pnl", None))

        # SNAPSHOT BEFORE THE BRAKE, AND ON BOTH PATHS. The emergency route
        # needs this list to attribute its fills, and an invalid-price cycle
        # is precisely when that route runs.
        positions_before_exit = asyncio.run(self._registry.positions_for_group(pg_id))
        symbols_before_exit = [p["symbol"] for p in positions_before_exit]

        if valuation is None and view.valid:
            # A valid price path that still yielded no valuation: the group is
            # genuinely absent. Unchanged behaviour.
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
            realized_pnl=self._session_realized_pnl(),
            daily_loss_limit=self._config.get("capital_snapshot", {}).get("daily_loss_limit"),
            consecutive_blind_cycles=getattr(self, "_consecutive_blind_cycles", 0),
            max_consecutive_blind_cycles=self._config.get("position_management", {}).get(
                "max_consecutive_blind_cycles", 3),
        )
        if brake_reason is None and valuation is None:
            # Blind, but not yet at the brake threshold. The position stays
            # open and UNVALUED -- deliberately. Every price-dependent
            # decision below needs a valuation, and running them against
            # nothing is how the substitution defect existed in the first
            # place. The consecutive counter advances; the brake escalates.
            self._logger.warning(
                "%s -- price path invalid and brake not yet armed "
                "(consecutive=%d). Position management SUSPENDED this cycle.",
                stage_label, getattr(self, "_consecutive_blind_cycles", 0))
            return

        if brake_reason is not None:
            self._logger.critical("%s -- EMERGENCY CLOSE: %s", stage_label, brake_reason)
            self._governor_result_summary["emergency_close_reason"] = brake_reason

            # `_emergency_closed` HALTS POSITION MANAGEMENT PERMANENTLY, so it
            # must mean "the broker confirmed flat", never "we tried".
            #
            # It was set HERE, before the attempt, and never reconsidered. On
            # 2026-08-21 the blind-cycle brake fired correctly at 09:54:35, the
            # close raised NameError, CRITICAL_UNFLATTENED_POSITION was logged
            # with both legs still open -- and one cycle later the loop read
            # this flag and logged "POSITION_MANAGEMENT halted: emergency close
            # executed." It had not executed. A naked short strangle then sat
            # with no management, no stop-loss evaluation and no retry from
            # 09:55:35 until the EOD sweep at 15:33:44. Five hours and 38
            # minutes, undefined risk, because the brake that fired to protect
            # the position disabled the only loop that could have retried it.
            #
            # The NameError is fixed; this fail-open is the general case, and
            # it fires for ANY failed close -- broker timeout, rejection,
            # network loss, an exception anywhere on the exit path.
            #
            # Cleared first so a True left by an earlier pass cannot be read as
            # this pass's result.
            self._governor_result_summary["emergency_close_broker_flat"] = None
            self._execute_emergency_close(
                brake_reason, valuation, stage_label, symbols_before_exit)

            if self._governor_result_summary.get("emergency_close_broker_flat") is True:
                self._emergency_closed = True
            else:
                # Deliberately NOT set: the loop continues, and the next pass
                # re-evaluates the brake and retries the close. Retrying is
                # naturally rate-limited -- the loop sleeps its cadence between
                # passes and is bounded by max_cycles and monitor_until -- and
                # an unmanaged naked position is far worse than a retry.
                self._logger.critical(
                    "%s -- EMERGENCY CLOSE DID NOT CONFIRM FLAT "
                    "(broker_flat=%r). Position management CONTINUES so the "
                    "next pass retries; the position is NOT abandoned.",
                    stage_label,
                    self._governor_result_summary.get("emergency_close_broker_flat"))
            return

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


    def _expected_symbols(self, observed_symbols: set) -> set:
        """Every symbol Bujji believes it holds, decided against the SAME
        broker read the comparison uses.

        FIXED 2026-08-21 (defect in d6fbfaf, my own): this called
        registry.get_group_reality() per group, and that re-reads
        get_open_positions() on EVERY call to compute is_open. So a
        reconciliation over N groups issued N+1 broker reads, and -- the real
        hazard -- derived EXPECTED from reads 1..N while OBSERVED came from
        read N+1. A position closing between them made its symbol drop out of
        EXPECTED while still appearing in OBSERVED: a manufactured
        BROKER_ONLY, which is the CRITICAL finding that blocks new risk.
        A safety check that can invent its own alarm is worse than none.

        Now: one read, passed in, used for both sides. `symbols_for_group` is
        pure memory. Memory remains deliberately the WEAKER side -- the broker
        always wins the comparison itself.
        """
        expected = set()
        registry = getattr(self, "_registry", None)
        if registry is None:
            return expected
        for pg_id in registry.all_group_ids():
            try:
                symbols = set(registry.symbols_for_group(pg_id))
            except Exception:  # noqa: BLE001 -- one unreadable group never blinds the rest
                continue
            # "Open" per THIS read: a group with at least one leg the broker
            # currently reports. A group whose legs have all closed is not a
            # belief Bujji still holds, so it must not raise EXPECTED_ONLY.
            if symbols & observed_symbols:
                expected.update(symbols)
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
            # ONE read, both sides. See _expected_symbols for why.
            observed_symbols = {str(p.get("symbol")) for p in (observed or [])
                                if p.get("symbol")}
            result = reconcile(self._expected_symbols(observed_symbols), observed)
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

    def _execute_emergency_close(self, brake_reason, valuation, stage_label: str,
                                 symbols_before_exit=()) -> None:
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

        # PositionHealthThresholds is imported function-locally throughout this
        # runner, and _run_one_management_pass has its own copy. This method is
        # a DIFFERENT function, so that import was never in scope here.
        #
        # It failed live on 2026-08-21 at 09:54:35: the blind-cycle brake fired
        # correctly ("3 consecutive unpriced cycles with an open position --
        # cannot see, will not hold") and then raised NameError instead of
        # placing the exit. The position stayed open. Every surrounding safety
        # behaviour held -- the exception was caught, the broker was read,
        # flat=False was reported with both legs named, and
        # CRITICAL_UNFLATTENED_POSITION was raised rather than a clean close --
        # but the close itself did not happen.
        from bujji.trading_brain.risk_governor.position_lifecycle_intelligence import (
            PositionHealthThresholds)

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

        # FINALISE THE RECORD, NOT ONLY THE BOOK (2026-08-21 closure audit).
        # This method returned straight after run_market_close_sequence() on
        # its SUCCESS branch, so a brake that genuinely flattened still left
        # _exit_prices_by_leg empty -- and _close_canonical_lifecycle then
        # recorded canonical_close_outcome=NEVER_EXITED for a position the
        # broker had confirmed closed. Flat book, absent history. Captured
        # here, from the same execution object and through the same mapper
        # the ordinary exit path uses, so the two cannot attribute fills
        # differently.
        self._capture_exit_fills(symbols_before_exit, result)

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

    def _position_truth(self):
        """This runner's reader. See `_position_truth_for`."""
        return _position_truth_for(self)

    def _broker_reports_flat(self):
        """(True|False|None, detail). None means we could not establish it.

        UNFILTERED. `PositionRealityRegistry` answers "is THIS strategy still
        on", scoped to the symbols this process registered. That is a
        different question, and a position Bujji never registered cannot be
        seen through it. This asks what the account actually holds.

        A read that fails returns None, never False: "I could not ask" must
        never become "there is nothing there".

        M3 (2026-08-22): the three-valued parsing moved to
        `bujji.broker_truth`, which this now adapts to the (True|False|None)
        shape its two callers already branch on. The rule did not change; the
        number of places implementing it did.
        """
        answer = _position_truth_for(self).read()
        if answer.is_unknown:
            return None, answer.detail
        return answer.is_flat, answer.detail

    def _current_leg_quotes(self, as_of: str) -> "LegPriceView":
        """Typed quotes for this cycle, and whether they may price a decision.

        WHAT THIS REPLACES, AND WHY IT WAS A P0.

        The previous version returned `dict(self._entry_prices)` on three
        paths -- no provider, provider raised, incomplete coverage -- and a
        boolean saying so. That boolean was consulted for excursion
        statistics and for the blind-cycle counter, and NOWHERE ELSE. The
        entry prices themselves flowed on into `revalue_all()`, became
        `leg.current_price`, and were read by the session governor as
        `reference_prices` for a forced exit -- directly under a comment
        stating "Real current market price per leg ... never the entry
        price". The guarantee was true only on the branch nobody checked.

        The consequence was not a bad number. An exit priced at entry
        realises approximately zero P&L however far the market has actually
        moved, and every stop and target compares the position against
        itself, so none of them can ever fire.

        AN ENTRY PRICE IS EXECUTION EVIDENCE, NOT A MARKET PRICE. It is still
        held on `self._entry_prices` and still used for what it legitimately
        proves -- what we paid. It no longer leaves this method under any
        condition, and `test_entry_price_is_never_market_price` asserts that
        structurally.

        Returns a `LegPriceView`. When `valid` is False there are no prices:
        the caller must not value, must not price an exit, and must escalate.
        """
        from bujji.market_perception.quote import SOURCE_TICK
        from bujji.production_runtime.market_data_gate import assess_quote_fields

        if self._price_provider is None or not self._contracts_by_symbol:
            return LegPriceView(valid=False, reason=PRICE_REASON_NO_PROVIDER,
                                detail="no tick provider configured for this session")
        try:
            quotes = self._price_provider.get_quotes(self._contracts_by_symbol, as_of)
        except Exception as exc:  # noqa: BLE001 -- market data never kills a session.
            self._logger.exception("tick provider failed: %s", exc)
            return LegPriceView(valid=False, reason=PRICE_REASON_PROVIDER_FAILED,
                                detail=f"{type(exc).__name__}: {exc}")

        # EVERY leg, or none. `revalue()` already refuses a partially priced
        # group; asking the gate for the whole set makes the refusal explicit
        # and gives it a typed reason instead of a silent substitution.
        required = list(self._entry_prices)
        allowed = tuple(self._config.get("position_management", {}).get(
            "allowed_price_sources", (SOURCE_TICK,))) if isinstance(
                getattr(self, "_config", None), dict) else (SOURCE_TICK,)
        verdict = assess_quote_fields(
            quotes, ["ltp"], now_mono=_time.monotonic(),
            max_age_seconds=float(self._config.get("position_management", {}).get(
                "max_price_age_seconds", 90.0))
            if isinstance(getattr(self, "_config", None), dict) else 90.0,
            allowed_sources=allowed, required_symbols=required)

        if not verdict.may_trade:
            return LegPriceView(
                valid=False, reason=PRICE_REASON_QUALITY_REFUSED,
                detail="; ".join(verdict.reasons[:4]),
                quotes=dict(quotes), quality=verdict)

        # DERIVED HERE, LOCALLY, AND ONLY FROM GATE-PASSED QUOTES. This is the
        # single place a float is taken out of a quote on the management path.
        prices = {sym: float(quotes[sym].ltp) for sym in required}
        return LegPriceView(valid=True, reason=PRICE_REASON_OK,
                            detail=f"{len(prices)} leg(s) priced from "
                                   f"{verdict.origin or 'live ticks'}",
                            quotes=dict(quotes), prices=prices,
                            priced_from_ticks=True, quality=verdict)

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
        self._capture_exit_fills_from(
            exit_symbols, execution.orders_submitted, source="MANAGEMENT")

    def _capture_exit_fills_from(self, exit_symbols, orders_submitted, source: str) -> None:
        """THE ONE PLACE exit evidence becomes lifecycle truth.

        Every route that reduces a position -- ordinary policy exit, 15:15
        mandatory exit, emergency brake, EOD broker-truth closure -- funnels
        through here. Before the 2026-08-21 closure audit only the first two
        did, so the two routes that actually ran on a bad day flattened the
        book and left the record saying the position never exited.

        `exit_symbols` and `orders_submitted` are PARALLEL: index i of one is
        index i of the other. Both callers preserve that ordering, and
        map_exit_fills_to_legs skips anything it cannot resolve rather than
        approximating it.
        """
        from bujji.production_runtime.lifecycle_outcome_bridge import map_exit_fills_to_legs

        if not orders_submitted:
            return
        if self._canonical_position_id is None:
            return
        lifecycle = self._lifecycle_states.get(self._canonical_position_id)
        if lifecycle is None:
            return
        try:
            mapped = map_exit_fills_to_legs(
                lifecycle, list(exit_symbols), list(orders_submitted), self._contracts_by_symbol,
            )
            self._exit_prices_by_leg.update(mapped)
            # orders_submitted, NOT execution.orders_submitted.
            #
            # When this function was extracted out of _capture_exit_fills, the
            # caller's local name came along for the ride and `execution` was
            # left unbound here -- a NameError on EVERY exit, on the one path
            # every closure route funnels through. It was swallowed by the
            # except below, because bookkeeping is not allowed to end a
            # session, so nothing ever surfaced it.
            #
            # The exit PRICES still landed: the update() above runs first, so
            # the closure fix itself worked and the lifecycle still closed.
            # What was silently lost was every exit ORDER ID, and with it the
            # fee/slippage half of the outcome record -- collect_execution_costs
            # then priced a round trip from its entry side alone.
            #
            # A structural test asserted this funnel EXISTS. It could not
            # assert the funnel RUNS, because the except made failure look
            # like success. tests/test_closure_integrity.py now calls it for
            # real and checks the order ids arrive.
            for submitted in orders_submitted:
                order_id = getattr(submitted, "client_order_id", None)
                if order_id:
                    self._execution_order_ids.append(order_id)
            self._logger.info("Captured %d real exit fill(s) for %s via %s",
                              len(mapped), self._canonical_position_id, source)
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
        # MONITORING-ONLY MODE. With no position open, this loop exists solely
        # to keep reconciliation ticking. There is nothing naked to protect,
        # so the tighter undefined-risk cadence would buy nothing and
        # _position_is_undefined_risk would log its fail-closed warning on
        # every pass, all day, for a position that does not exist.
        monitoring_only = not self._entry_prices
        naked = False if monitoring_only else self._position_is_undefined_risk()
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
        if monitoring_only:
            self._logger.info(
                "POSITION_MANAGEMENT -- monitoring-only (no position open): each pass "
                "reconciles broker truth and returns. interval=%ss", interval_s)

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
                # Only reachable once the broker CONFIRMED flat, so this
                # sentence is now true. It used to be printed after a close
                # that raised NameError and left both legs open.
                self._logger.critical(
                    "POSITION_MANAGEMENT halted: emergency close confirmed flat "
                    "by the broker.")
                break
            if termination_requested():
                self._logger.critical(
                    "ORDERLY STOP requested -- ending position management; EOD closure will "
                    "flatten against broker truth.")
                break
            self._run_one_management_pass(f"POSITION_MANAGEMENT[{cycles + 1}]")
            cycles += 1
            # STAGE 3 TERMINATION: THE BROKER PROVES FLAT, OR MONITORING RUNS ON.
            #
            # The operator's rule (2026-08-22): once a position exists, its
            # legs and hedges "remain mandatory until the broker proves the
            # account is flat."
            #
            # This read `if not self._entry_prices` -- LOCAL state, and the
            # weakest possible kind. `_entry_prices` is assigned at three
            # sites and cleared at NONE, so after the first fill the branch
            # was unreachable and its log line ("Position closed during
            # management") could never be printed truthfully. Monitoring did
            # continue to monitor_until, but by accident rather than by rule:
            # the moment anything cleared that dict, the loop would have
            # stopped watching a position on local belief alone.
            #
            # `_broker_reports_flat` is three-valued and already refuses to
            # turn "I could not ask" into "there is nothing there". It had
            # exactly ONE caller, on the emergency-close path. This is the
            # second, and it is the one that governs the ordinary day.
            #
            # THE GUARD IS A PRECONDITION, AND IT SHORT-CIRCUITS THE READ.
            # A first version broke on `flat is True` alone and the regression
            # suite caught it: on a NO-TRADE day the broker is flat from the
            # first pass, so monitoring stopped at cycle one -- defeating the
            # reason this loop was made unconditional on 2026-08-21, that "the
            # case reconciliation exists for is 'Bujji believes it holds
            # nothing while the broker holds something'". A broker flat at
            # 09:20 says nothing about a position appearing at 11:00.
            #
            # `_entry_prices` is sound HERE precisely because it is never
            # cleared: it can only ever say "something was opened", never
            # "nothing is open", so it cannot end monitoring on its own. That
            # was the defect in the condition it replaces; here it only gates,
            # and broker truth decides. Gating the READ as well keeps a
            # no-trade day from making one broker call per cycle whose answer
            # could never end the loop -- `_reconcile_broker_positions` at the
            # top of each pass already carries the read such a day needs.
            if self._entry_prices:
                flat, flat_detail = self._broker_reports_flat()
                if flat is True:
                    self._logger.info(
                        "POSITION_MANAGEMENT -- a position was opened and the broker "
                        "now proves the account is flat (%s); ending monitoring.",
                        flat_detail)
                    break
                if flat is None:
                    # UNKNOWN IS NOT FLAT. Monitoring continues, loudly: a
                    # position we cannot see is the case this loop exists for.
                    self._logger.warning(
                        "POSITION_MANAGEMENT -- could not establish flatness (%s). "
                        "Monitoring CONTINUES: UNKNOWN is not flat.", flat_detail)
            now_t = self._clock().time()
            if now_t >= end_t:
                self._logger.info("Reached monitor_until=%s -- ending monitoring loop.", end_time_s)
                break
            if interval_s > 0 and sleep_unless_terminated(interval_s):
                # Observed WITHIN the sleep, not one cadence later. Falling out
                # here reaches _eod_close(), which flattens against broker
                # truth; being SIGKILLed here would not.
                self._logger.critical(
                    "ORDERLY STOP requested during the %ds management interval -- "
                    "ending position management now; EOD closure will flatten "
                    "against broker truth.", interval_s)
                break
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

        # THE BACKSTOP MUST NOT BE GATED ON THE STRATEGY EXIT.
        #
        # These were two bare statements in sequence, so ANY exception from the
        # management pass meant `_run_eod_closure()` never ran at all. That is
        # backwards: the management pass is the strategy-aware exit, an
        # optimisation that prices legs from the valuation; the closure machine
        # is the broker-truth backstop that flattens whatever the strategy did
        # not. Gating the backstop on the optimisation succeeding removes it
        # exactly when it is needed.
        #
        # The paths that raise here are not exotic. `positions_for_group()` and
        # `get_group_reality()` go to the broker through PositionRealityRegistry
        # and do NOT catch, so a live position read that fails -- the single
        # most likely reason a position is still open at 15:30 -- would take the
        # closure machine down with it. `_run_eod_closure()` is itself fully
        # guarded and records BROKER_TRUTH_UNKNOWN rather than claiming
        # flatness, so reaching it is always better than not reaching it.
        pending_signal = None
        try:
            self._run_one_management_pass("EOD_CLOSE")
        except BaseException as exc:  # noqa: BLE001 -- the backstop runs regardless
            self._governor_result_summary["eod_management_pass_error"] = (
                f"{type(exc).__name__}: {exc}")
            self._logger.critical(
                "EOD_CLOSE -- the final management pass raised (%s: %s). Running the "
                "broker-truth closure machine ANYWAY; the strategy exit is an "
                "optimisation, the closure machine is the backstop.",
                type(exc).__name__, exc)
            if not isinstance(exc, Exception):
                # KeyboardInterrupt / SystemExit: a real termination request.
                # Honour it -- but AFTER the flatten attempt, never instead of
                # one. Masking it would be worse than delaying it.
                pending_signal = exc

        self._run_eod_closure()

        if pending_signal is not None:
            raise pending_signal

    def _run_eod_closure(self) -> None:
        """Drive the closure state machine and gate COMPLETE on its verdict."""
        import asyncio as _asyncio

        from bujji.production_runtime.eod_closure import run_eod_closure

        # THE SESSION MUST KNOW WHEN ITS OWN MARKET CLOSES.
        #
        # This runner's entire schedule is configured times -- entry_cutoff
        # 14:30, mandatory_exit 15:15, observe_until 15:30 -- and it referenced
        # the real exchange close NOWHERE. `bujji.market_calendar` has
        # separated CASH_MARKET_CLOSE (15:30) from FO_MARKET_CLOSE (15:40)
        # since the NSE circular of 2026-05-30, and Bujji trades F&O.
        #
        # The 15:30 observation end is a deliberate ten-minute buffer, not a
        # close, and that is right. What was missing is the consequence of
        # crossing 15:40: after it, submitting an exit is not "an attempt that
        # failed", it is an attempt that CANNOT succeed. A position still open
        # at 15:41 is an OVERNIGHT position with gap risk until the next
        # session, and that is a categorically different fact from one still
        # open at 15:31 with nine minutes left to flatten in.
        #
        # This does not change what the closure machine does -- it records
        # which of those two situations the operator is actually in.
        from bujji.market_calendar import FO_MARKET_CLOSE

        now_t = self._clock().time()
        past_fo_close = now_t >= FO_MARKET_CLOSE
        self._governor_result_summary["eod_started_after_fo_close"] = past_fo_close
        if past_fo_close:
            self._logger.critical(
                "EOD CLOSURE STARTING AT %s, AFTER THE F&O CLOSE (%s). Any exit "
                "submitted now cannot fill. If a position is open it is an "
                "OVERNIGHT position carrying gap risk, not a pending flatten.",
                now_t.strftime("%H:%M:%S"), FO_MARKET_CLOSE.strftime("%H:%M:%S"))
        else:
            self._logger.info(
                "EOD CLOSURE -- %s remaining before the F&O close (%s).",
                _fo_close_margin(now_t, FO_MARKET_CLOSE),
                FO_MARKET_CLOSE.strftime("%H:%M:%S"))

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

        # THE CLOSURE MACHINE FLATTENS THE BOOK; THIS FINALISES THE RECORD.
        # run_eod_closure lives outside the management path and never touched
        # _capture_exit_fills, so every position it flattened -- including
        # today's, and every EOD-closed session before it -- produced a
        # provably flat broker and a lifecycle permanently stuck at
        # NEVER_EXITED, with no exit price, no fees and no outcome memory.
        # The fills it really got are now mapped through the SAME
        # map_exit_fills_to_legs the management path uses.
        fills = tuple(getattr(result, "exit_fills", ()) or ())
        if fills:
            self._capture_exit_fills_from(
                [symbol for symbol, _ in fills], [outcome for _, outcome in fills],
                source="EOD_CLOSURE")

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
        self._assert_closure_truths_agree()

    def _assert_closure_truths_agree(self) -> None:
        """Three truths must tell one story. Nothing compared them before.

        A session carries three independent accounts of the same fact:

          BROKER   -- eod_closure's own unfiltered read (`flat`)
          LIFECYCLE-- the canonical position record (`canonical_close_outcome`)
          JOURNAL  -- the archived summary (`final_positions_status`)

        Each was individually honest and none was ever checked against the
        others, so the contradiction this audit found could persist
        indefinitely: broker provably FLAT, lifecycle permanently
        NEVER_EXITED, summary reading as a clean, complete session. The
        divergence was not hidden -- both values sat in the same dict -- it
        was simply never read as a pair.

        Runs AFTER _close_canonical_lifecycle so it grades the final state.
        Never raises: this is the detector of last resort, and a detector
        that can end the session would be a new way to lose one.
        """
        try:
            summary = self._governor_result_summary
            closure = getattr(self, "_eod_closure_result", None)
            broker_flat = getattr(closure, "flat", None)
            lifecycle_outcome = summary.get("canonical_close_outcome")
            positions_status = summary.get("final_positions_status")

            # A position that was opened and is now flat MUST have an exit
            # record. NEVER_EXITED is only honest when nothing ever closed.
            lifecycle_flat = lifecycle_outcome == "ACCEPTED"
            had_position = bool(self._canonical_position_id)
            divergences = []
            if had_position and broker_flat is True and not lifecycle_flat:
                divergences.append(
                    f"broker reports FLAT but the lifecycle says "
                    f"{lifecycle_outcome!r} -- the position closed and the record does not "
                    f"know how, at what price, or at what cost")
            if had_position and broker_flat is True and not self._exit_prices_by_leg:
                divergences.append(
                    "broker reports FLAT but no exit fill was captured for any leg -- "
                    "realized P&L cannot be attributed to this trade")
            if broker_flat is True and positions_status not in (None, "FLAT"):
                divergences.append(
                    f"broker reports FLAT but the archived summary says "
                    f"final_positions_status={positions_status!r}")
            if broker_flat is False and summary.get("session_closed"):
                divergences.append(
                    "session_closed is True while the broker reports open legs")

            summary["closure_truths_agree"] = not divergences
            if divergences:
                summary["closure_truth_divergences"] = divergences
                self._logger.critical(
                    "CLOSURE TRUTH DIVERGENCE (%d): %s. The book and the record disagree; "
                    "this session's outcome is NOT evidence and must not be pooled with "
                    "sessions whose lifecycle closed cleanly.",
                    len(divergences), " | ".join(divergences))
        except Exception as exc:  # noqa: BLE001 -- a detector must never end a session
            self._logger.warning("closure-truth check failed (session unaffected): %s", exc)

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
        # THE JOURNAL IS SEALED AFTER THE FEED STOPS AND ON EVERY EXIT PATH.
        #
        # This runs from `run()`'s `finally`, so a session that crashed, was
        # SIGTERMed, or refused to trade at all still leaves a sealed journal
        # and a manifest. An evidence layer that only closes cleanly records
        # exactly the sessions least in need of explanation.
        #
        # ORDER MATTERS: the feed is stopped first, so no callback can offer a
        # record after the accounting is taken and make the manifest disagree
        # with the file it describes.
        self._seal_tick_journal()

        # No auto-resume, no retry, no hidden recovery: this method only
        # logs and releases whatever was constructed in _startup(). A
        # future invocation of this runner always begins a brand new
        # session from STARTUP -- it never reads back a prior one.

    def _seal_tick_journal(self) -> None:
        """Close the journal and write the manifest beside it. Never raises.

        A manifest is what turns "this file exists" into "this file is a
        faithful record, or here is exactly how it is not". Without one, a
        journal truncated by a kill -9 parses perfectly and is silently short.

        Never raises because this runs in teardown: a failure here must not
        replace the session's real result with its own.
        """
        journal = getattr(self, "_tick_journal", None)
        if journal is None:
            # SAY SO, rather than leaving the key absent. A configuration with
            # no websocket (replay, store-backed) legitimately records no
            # ticks -- but silence and "no journal was expected" are different
            # claims, and only the session itself can tell them apart. Leaving
            # the key absent made the verdict infer legitimacy from silence,
            # which a negative control caught: a session that recorded nothing
            # at all still certified.
            self._governor_result_summary["tick_journal"] = {
                "expected": False,
                "detail": "no tick feed in this configuration; no ticks were "
                          "recorded and none were expected",
            }
            return
        try:
            from bujji.tick_journal import JournalManifest, MANIFEST_VERSION

            stats = journal.close()
            manifest_path = journal.path.with_suffix(".manifest.json")
            JournalManifest(
                version=MANIFEST_VERSION,
                session_id=self._session_id,
                as_of_date=self._as_of_date,
                journal_filename=journal.path.name,
                started_at=getattr(self, "_session_started_at", "") or "",
                ended_at=self._clock().isoformat(),
                offered=stats.offered, written=stats.written, dropped=stats.dropped,
                max_queue_depth=stats.max_queue_depth,
                bytes_written=stats.bytes_written,
                content_sha256=journal.content_sha256(),
                fsync_every_records=journal._fsync_every_records,
                fsync_every_seconds=journal._fsync_every_seconds,
                max_queue=journal._queue.maxsize,
                universe_symbols=len(getattr(self._universe, "symbols", ()) or ()),
            ).write(manifest_path)

            # `sealed` and `faithful` are STATED, not inferred by a reader
            # from the counters. A session verdict must not have to
            # reconstruct what "complete" meant.
            self._governor_result_summary["tick_journal"] = {
                "path": str(journal.path),
                "manifest": str(manifest_path),
                "sealed": True,
                "faithful": stats.complete,
                "max_crash_loss_records": journal._fsync_every_records,
                "max_crash_loss_seconds": journal._fsync_every_seconds,
                **stats.as_dict(),
            }
            self._logger.info(
                "TICK JOURNAL sealed -- %d records, %d dropped, complete=%s, "
                "manifest at %s",
                stats.written, stats.dropped, stats.complete, manifest_path)

            # THE WRITER'S CLAIM IS NOT EVIDENCE. Everything above is the
            # journal describing itself -- "I was offered N, wrote N, dropped
            # 0". Nothing had opened the file back up. A journal truncated
            # after its last fsync, or altered on disk, self-reports faithful.
            #
            # This reads it back through `read_journal`, which re-derives the
            # content hash, checks it against the manifest, verifies the
            # sequence has no gaps, and compares the count to what the manifest
            # said. The verdict below reads `verified`, not `faithful`.
            from bujji.production_runtime.tick_evidence import (
                reproduce_recorded_ticks, verify_sealed_journal)

            verified = verify_sealed_journal(journal.path, self._logger)
            self._governor_result_summary["tick_journal"]["verified"] = verified.to_dict()

            # POST-SESSION AUDIT, after all trading has ended. Reads the sealed
            # journal back and proves the recorded input stream reproduces.
            # Holds no broker and constructs no order: it takes a path and
            # returns a report, so it cannot trigger or repeat an order.
            reproduced, replay_report = reproduce_recorded_ticks(
                journal.path, self._logger)
            replay_report["reproduced"] = reproduced
            self._governor_result_summary["tick_journal"]["replay"] = replay_report
        except Exception as exc:  # noqa: BLE001 -- teardown must not mask the session result
            self._logger.critical(
                "TICK JOURNAL could not be sealed (%s: %s) -- the session's tick "
                "evidence may be unverifiable. The session result stands; this "
                "is a record-keeping failure, and it is recorded as one.",
                type(exc).__name__, exc)
            self._governor_result_summary["tick_journal"] = {
                "sealed": False, "faithful": False,
                "error": f"{type(exc).__name__}: {exc}"}


_TERMINATION_REQUESTED = _threading.Event()


def _fo_close_margin(now_time, fo_close) -> str:
    """Human-readable time left before the F&O close, for the EOD log line."""
    minutes = ((fo_close.hour * 60 + fo_close.minute)
               - (now_time.hour * 60 + now_time.minute))
    if minutes <= 0:
        return "0m"
    return f"{minutes}m"


def termination_requested() -> bool:
    return _TERMINATION_REQUESTED.is_set()


def sleep_unless_terminated(seconds: float, *, slice_seconds: float = 1.0,
                            sleep_fn=None) -> bool:
    """Sleep for `seconds`, but wake immediately on an orderly-stop request.

    Returns True if a stop was requested (the caller should break), False if
    the full interval elapsed quietly.

    WHY THIS EXISTS. The position-management loop ended with a bare
    `time.sleep(interval_s)`, where `interval_s` is 60s for a naked position
    and 300s for a defined-risk one. The loop checks `termination_requested()`
    at the TOP, so a SIGTERM arriving one second into a 300-second sleep was
    not observed for another 299 seconds.

    The unit sets `TimeoutStopSec=60` and `KillSignal=SIGTERM`. systemd
    therefore escalates to SIGKILL at 60 seconds -- which cannot be caught, so
    `finally` never runs, no EOD closure happens, and an open position is
    abandoned. The orderly-stop path that
    `install_termination_handlers` exists to provide was unreachable inside
    the window systemd allows, for the entire duration of every management
    sleep, which is where the loop spends essentially all of its time.

    Slicing at one second bounds the observation delay to ~1s regardless of
    the configured cadence, and costs one Event check per second.

    `_TERMINATION_REQUESTED` is a `threading.Event`, so `wait(timeout)` blocks
    on the event itself rather than polling -- but the slice loop is kept
    explicit and injectable so the behaviour is testable without real time.
    """
    import time as _t

    naps = sleep_fn or _t.sleep
    remaining = float(seconds)
    while remaining > 0:
        if termination_requested():
            return True
        nap = slice_seconds if remaining > slice_seconds else remaining
        naps(nap)
        remaining -= nap
    return termination_requested()


def install_termination_handlers(logger=None) -> None:
    """Make SIGTERM mean "stop at the next safe point", not "vanish".

    THE UNIT FILE ALREADY PROMISED THIS AND THE CODE DID NOT DO IT. The
    service says:

        # Give the session time to release its lock and finalise the outcome
        # record on SIGTERM before systemd escalates.
        TimeoutStopSec=60
        KillSignal=SIGTERM

    but there was no handler anywhere on this entrypoint's import tree, so
    Python's default SIGTERM disposition terminated the process WITHOUT
    unwinding: `finally` never ran, nothing was archived, and any open
    position was abandoned. A false safety comment is worse than a missing
    feature, because it stops anyone from looking.

    COOPERATIVE, NOT AN EXCEPTION. Raising from the handler would unwind
    straight past `_eod_close()` -- the session would tear down without ever
    flattening, which is barely better than being killed. Setting a flag that
    the session's own loops check means SIGTERM lands the process in its
    NORMAL closure path: break the loop, run the EOD close, archive.

    HONEST LIMIT: this only helps where the process is executing Python. A
    SIGTERM arriving while blocked in a broker call still cannot be serviced
    until that call returns, and the installed fyers SDK sets no HTTP timeout
    (verified: zero occurrences of "timeout" in the package), so that wait is
    unbounded. systemd escalates to SIGKILL after TimeoutStopSec regardless.
    """
    def _request_stop(signum, _frame):
        _TERMINATION_REQUESTED.set()
        if logger is not None:
            logger.critical(
                "SIGNAL %s received -- requesting an ORDERLY stop: the current loop will "
                "break at its next check and the session will run its normal EOD closure "
                "(flatten, verify, archive). It does NOT stop immediately.", signum)

    for signame in ("SIGTERM", "SIGINT"):
        sig = getattr(_signal, signame, None)
        if sig is None:
            continue
        try:
            _signal.signal(sig, _request_stop)
        except (ValueError, OSError):
            # Not the main thread, or a platform without it. Never fatal:
            # the session simply keeps the old behaviour for that signal.
            pass


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

    # Installed AFTER the lock is held and BEFORE the session starts, on the
    # main thread, so the handler is in place for every second this process
    # could be asked to stop while holding a position.
    install_termination_handlers(logging.getLogger("bujji-options-os-shadow"))

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

        # THE SESSION'S OWN VERDICT MUST REACH THE PROCESS EXIT CODE.
        #
        # Every fact below was already computed and already logged CRITICAL.
        # What was missing was any path from those facts to systemd. Without
        # it a session could end CRITICAL_UNFLATTENED_POSITION and still exit
        # 0, so OnFailure= never fired and the alarm that exists reached
        # nobody. On 2026-08-21 the alert fired only because an unrelated
        # websocket hang got the process SIGTERM-killed; a clean exit that
        # day would have been silent.
        from bujji.production_runtime.session_safety_verdict import (
            evaluate_session_safety,
        )

        verdict = evaluate_session_safety(summary)
        summary["session_safety_verdict"] = verdict.as_dict()
        if not verdict.safe:
            logger.critical(
                "SESSION ENDED UNSAFE (%d reason(s)) -- exiting %d so systemd "
                "fails this unit and OnFailure= alerts the operator: %s",
                len(verdict.reasons), EXIT_UNSAFE_SESSION,
                " | ".join(verdict.reasons))
            return EXIT_UNSAFE_SESSION
        # SAFE IS NOT THE SAME AS CERTIFIED. Nothing went wrong, but the
        # session could not establish its own evidence, so it must not be
        # reported as a success.
        if verdict.pending_evidence:
            logger.critical(
                "SESSION ENDED PENDING_EVIDENCE (%d reason(s)) -- nothing is known "
                "to be wrong, but this session cannot prove what it saw, so it is "
                "not certified. Exiting %d: %s",
                len(verdict.pending_reasons), EXIT_PENDING_EVIDENCE,
                " | ".join(verdict.pending_reasons))
            return EXIT_PENDING_EVIDENCE
        return EXIT_OK
    except (ConfigurationError, MissingRegimeInputError, MarketDataUnavailableError) as exc:
        logging.getLogger("bujji-options-os-shadow").error("Configuration/input failure: %s", exc)
        return EXIT_CONFIG_ERROR
    except Exception as exc:  # noqa: BLE001 -- process boundary, never let this propagate as a raw traceback exit
        logging.getLogger("bujji-options-os-shadow").exception("Runtime failure: %s", exc)
        return EXIT_RUNTIME_ERROR


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
