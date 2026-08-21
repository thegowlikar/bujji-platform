"""Tests — Numeric Risk Governor Gate C.3 (broker margin reality
adapter, READ-ONLY). ZERO network access anywhere in this file --
every test uses a fake, in-memory broker object; no real Fyers/broker
credentials or connections are ever constructed or used here."""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from bujji.broker.errors import AuthenticationError
from bujji.core.enums import OptionType
from bujji.core.models import OptionContract
from bujji.trading_brain.risk_governor import broker_margin_reality_adapter
from bujji.trading_brain.risk_governor.broker_margin_reality_adapter import (
    BROKER_SNAPSHOT_AUTH_FAILED,
    BROKER_SNAPSHOT_SOURCE,
    BROKER_SNAPSHOT_UNAVAILABLE,
    FyersMarginProvider,
)


def _clock(iso="2026-08-02T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _ce():
    return OptionContract(symbol="NSE:NIFTY26AUG24800CE", underlying="NIFTY", strike=24800,
                           option_type=OptionType.CE, expiry="2026-08-27", lot_size=75)


def _pe():
    return OptionContract(symbol="NSE:NIFTY26AUG24800PE", underlying="NIFTY", strike=24800,
                           option_type=OptionType.PE, expiry="2026-08-27", lot_size=75)


class _FakeBroker:
    """Implements ONLY get_funds/get_order_margin -- no place_order,
    no cancel_order, nothing else exists on this object at all, so any
    accidental call to a mutating method raises AttributeError
    immediately rather than silently succeeding."""

    def __init__(self, funds_response=None, order_margin_response=None,
                 funds_raises=None, order_margin_raises=None):
        self._funds_response = funds_response
        self._order_margin_response = order_margin_response
        self._funds_raises = funds_raises
        self._order_margin_raises = order_margin_raises

    async def get_funds(self):
        if self._funds_raises is not None:
            raise self._funds_raises
        return self._funds_response

    async def get_order_margin(self, ce_contract, pe_contract):
        if self._order_margin_raises is not None:
            raise self._order_margin_raises
        return self._order_margin_response


# --------------------------------------------------------------------- #
# Successful response mapping
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_successful_response_mapping():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0, "used_margin": 120000.0, "account_equity": 620000.0},
        order_margin_response={"margin_per_lot": 42000.0, "verified": False, "source": "fyers_span_margin"},
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is True
    assert snapshot.available_margin == 500000.0
    assert snapshot.used_margin == 120000.0
    assert snapshot.required_margin == 42000.0
    assert snapshot.source == BROKER_SNAPSHOT_SOURCE
    assert snapshot.raw_metadata["order_margin"]["margin_per_lot"] == 42000.0


# --------------------------------------------------------------------- #
# Missing fields
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_missing_available_margin_field_maps_to_none_not_zero():
    broker = _FakeBroker(
        funds_response={"used_margin": 120000.0},  # available_margin absent
        order_margin_response={"margin_per_lot": 42000.0},
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is True   # required_margin was present -- snapshot still usable
    assert snapshot.available_margin is None
    assert snapshot.used_margin == 120000.0


@pytest.mark.asyncio
async def test_missing_margin_per_lot_field_is_unavailable():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0},
        order_margin_response={"verified": False},  # margin_per_lot absent
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    assert snapshot.required_margin is None
    assert snapshot.source == BROKER_SNAPSHOT_UNAVAILABLE


@pytest.mark.asyncio
async def test_wrong_type_margin_value_is_unavailable_not_silently_coerced():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0},
        order_margin_response={"margin_per_lot": "not-a-number"},
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False


# --------------------------------------------------------------------- #
# Authentication failure
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_authentication_failure_on_funds_is_explicit_not_generic_unavailable():
    broker = _FakeBroker(funds_raises=AuthenticationError("expired token"))
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    assert snapshot.source == BROKER_SNAPSHOT_AUTH_FAILED
    assert snapshot.required_margin is None


@pytest.mark.asyncio
async def test_authentication_failure_on_order_margin_is_explicit():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0},
        order_margin_raises=AuthenticationError("expired token"),
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    assert snapshot.source == BROKER_SNAPSHOT_AUTH_FAILED


# --------------------------------------------------------------------- #
# Timeout / broker unavailable
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_broker_unavailable_funds_returns_none_maps_to_unavailable_never_zero():
    broker = _FakeBroker(funds_response=None)  # matches real get_funds()'s own "unavailable" contract
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    assert snapshot.required_margin is None    # NEVER 0.0 -- "never silently assume safety"
    assert snapshot.source == BROKER_SNAPSHOT_UNAVAILABLE


@pytest.mark.asyncio
async def test_timeout_exception_fails_closed_not_crashed():
    broker = _FakeBroker(funds_raises=TimeoutError("simulated network timeout"))
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    assert snapshot.source == BROKER_SNAPSHOT_UNAVAILABLE


@pytest.mark.asyncio
async def test_timeout_on_order_margin_fails_closed():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0},
        order_margin_raises=ConnectionError("simulated connection drop"),
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False


# --------------------------------------------------------------------- #
# Partial broker response -- reject the whole comparison
# --------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_partial_response_funds_ok_order_margin_none_rejects_whole_snapshot():
    broker = _FakeBroker(
        funds_response={"available_margin": 500000.0, "used_margin": 100000.0},
        order_margin_response=None,
    )
    provider = FyersMarginProvider(broker)
    snapshot = await provider.get_broker_margin_snapshot(_ce(), _pe(), clock=_clock())
    assert snapshot.available is False
    # NOT a half-filled snapshot with available_margin populated -- fully rejected
    assert snapshot.available_margin is None
    assert snapshot.used_margin is None


# --------------------------------------------------------------------- #
# Safety: structural read-only guarantees
# --------------------------------------------------------------------- #

def test_fake_broker_has_no_mutating_methods_at_all():
    """The fake broker used across every test in this file structurally
    cannot be misused to place/cancel orders -- the methods don't exist."""
    broker = _FakeBroker()
    assert not hasattr(broker, "place_order")
    assert not hasattr(broker, "cancel_order")
    assert not hasattr(broker, "modify_order")


def test_source_never_references_mutating_broker_methods():
    """AST-verified (not a substring scan): the adapter's own source
    code never references place_order/cancel_order/modify_order/
    get_open_positions by name anywhere in its parsed syntax tree."""
    source_path = Path(inspect.getfile(broker_margin_reality_adapter))
    tree = ast.parse(source_path.read_text())
    forbidden_names = {"place_order", "cancel_order", "modify_order", "get_open_positions"}
    referenced_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    referenced_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    offenders = forbidden_names & (referenced_attrs | referenced_names)
    assert offenders == set(), f"forbidden mutating method references found: {offenders}"


def test_no_live_broker_or_execution_imports():
    source_path = Path(inspect.getfile(broker_margin_reality_adapter))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden_prefixes = ("bujji.broker.fyers", "bujji.broker.hybrid", "bujji.runtime_execution")
    offenders = [m for m in imported if any(m == p or m.startswith(p + ".") for p in forbidden_prefixes)]
    assert offenders == [], f"forbidden imports found: {offenders}"
