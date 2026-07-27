"""Capital policy — explicit, configurable, never hidden or guessed.

Determines which MarginProvider is constructed for a given deployment. The
policy is a single, operator-visible config value (`risk.capital_policy`);
nothing about which provider gets used is inferred from the broker name or
any other implicit signal.

    STRICT      Unknown/unverified margin -> NO TRADE. Requires CERTIFIED
                margin to trade at all. The only policy safe for real
                capital.
    ESTIMATED   Uses an operator-supplied config estimate (ConfigMarginProvider).
                Never broker-verified. Suitable for paper trading where you
                want realistic-ish sizing without a certified broker feed.
    SIMULATION  Uses a synthetic/schedule-driven figure (SyntheticMarginProvider).
                For replay and pure development — capital numbers are
                whatever the test/replay configuration says, not tied to
                any real account at all.
    CERTIFIED   Uses a broker-verified margin calculator
                (CertifiedBrokerMarginProvider) -- REQUIRED for live trading.
                Constructing this requires an explicit, separate
                confirmation (`risk.margin_provider_certified: true`) on
                top of selecting this policy -- selecting CERTIFIED policy
                alone, without that flag, is treated as a configuration
                error (fail loud, not fail open).
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from ..broker.base import Broker
from .providers import (
    BrokerMarginProvider,
    CertifiedBrokerMarginProvider,
    ConfigMarginProvider,
    MarginProvider,
    SyntheticMarginProvider,
)


class CapitalPolicy(str, Enum):
    STRICT = "STRICT"
    ESTIMATED = "ESTIMATED"
    SIMULATION = "SIMULATION"
    CERTIFIED = "CERTIFIED"


class CapitalPolicyError(RuntimeError):
    """Raised at construction time (never mid-trading-day) for a
    self-contradictory or unsafe policy configuration -- e.g. CERTIFIED
    policy selected without the certification flag, or ESTIMATED policy
    selected without an estimate configured. Fails loud, at startup,
    rather than silently falling back to something the operator didn't
    ask for."""


def build_margin_provider(
    policy: CapitalPolicy,
    broker: Broker,
    logger: logging.Logger,
    *,
    estimated_margin_per_lot: Optional[float] = None,
    simulated_margin_per_lot: float = 1_00_000.0,
    margin_provider_certified: bool = False,
) -> MarginProvider:
    """The single place a policy becomes a concrete MarginProvider.

    Called once at composition-root time (Orchestrator/app.py construction),
    never re-evaluated mid-day -- which provider backs a running process is
    fixed for that process's lifetime, exactly like every other
    capital-protection decision in this codebase.
    """
    if policy is CapitalPolicy.SIMULATION:
        logger.info("capital_policy_simulation: using SyntheticMarginProvider "
                   "-- figures are NOT tied to any real account")
        return SyntheticMarginProvider(simulated_margin_per_lot)

    if policy is CapitalPolicy.ESTIMATED:
        if estimated_margin_per_lot is None or estimated_margin_per_lot <= 0:
            raise CapitalPolicyError(
                "capital_policy=ESTIMATED requires risk.estimated_margin_per_lot "
                "to be set to a positive value -- an estimate must be an "
                "explicit operator input, never inferred"
            )
        logger.warning("capital_policy_estimated: using ConfigMarginProvider "
                       "(₹%.2f/lot) -- NOT broker-verified", estimated_margin_per_lot)
        return ConfigMarginProvider(estimated_margin_per_lot)

    if policy is CapitalPolicy.CERTIFIED:
        if not margin_provider_certified:
            raise CapitalPolicyError(
                "capital_policy=CERTIFIED requires risk.margin_provider_certified: "
                "true as a SEPARATE, explicit confirmation -- selecting the "
                "policy alone is not enough. Set this flag only after a "
                "human has verified the broker's margin-calculator response "
                "against a real account (see docs/CAPITAL_MANAGEMENT_ENGINE.md)."
            )
        logger.info("capital_policy_certified: using CertifiedBrokerMarginProvider")
        return CertifiedBrokerMarginProvider(broker, source_label="fyers")

    if policy is CapitalPolicy.STRICT:
        # STRICT means "only ever trade on certified margin" -- if the
        # broker hasn't been certified, this provider will honestly report
        # margin as unverified (verified=False) on every call, and the
        # Capital Management Engine's own require-verified check refuses
        # the trade. No separate provider type needed: STRICT is
        # BrokerMarginProvider's uncertified honesty, taken at face value
        # rather than silently accepted as good enough (ESTIMATED/SIMULATION
        # policies are what accept an unverified figure deliberately).
        logger.info("capital_policy_strict: using BrokerMarginProvider "
                   "(uncertified) -- trade proceeds ONLY if the broker "
                   "response is independently marked verified=True, which "
                   "requires CERTIFIED policy instead")
        return BrokerMarginProvider(broker, source_label="fyers")

    raise CapitalPolicyError(f"unknown capital_policy: {policy!r}")
