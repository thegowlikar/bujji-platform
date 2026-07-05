"""Broker factory — dependency-injection entry point for broker selection.

Chooses the concrete :class:`Broker` implementation from config so the rest of
the system stays broker-agnostic. Add new brokers here only.
"""
from __future__ import annotations

import logging

from ..core.config import AppConfig
from .base import Broker
from .fyers import FyersBroker
from .guard import disable_live_execution
from .hybrid import HybridPaperBroker
from .paper import PaperBroker

# Paper Trading mode (live market data, paper-only execution). Chosen via
# `broker.name: fyers_paper` in config — reuses the same `broker.*` settings
# (app_id/access_token/retry policy) already used by full-live `fyers` mode,
# since it needs a genuine authenticated FYERS session for market data.
_PAPER_LIVE_DATA_NAME = "fyers_paper"


def build_broker(config: AppConfig, logger: logging.Logger) -> Broker:
    name = config.broker.name.lower()
    if name == "paper":
        return PaperBroker()
    if name == "fyers":
        return FyersBroker(config.broker, logger)
    if name == _PAPER_LIVE_DATA_NAME:
        return _build_hybrid_paper_broker(config, logger)
    raise ValueError(f"Unknown broker: {config.broker.name}")


def _build_hybrid_paper_broker(config: AppConfig, logger: logging.Logger) -> Broker:
    # The live leg is neutered for execution IMMEDIATELY on construction —
    # before it is ever handed to HybridPaperBroker, and before HybridPaperBroker
    # applies the same guard again (belt-and-braces). No path exists between
    # "FyersBroker() constructed" and "execution methods disabled."
    live_data = disable_live_execution(FyersBroker(config.broker, logger))
    ledger = PaperBroker()
    logger.warning(
        "paper_mode_live_data_active: using LIVE FYERS market data with "
        "PAPER-ONLY execution (broker.name=%s). No real orders can be placed.",
        _PAPER_LIVE_DATA_NAME,
    )
    return HybridPaperBroker(live_data, ledger, logger)
