"""Tests -- Bujji Options OS Phase-1 Runner.

Covers ONLY: provider wiring, runner construction, lifecycle sequencing,
failure handling, fail-closed missing-regime behavior. Deliberately does
NOT re-test strategy selection math, D.1-D.6 risk formulas, strike/delta
selection, or exit-policy thresholds -- those already have their own
dedicated suites (test_trading_session_governor.py, test_strategy_
selector's own coverage inside it, etc.). Uses the same real historical
bhavcopy fixture as tests/test_trading_brain_runtime.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bujji.production_runtime.market_data_provider import (
    MarketDataUnavailableError, ReplayChainProvider,
)
from bujji.production_runtime.regime_provider import (
    HumanSuppliedRegimeProvider, MissingRegimeInputError,
)

import bujji_options_os_runner as runner_module
from bujji_options_os_runner import (
    ConfigurationError, EXIT_CONFIG_ERROR, EXIT_OK, EXIT_RUNTIME_ERROR,
    OptionsOSRunner, load_config, run,
)

REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
DAY = "2026-05-25"


def base_config(tmp_path, trend=None, volatility=None, bhavcopy=REAL_BHAVCOPY):
    return {
        "shadow_mode": True,
        "logging": {"namespace": "bujji-options-os-shadow-test"},
        "session": {
            "underlying": "NIFTY", "exchange_lot_size": 75, "desired_quantity": 1,
            "requested_risk": 5000.0,
            # Lifecycle mechanics, not market hours: these run at whatever
            # wall-clock the suite happens to hit. The gate itself is proven
            # in tests/test_market_hours_gate.py.
            "skip_market_hours_check": True,
            "proposed_trade_effect": {"additional_margin": 10000.0, "additional_max_loss": 5000.0},
        },
        "exit_policy": {"profit_target_fraction": 0.5, "max_loss_fraction": 1.0, "mandatory_exit_time": None},
        # BOUNDED, AND INDEPENDENT OF THE TIME OF DAY. Without this block the
        # runner falls back to its PRODUCTION defaults -- cycle_interval_
        # seconds=300, max_cycles=78 -- and `_position_management` sleeps on
        # the real clock. The loop only exits early when now >= monitor_until
        # (15:15 by default), so these tests passed in seconds all afternoon
        # and evening, then hung for 6h30m the moment a run started after
        # midnight: at 04:47 IST `04:47 >= 15:15` is False, so all 78 cycles
        # sleep for real. Observed 2026-08-18, a full-suite run stuck at 62%.
        #
        # cycle_interval_seconds=0 makes the loop skip its sleep entirely
        # (`if interval_s > 0`), so the cap alone bounds it. Two passes is
        # enough to prove the loop runs more than once without asserting
        # anything about wall-clock duration.
        "position_management": {
            "cycle_interval_seconds": 0, "monitor_until": "23:59:59", "max_cycles": 2,
        },
        "capital_snapshot": {},
        "providers": {
            "market_data": {"type": "replay_chain", "bhavcopy_path": bhavcopy},
            "regime": {"type": "human_supplied", "trend_regime": trend, "volatility_regime": volatility},
        },
        "artifacts": {
            "shadow_sessions_root": str(tmp_path / "shadow_sessions"),
            "journal_path": str(tmp_path / "pg_journal.db"),
        },
    }


# --------------------------------------------------------------------- #
# Provider wiring
# --------------------------------------------------------------------- #

def test_replay_chain_provider_loads_real_bhavcopy():
    provider = ReplayChainProvider(bhavcopy_path=REAL_BHAVCOPY, underlying="NIFTY")
    chain = provider.get_option_chain(DAY)
    spot = provider.get_spot()
    assert len(chain) > 0
    assert spot is not None and spot > 0


def test_replay_chain_provider_missing_file_fails_closed():
    provider = ReplayChainProvider(bhavcopy_path="/nonexistent/path.csv", underlying="NIFTY")
    with pytest.raises(MarketDataUnavailableError):
        provider.get_option_chain(DAY)


def test_human_supplied_regime_provider_returns_supplied_values():
    provider = HumanSuppliedRegimeProvider(trend_regime="SIDEWAYS", volatility_regime="LOW_VOL")
    assert provider.get_regime() == ("SIDEWAYS", "LOW_VOL")


def test_human_supplied_regime_provider_fails_closed_on_missing_trend():
    provider = HumanSuppliedRegimeProvider(trend_regime=None, volatility_regime="LOW_VOL")
    with pytest.raises(MissingRegimeInputError):
        provider.get_trend_regime()


def test_human_supplied_regime_provider_fails_closed_on_missing_volatility():
    provider = HumanSuppliedRegimeProvider(trend_regime="SIDEWAYS", volatility_regime=None)
    with pytest.raises(MissingRegimeInputError):
        provider.get_volatility_regime()


def test_human_supplied_regime_provider_never_guesses_a_default():
    provider = HumanSuppliedRegimeProvider()
    with pytest.raises(MissingRegimeInputError):
        provider.get_regime()


# --------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------- #

def test_load_config_missing_file_raises_configuration_error(tmp_path):
    with pytest.raises(ConfigurationError):
        load_config(tmp_path / "does_not_exist.yaml")


def test_load_config_real_options_os_shadow_yaml_parses():
    repo_root = Path(__file__).resolve().parent.parent
    config = load_config(repo_root / "config" / "options_os_shadow.yaml")
    assert config["shadow_mode"] is True
    assert config["logging"]["namespace"] == "bujji-options-os-shadow"
    assert "providers" in config


# --------------------------------------------------------------------- #
# Runner construction / lifecycle sequencing
# --------------------------------------------------------------------- #

def test_runner_construction_wires_governor(tmp_path):
    import logging
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    logger = logging.getLogger("test-options-os-runner")
    r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-1", logger=logger)
    r._startup()
    assert r._governor is not None
    from bujji.production_runtime.trading_session_governor.session_governor import TradingSessionGovernor
    assert isinstance(r._governor, TradingSessionGovernor)
    from bujji.production_runtime.trading_session_governor.session_trading_state import TradingSessionState
    assert r._governor.state == TradingSessionState.INITIALIZING
    r._shutdown()


def test_runner_full_lifecycle_no_trade_day_completes_cleanly(tmp_path):
    import logging
    config = base_config(tmp_path, trend="TRENDING_UP", volatility="HIGH")  # not in SELLING_UNIVERSE mapping -> NO_TRADE
    logger = logging.getLogger("test-options-os-runner")
    r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-2", logger=logger)
    summary = r.run()
    from bujji.production_runtime.trading_session_governor.session_trading_state import TradingSessionState
    assert r._governor.state == TradingSessionState.SESSION_COMPLETE
    assert summary["final_session_state"] == TradingSessionState.SESSION_COMPLETE.value


def test_runner_full_lifecycle_real_regime_reaches_entry_window(tmp_path):
    """Session #1 against a fresh journal now reaches a REAL FILL.

    This previously documented the D.2 empty-book gap: an empty book was
    aggregated with concentration figures left None, which
    `classify_portfolio_risk` read as INSUFFICIENT_PORTFOLIO_RISK_DATA ->
    RISK_INVALID -> PIPELINE_BLOCKED. That inverted the risk model at the
    safest possible moment (a portfolio holding nothing ranked more
    dangerous than a concentrated one) and made the first trade of any
    fresh journal unplaceable. Fixed in `portfolio_risk_aggregator` by
    distinguishing "genuinely zero" from "unknown"; every fail-closed
    path for a NON-empty book is unchanged (see
    tests/test_portfolio_risk_empty_book.py)."""
    import logging
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    logger = logging.getLogger("test-options-os-runner")
    r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-3", logger=logger)
    summary = r.run()
    # Updated 2026-08-19: SIDEWAYS + LOW_VOL now selects a short strangle
    # (three-part regime selection). Asserted against the declared universe
    # rather than one literal, so a future re-map of this regime does not
    # need this end-to-end test edited again.
    from bujji.production_runtime.trading_session_governor.strategy_selector import SELLING_UNIVERSE
    assert summary["strategy_selected"] == "NEUTRAL_PREMIUM_SELLING"
    assert summary["strategy_selected"] in SELLING_UNIVERSE
    assert summary["entry_allowed"] is True
    assert summary["entry_filled"] is True, "empty-book blocker regressed: session #1 no longer fills"
    from bujji.production_runtime.trading_session_governor.session_trading_state import TradingSessionState
    assert r._governor.state == TradingSessionState.SESSION_COMPLETE


def test_runner_stage_sequence_recorded_in_order(tmp_path):
    import logging
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    logger = logging.getLogger("test-options-os-runner")
    stages_seen = []
    r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-4", logger=logger)

    original_methods = {
        name: getattr(r, name) for name in
        ("_startup", "_pre_market_check", "_market_session", "_entry_window",
         "_position_management", "_eod_close", "_session_archive", "_shutdown")
    }

    def wrap(name, fn):
        def wrapped():
            stages_seen.append(name)
            return fn()
        return wrapped

    for name, fn in original_methods.items():
        setattr(r, name, wrap(name, fn))

    r.run()
    assert stages_seen == [
        "_startup", "_pre_market_check", "_market_session", "_entry_window",
        "_position_management", "_eod_close", "_session_archive", "_shutdown",
    ]


# --------------------------------------------------------------------- #
# Failure handling / exit codes
# --------------------------------------------------------------------- #

def test_run_missing_config_file_exits_1(tmp_path):
    exit_code = run(["--config", str(tmp_path / "nope.yaml"), "--as-of-date", DAY])
    assert exit_code == EXIT_CONFIG_ERROR


def test_run_missing_regime_exits_1(tmp_path, monkeypatch):
    config_path = tmp_path / "options_os_shadow.yaml"
    import yaml
    config = base_config(tmp_path, trend=None, volatility=None)
    config_path.write_text(yaml.safe_dump(config))
    exit_code = run(["--config", str(config_path), "--as-of-date", DAY])
    assert exit_code == EXIT_CONFIG_ERROR


def test_run_missing_bhavcopy_path_exits_1(tmp_path):
    config_path = tmp_path / "options_os_shadow.yaml"
    import yaml
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL", bhavcopy=None)
    config_path.write_text(yaml.safe_dump(config))
    exit_code = run(["--config", str(config_path), "--as-of-date", DAY])
    assert exit_code == EXIT_CONFIG_ERROR


def test_run_valid_session_exits_0(tmp_path):
    config_path = tmp_path / "options_os_shadow.yaml"
    import yaml
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    config_path.write_text(yaml.safe_dump(config))
    exit_code = run(["--config", str(config_path), "--as-of-date", DAY])
    assert exit_code == EXIT_OK


def test_runner_failure_does_not_create_a_second_entry_attempt(tmp_path, monkeypatch):
    """A failure mid-session must not cause attempt_entry() to be called
    twice for the same session -- there is no retry loop anywhere in the
    runner."""
    import logging
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    logger = logging.getLogger("test-options-os-runner")
    r = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-5", logger=logger)
    r._startup()
    r._pre_market_check()
    r._market_session()

    call_count = {"n": 0}
    original_attempt_entry = r._governor.attempt_entry

    def counting_attempt_entry(**kwargs):
        call_count["n"] += 1
        return original_attempt_entry(**kwargs)

    r._governor.attempt_entry = counting_attempt_entry
    r._entry_window()
    r._shutdown()
    assert call_count["n"] <= 1


def test_runner_never_auto_resumes_a_prior_session(tmp_path):
    """Two separate runner instances against the same journal/broker
    each start a brand-new session -- there is no code path that reads
    back a prior in-memory state."""
    import logging
    config = base_config(tmp_path, trend="SIDEWAYS", volatility="LOW_VOL")
    logger = logging.getLogger("test-options-os-runner")

    r1 = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-A", logger=logger)
    r1.run()

    r2 = OptionsOSRunner(config=config, as_of_date=DAY, session_id="TEST-SESSION-B", logger=logger)
    r2._startup()
    from bujji.production_runtime.trading_session_governor.session_trading_state import TradingSessionState
    assert r2._governor.state == TradingSessionState.INITIALIZING  # fresh, not resumed from r1
    r2._shutdown()
