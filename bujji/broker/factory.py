"""Broker factory — dependency-injection entry point for broker selection.

Chooses the concrete :class:`Broker` implementation from config so the rest of
the system stays broker-agnostic. Add new brokers here only.
"""
from __future__ import annotations

import logging

from ..core.config import AppConfig
from ..execution_profiles import get_profile
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


def _production_paper_broker() -> PaperBroker:
    """A PaperBroker configured with the REALISTIC execution profile.

    The realism layer (slippage/latency/rejection) was built and tested,
    then left switched off at every real construction site: `PaperBroker()`
    defaults every mode to ZERO, so live/shadow P&L was computed as if
    execution were frictionless. For a 4-leg IRON_CONDOR/IRON_FLY that
    understates cost on every leg of every trade, and premium-selling is
    high trade-count by design -- so the error compounds fastest exactly
    where this system trades most.

    Uses `execution_profiles.NORMAL` (already defined, already tested)
    rather than a new set of constants, so there is ONE place where
    execution realism is described. Note NORMAL is CALIBRATION_PENDING:
    its ~0.02% adverse impact is a disclosed modelled assumption, not a
    figure measured against real fills.

    Deliberately applied HERE and not as `PaperBroker.__init__`'s default:
    tests that construct `PaperBroker()` directly keep byte-identical,
    frictionless behaviour, so this change cannot silently move any
    existing assertion.
    """
    profile = get_profile("NORMAL")
    return PaperBroker(
        slippage_config=profile.slippage,
        latency_config=profile.latency,
        rejection_config=profile.rejection,
        charges_config=profile.charges,
        # A real broker refuses what the account cannot margin. Enabled
        # only here, on the production path -- a campaign that quietly
        # takes positions no real account could hold produces evidence
        # about a book that never could have existed.
        enforce_margin=True,
    )


def build_broker(config: AppConfig, logger: logging.Logger) -> Broker:
    name = config.broker.name.lower()
    if name == "paper":
        return _production_paper_broker()
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
    ledger = _production_paper_broker()
    logger.warning(
        "paper_mode_live_data_active: using LIVE FYERS market data with "
        "PAPER-ONLY execution (broker.name=%s). No real orders can be placed.",
        _PAPER_LIVE_DATA_NAME,
    )
    return HybridPaperBroker(live_data, ledger, logger)
