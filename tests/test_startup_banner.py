"""Startup banner — verifies mode/data-source/execution/live-orders lines
render correctly and unambiguously for every operating mode."""
from bujji.core.banner import render_startup_banner


def test_banner_fyers_paper_mode_shows_paper_disabled(config):
    config.broker.name = "fyers_paper"
    banner = render_startup_banner(config)
    assert "Mode:               PAPER" in banner
    assert "Market data source: FYERS LIVE" in banner
    assert "Execution dest.:    PAPER LEDGER" in banner
    assert "Live Orders: DISABLED" in banner
    assert "ENABLED" not in banner


def test_banner_plain_paper_mode_shows_synthetic_data(config):
    config.broker.name = "paper"
    banner = render_startup_banner(config)
    assert "Mode:               PAPER" in banner
    assert "SYNTHETIC" in banner
    assert "Live Orders: DISABLED" in banner


def test_banner_fyers_live_mode_shows_prominent_warning(config):
    config.broker.name = "fyers"
    banner = render_startup_banner(config)
    assert "Mode:               LIVE" in banner
    assert "Market data source: FYERS LIVE" in banner
    assert "Execution dest.:    FYERS LIVE" in banner
    assert "Live Orders: ENABLED" in banner
    assert "REAL CAPITAL AT RISK" in banner
    assert "DISABLED" not in banner


def test_banner_replay_mode_override(config):
    config.broker.name = "fyers_paper"  # Irrelevant to replay's own broker.
    banner = render_startup_banner(config, mode_override="REPLAY")
    assert "Mode:               REPLAY" in banner
    assert "Live Orders: DISABLED" in banner
    assert "HISTORICAL" in banner


def test_banner_shows_platform_version(config):
    from bujji import __version__
    banner = render_startup_banner(config)
    assert __version__ in banner
