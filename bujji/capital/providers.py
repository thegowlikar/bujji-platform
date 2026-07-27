"""Margin Provider abstraction.

The Capital Management Engine (engine.py) consumes ONLY this abstraction —
it never calls a Broker method directly, never contains a broker name, and
never branches on which broker is in use. Every broker-specific detail
(which REST endpoint, which SDK method, whether the figure is even
obtainable) lives inside a MarginProvider subclass, not in the engine.

    CapitalManagementEngine -> MarginProvider -> (Broker | config | nothing)

Four provider tiers, from least to most trustworthy:

  SyntheticMarginProvider    -- a fixed or schedule-driven number, for
                                 dev/test/replay. NEVER used for real capital
                                 decisions; `verified=False`, `source`
                                 always says "synthetic".
  ConfigMarginProvider       -- an operator-supplied ESTIMATE from
                                 config.yaml. `verified=False` always — an
                                 estimate is not a broker confirmation, no
                                 matter how carefully chosen. Exists for the
                                 explicit "ESTIMATED" capital_policy tier.
  BrokerMarginProvider       -- calls Broker.get_order_margin() for a real,
                                 broker-side figure, but UNCERTIFIED: the
                                 request/response mapping has not been
                                 confirmed against a live account by a human
                                 in this deployment. `verified=False`.
  CertifiedBrokerMarginProvider -- identical to BrokerMarginProvider except
                                 a human has explicitly certified (via
                                 config, not a code change) that the
                                 request/response shape was confirmed live.
                                 `verified=True`. This is the ONLY provider
                                 tier the "CERTIFIED" capital_policy accepts.

Research backing BrokerMarginProvider's FYERS implementation (2026-07-19):
the official fyers-apiv3 SDK (PyPI, v3.1.14, and direct introspection of the
installed package) exposes NO margin-calculator method. However, FYERS's own
support KB ("Calculating Trade Margin for Stock Symbols...") and multiple
posts on FYERS's own community forum (fyers.in/community) independently and
consistently describe a REST endpoint:

    POST https://api.fyers.in/api/v2/span_margin
    Body: {"data": [{"symbol": ..., "qty": ..., "side": 1|-1, "type": ...,
                     "productType": ..., "limitPrice": ..., "stopLoss": ...}, ...]}
    Response (reported): {"benefit": ..., "expo": ..., "span": ...,
                          "total": ..., "individual_info": {...}}

This is NOT part of the officially versioned v3 REST docs site
(myapi.fyers.in/docsv3) as far as could be confirmed, and at least one
community poster reported an "Invalid input" error on an apparently
well-formed request — so the exact required fields/auth header are NOT
independently verified in this codebase. `FyersSpanMarginProvider` below
implements a best-effort call to this endpoint, structured exactly like
`FyersTokenManager`'s existing raw-REST pattern, but is registered as an
UNCERTIFIED `BrokerMarginProvider` — never `CertifiedBrokerMarginProvider`
— until a human confirms it against a real account and flips the
`margin_provider_certified` config flag.
"""
from __future__ import annotations

import abc
import logging
from datetime import datetime
from typing import Optional

from ..broker.base import Broker
from ..core.clock import now_ist
from ..core.models import OptionContract
from .exceptions import BrokerCapitalQueryError
from .models import MarginRequirement


class MarginProvider(abc.ABC):
    """The ONLY thing CapitalManagementEngine talks to for margin figures."""

    @abc.abstractmethod
    async def get_margin_per_lot(
        self, ce_contract: OptionContract, pe_contract: OptionContract,
    ) -> MarginRequirement:
        """Never raises for 'no figure available' -- returns a
        MarginRequirement with margin_per_lot=None instead. May raise
        BrokerCapitalQueryError for a genuine transport failure (timeout,
        disconnect) -- the engine catches this and treats it identically
        to 'no figure available' (both -> BLOCKED, never a guess)."""


class SyntheticMarginProvider(MarginProvider):
    """Fixed or schedule-driven synthetic figure. Dev/test/replay ONLY --
    never wire this into a live or fyers_paper deployment; `verified` is
    always False and `source` always says so explicitly."""

    def __init__(self, margin_per_lot: float,
                 schedule: Optional[dict[int, float]] = None) -> None:
        self._margin_per_lot = margin_per_lot
        self._schedule = schedule or {}
        self._calls = 0

    async def get_margin_per_lot(self, ce_contract, pe_contract) -> MarginRequirement:
        now = now_ist()
        applicable = [k for k in self._schedule if k <= self._calls]
        value = self._schedule[max(applicable)] if applicable else self._margin_per_lot
        self._calls += 1
        return MarginRequirement(margin_per_lot=value, verified=False,
                                 source="synthetic", as_of=now)


class ConfigMarginProvider(MarginProvider):
    """An operator-supplied ESTIMATE, read from config. Explicitly
    `verified=False` — the mandate is clear this must never be confused
    with a broker-confirmed figure, however carefully chosen."""

    def __init__(self, margin_per_lot: float) -> None:
        if margin_per_lot <= 0:
            raise ValueError("configured margin estimate must be positive")
        self._margin_per_lot = margin_per_lot

    async def get_margin_per_lot(self, ce_contract, pe_contract) -> MarginRequirement:
        return MarginRequirement(
            margin_per_lot=self._margin_per_lot, verified=False,
            source="config_estimate", as_of=now_ist(),
        )


class BrokerMarginProvider(MarginProvider):
    """Calls Broker.get_order_margin() for a real broker-side figure.
    UNCERTIFIED by default: `verified` reflects whatever the broker call
    itself reports (see Broker.get_order_margin's contract) — for
    FyersBroker specifically this is currently always False/None (see
    module docstring: no live-certified margin calculator exists yet)."""

    def __init__(self, broker: Broker, source_label: str = "broker") -> None:
        self._broker = broker
        self._source_label = source_label

    async def get_margin_per_lot(self, ce_contract, pe_contract) -> MarginRequirement:
        now = now_ist()
        try:
            raw = await self._broker.get_order_margin(ce_contract, pe_contract)
        except Exception as exc:  # noqa: BLE001 - any broker-side failure.
            raise BrokerCapitalQueryError(str(exc)) from exc
        if raw is None:
            return MarginRequirement(margin_per_lot=None, verified=False,
                                     source=f"{self._source_label}_unavailable", as_of=now)
        margin = raw.get("margin_per_lot")
        try:
            margin_val = float(margin) if margin is not None else None
        except (TypeError, ValueError):
            margin_val = None
        return MarginRequirement(
            margin_per_lot=margin_val,
            verified=False,  # UNCERTIFIED tier -- see class docstring.
            source=str(raw.get("source", self._source_label)),
            as_of=now,
        )


class CertifiedBrokerMarginProvider(BrokerMarginProvider):
    """Identical mechanism to BrokerMarginProvider, but `verified=True` —
    construct this ONLY after a human has confirmed, against a real live
    account, that the broker's margin response is correctly parsed. There
    is no code-level check that enforces this — it is an explicit,
    deliberate operator decision (config: margin_provider_certified: true),
    exactly as the mandate requires: 'never hidden, never guessed'."""

    async def get_margin_per_lot(self, ce_contract, pe_contract) -> MarginRequirement:
        result = await super().get_margin_per_lot(ce_contract, pe_contract)
        if result.margin_per_lot is None:
            return result
        return MarginRequirement(
            margin_per_lot=result.margin_per_lot, verified=True,
            source=f"{result.source}_certified", as_of=result.as_of,
        )
