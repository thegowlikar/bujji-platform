"""M1 ACCEPTANCE: every production chain request derives from the universe.

The milestone criterion, from ARCHITECTURE.md:

    The chain request's strike count and expiry are derived from the universe;
    band subset-of universe holds for every expiry role, including on expiry day.

WHAT THIS REPLACES. The width was `strike_count: 20` -- a config constant that
WAS the eligible selection band, deciding independently of anything subscribed
what a strategy could range over. The expiry was `expiryData[0]` -- a broker
list read by position. Neither derived from the canonical universe, and neither
was checked against it, so the set a strategy could select from and the set that
was actually subscribed were two independent numbers that happened to agree.

CAPTURE WIDE, SELECT NARROW. The universe subscribes its capture tiers for
evidence and states a separate, narrower SELECTION band for what a strategy may
choose. The chain request derives from the selection band -- never the capture
width, which would offer the selector contracts nobody authorised it to trade.
"""
from __future__ import annotations

import ast
import datetime
import io
import logging
import os
from pathlib import Path

import pytest

from bujji.capture_universe.builder import (
    DEFAULT_SELECTION_BAND_POINTS, DEFAULT_TIERS, ROLE_FRONT, ROLE_SECOND,
    UniverseConstructionError, build_capture_universe, selection_strikes_each_side,
    strikes_each_side,
)
from bujji.production_runtime.live_chain_provider import LiveChainProvider
from bujji.production_runtime.market_data_provider import MarketDataUnavailableError

REPO_ROOT = Path(__file__).resolve().parent.parent
MASTER = Path("/opt/bujji/app/data/instrument_master")
_HAVE_MASTER = (MASTER / "fyers_fo_NSE.csv").exists()


def _rows():
    from bujji.broker.instrument_master import InstrumentMaster
    return InstrumentMaster(MASTER, logging.getLogger("m1"))._rows_for("NIFTY")


# ---------------------------------------------------- 1. width derives
@pytest.mark.parametrize("points,expected", [(500, 10), (1000, 20), (1500, 30)])
def test_the_request_width_derives_from_the_selection_band(points, expected):
    u = _universe(selection_band_points=points)
    assert selection_strikes_each_side(u, step=50) == expected


def _universe(symbols=(), selection_band_points=DEFAULT_SELECTION_BAND_POINTS, atm=24000):
    from bujji.capture_universe.builder import CaptureInstrument, CaptureUniverse, KIND_OPTION
    return CaptureUniverse(
        as_of_date="2026-08-24", spot=float(atm), atm_strike=atm,
        instruments=tuple(
            CaptureInstrument(symbol=s, kind=KIND_OPTION, role=ROLE_FRONT,
                              expiry="2026-08-25", strike=float(atm), option_type="CE")
            for s in symbols),
        roles_resolved={ROLE_FRONT: "2026-08-25"}, collapsed_roles=(),
        expiries_available=1, expiries_excluded=0,
        selection_band_points=selection_band_points)


def test_the_width_that_reaches_the_broker_is_the_universes():
    """Not a config's, and not the capture width."""
    sent = {}

    class _Broker:
        async def get_option_chain_raw(self, underlying, strike_count=None):
            sent["strike_count"] = strike_count
            return {"data": {"optionsChain": [
                {"strike_price": -1, "symbol": "NSE:NIFTY50-INDEX", "ltp": 24000.0,
                 "option_type": ""},
                {"strike_price": 24000, "option_type": "CE", "ltp": 10.0,
                 "symbol": "S1", "bid": 9.0, "ask": 11.0},
            ], "expiryData": [{"date": "25-08-2026"}]}}

    p = LiveChainProvider(_Broker(), underlying="NIFTY",
                          universe_source=lambda: _universe(["S1"], selection_band_points=1500))
    p.get_option_chain("2026-08-24")
    assert sent["strike_count"] == 30, "the request width is not the selection band's"


# ------------------------------------------- 2. no universe means refusal
def test_a_chain_request_without_a_universe_refuses():
    class _Broker:
        async def get_option_chain_raw(self, underlying, strike_count=None):
            raise AssertionError("the broker must not be called without a universe")

    p = LiveChainProvider(_Broker(), universe_source=lambda: None)
    with pytest.raises(MarketDataUnavailableError, match="no canonical universe"):
        p.get_option_chain("2026-08-24")


def test_a_universe_source_that_raises_refuses_rather_than_falling_back():
    def _boom():
        raise RuntimeError("master unreadable")

    class _Broker:
        async def get_option_chain_raw(self, underlying, strike_count=None):
            raise AssertionError("the broker must not be called")

    p = LiveChainProvider(_Broker(), universe_source=_boom)
    with pytest.raises(MarketDataUnavailableError, match="could not be resolved"):
        p.get_option_chain("2026-08-24")


def test_the_provider_cannot_be_built_without_a_universe_source():
    """No compatibility default: a caller that cannot supply one must fail."""
    with pytest.raises(TypeError):
        LiveChainProvider(object(), underlying="NIFTY")          # missing kwarg
    with pytest.raises(ValueError, match="requires a universe_source"):
        LiveChainProvider(object(), underlying="NIFTY", universe_source=None)


# -------------------------------- 3. band subset-of universe BY CONSTRUCTION
def test_contracts_the_universe_never_selected_are_dropped():
    """This is what makes containment structural rather than checked later:
    the band is derived from the chain, and the chain carries only universe
    contracts."""
    class _Broker:
        async def get_option_chain_raw(self, underlying, strike_count=None):
            return {"data": {"optionsChain": [
                {"strike_price": -1, "symbol": "NSE:NIFTY50-INDEX", "ltp": 24000.0,
                 "option_type": ""},
                {"strike_price": 24000, "option_type": "CE", "ltp": 10.0,
                 "symbol": "IN_UNIVERSE", "bid": 9.0, "ask": 11.0},
                {"strike_price": 26000, "option_type": "CE", "ltp": 1.0,
                 "symbol": "NOT_IN_UNIVERSE", "bid": 0.5, "ask": 1.5},
            ], "expiryData": [{"date": "25-08-2026"}]}}

    p = LiveChainProvider(_Broker(), universe_source=lambda: _universe(["IN_UNIVERSE"]))
    chain = p.get_option_chain("2026-08-24")
    symbols = {c.instrument_symbol for c in chain}
    assert symbols == {"IN_UNIVERSE"}
    assert "NOT_IN_UNIVERSE" not in symbols


@pytest.mark.skipif(not _HAVE_MASTER, reason="instrument master cache not present")
@pytest.mark.parametrize("as_of", [
    datetime.date(2026, 8, 24),   # ordinary day, FRONT is a live weekly
    datetime.date(2026, 8, 25),   # EXPIRY DAY -- FRONT is the 0-DTE contract
    datetime.date(2026, 8, 26),
])
def test_selection_never_exceeds_capture_on_any_day_including_expiry_day(as_of):
    """The criterion's hardest case. On expiry day FRONT is the contract
    expiring today and selection falls to SECOND, whose capture tier is
    narrower -- historically the point at which the two numbers came closest
    to disagreeing."""
    u = build_capture_universe(_rows(), 24216.65, as_of)
    select = selection_strikes_each_side(u, step=50)
    for role in (ROLE_FRONT, ROLE_SECOND):
        if role not in u.roles_resolved:
            continue
        assert select <= strikes_each_side(u, role, step=50), (
            f"{as_of}: selection {select} strikes exceeds {role} capture "
            f"{strikes_each_side(u, role, step=50)} -- a strategy could choose a "
            f"contract that was never subscribed")


@pytest.mark.skipif(not _HAVE_MASTER, reason="instrument master cache not present")
def test_a_selection_band_wider_than_capture_is_refused_at_build_time():
    with pytest.raises(UniverseConstructionError, match="exceeds the FRONT capture tier"):
        build_capture_universe(_rows(), 24216.65, datetime.date(2026, 8, 24),
                               selection_band_points=DEFAULT_TIERS[ROLE_FRONT] + 500)


# --------------------------------------------- 4. no surviving fallbacks
RUNNER = io.open(REPO_ROOT / "bujji_options_os_runner.py", encoding="utf-8").read()
PROVIDER = io.open(REPO_ROOT / "bujji" / "production_runtime" / "live_chain_provider.py",
                   encoding="utf-8").read()


def _fn(src, name):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_no_configured_strike_count_survives_on_the_request_path():
    body = _fn(PROVIDER, "_fetch")
    assert "self._strike_count" not in body
    assert "selection_strikes_each_side(universe)" in body


def test_the_obsolete_config_key_is_refused_not_ignored():
    """A stale config must not silently keep the old authority.

    Asserted STRUCTURALLY, not as a substring. A first version grepped for the
    membership test and a negative control proved it vacuous: a disabled guard
    (`if False and ...`) still CONTAINS that text, so it passed. The check must
    be a real comparison whose branch RAISES.
    """
    for node in ast.walk(ast.parse(RUNNER)):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
            continue
        if "strike_count" not in ast.unparse(node.test):
            continue
        if not any(isinstance(n, ast.Raise) for n in ast.walk(node)):
            continue
        assert 'market_data_cfg.get("strike_count"' not in RUNNER, \
            "the obsolete key is still readable as a value"
        return
    raise AssertionError(
        "no `if <strike_count in config>: raise` guard found -- a stale config "
        "could silently keep the old selection-band authority")


def test_the_universe_is_built_before_the_first_chain_is_requested():
    """Ordering, not merely presence. A chain pulled before the universe
    existed would be requested against nothing authoritative."""
    body = _fn(RUNNER, "_pre_market_check")
    assert "_ensure_universe_built()" in body
    assert body.index("_ensure_universe_built()") < body.index("get_option_chain("), \
        "the pre-market chain pull precedes the universe build"


def test_building_the_universe_does_not_require_a_tick_feed():
    """A replay session is offline by design and still needs a universe for
    its chain request; the two were conflated and it got neither."""
    body = _fn(RUNNER, "_ensure_universe_built")
    assert "_tick_feed" not in body, "universe construction still depends on a feed"


def test_spot_for_centring_comes_from_the_broker_not_the_chain():
    """Breaking the circularity: spot from the chain, chain from the universe,
    universe from spot."""
    body = _fn(RUNNER, "_ensure_universe_built")
    assert "get_spot" in body
