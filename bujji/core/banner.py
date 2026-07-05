"""Startup banner — pure presentation, no trading logic.

Renders a clear, prominent summary of what mode the platform is running in,
where market data comes from, where orders are routed, and whether live order
placement is even possible — so an operator glancing at the console/logs at
startup cannot mistake PAPER for LIVE. Shared by the live app entry point
(`bujji.app`) and the replay CLI (`bujji.replay`) so both present the same
unambiguous format.
"""
from __future__ import annotations

from typing import Optional

from .. import __version__
from .config import AppConfig

_WIDTH = 62


def describe_broker_mode(broker_name: str) -> dict:
    """Map a configured broker name to its data/execution/risk profile."""
    name = broker_name.lower()
    if name == "fyers":
        return {
            "mode": "LIVE",
            "market_data": "FYERS LIVE",
            "execution": "FYERS LIVE",
            "live_orders_enabled": True,
        }
    if name == "fyers_paper":
        return {
            "mode": "PAPER",
            "market_data": "FYERS LIVE",
            "execution": "PAPER LEDGER",
            "live_orders_enabled": False,
        }
    if name == "paper":
        return {
            "mode": "PAPER",
            "market_data": "SYNTHETIC (no live data)",
            "execution": "PAPER LEDGER",
            "live_orders_enabled": False,
        }
    return {
        "mode": name.upper(),
        "market_data": "UNKNOWN",
        "execution": "UNKNOWN",
        "live_orders_enabled": None,
    }


def _orders_line(live_orders_enabled: Optional[bool]) -> str:
    if live_orders_enabled is True:
        return "  Live Orders: ENABLED — REAL CAPITAL AT RISK  ⚠ ⚠ ⚠"
    if live_orders_enabled is False:
        return "  Live Orders: DISABLED ✓"
    return "  Live Orders: UNKNOWN — broker not recognized"


def render_startup_banner(config: AppConfig, mode_override: Optional[str] = None) -> str:
    """Build the banner text. `mode_override="REPLAY"` describes the Replay
    Engine's own broker (deterministic historical data + a replay-only paper
    ledger), which is entirely independent of `config.broker.name`.
    """
    if mode_override == "REPLAY":
        mode = "REPLAY"
        broker_label = "replay (deterministic)"
        market_data = "HISTORICAL CANDLES (replayed)"
        execution = "PAPER LEDGER (replay-only)"
        live_orders_enabled: Optional[bool] = False
    else:
        profile = describe_broker_mode(config.broker.name)
        mode = profile["mode"]
        broker_label = config.broker.name
        market_data = profile["market_data"]
        execution = profile["execution"]
        live_orders_enabled = profile["live_orders_enabled"]

    lines = [
        "=" * _WIDTH,
        f"  Bujji ORB-VWAP ATM Seller  v{__version__}",
        f"  Mode:               {mode}",
        f"  Broker:             {broker_label}",
        f"  Market data source: {market_data}",
        f"  Execution dest.:    {execution}",
        _orders_line(live_orders_enabled),
        "=" * _WIDTH,
    ]
    return "\n".join(lines)
