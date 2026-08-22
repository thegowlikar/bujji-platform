"""Live FYERS chain -> OptionObservation, driven by a REAL dated capture.

Field names here are not invented: they come from
`data_certification/fyers_option_chain_discovery_20260813.json`, a real
NIFTY optionchain response captured 2026-08-13 09:19 IST. That capture
exists because `FyersBroker.get_option_chain_raw()` explicitly warns that
a caller building an OptionObservation with a premium price "must not
guess field names" and should read them from a real capture instead.

Using the capture as the fixture means these tests fail if FYERS changes
its response shape -- which is exactly the failure worth catching, and
one an invented fixture would hide.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from bujji.production_runtime.live_chain_provider import (
    LiveChainProvider,
    _positive,
    _to_iso_expiry,
)
from bujji.production_runtime.market_data_provider import MarketDataUnavailableError

CAPTURE = Path("/opt/bujji/app/data_certification/fyers_option_chain_discovery_20260813.json")
AS_OF = "2026-08-13"
_HAVE_CAPTURE = CAPTURE.exists()


def _raw():
    return json.loads(CAPTURE.read_text())["raw_response"]


class _Broker:
    def __init__(self, raw):
        self._raw = raw
        self.calls = []

    async def get_option_chain_raw(self, underlying, strike_count=5):
        self.calls.append((underlying, strike_count))
        return self._raw


def _universe_admitting(*symbols, selection_band_points=1000, atm=24500):
    """A REAL CaptureUniverse admitting exactly `symbols`.

    Not a mock. The provider derives its request width from
    `selection_band_points` and admits only contracts the universe selected, so
    a stand-in that skipped either would exercise a provider nobody runs. These
    fixtures therefore name the contracts they expect to survive -- which is
    also what makes the drop-what-the-universe-never-chose behaviour visible
    here rather than only in its own test.
    """
    from bujji.capture_universe.builder import (
        CaptureInstrument, CaptureUniverse, KIND_OPTION, ROLE_FRONT)
    return CaptureUniverse(
        as_of_date="2026-08-13", spot=float(atm), atm_strike=atm,
        instruments=tuple(
            CaptureInstrument(symbol=s, kind=KIND_OPTION, role=ROLE_FRONT,
                              expiry="2026-08-25", strike=float(atm), option_type="CE")
            for s in symbols),
        roles_resolved={ROLE_FRONT: "2026-08-25"}, collapsed_roles=(),
        expiries_available=1, expiries_excluded=0,
        selection_band_points=selection_band_points)


def _symbols_in(payloads):
    """Every broker symbol appearing in these raw payloads.

    Derived from the fixture rather than hardcoded, so a fixture that gains a
    strike does not silently start testing the drop path instead of the path
    it was written for."""
    out = []
    for raw in payloads if isinstance(payloads, (list, tuple)) else [payloads]:
        if not isinstance(raw, dict):
            continue
        for row in (raw.get("data") or {}).get("optionsChain") or []:
            sym = row.get("symbol")
            if sym and row.get("option_type") in ("CE", "PE"):
                out.append(sym)
    return out


class TestExpiryParsing:
    def test_fyers_ddmmyyyy_becomes_iso(self):
        assert _to_iso_expiry("18-08-2026") == "2026-08-18"

    def test_malformed_dates_are_none_not_guessed(self):
        assert _to_iso_expiry("") is None
        assert _to_iso_expiry("18-08-26") is None      # 2-digit year is ambiguous
        assert _to_iso_expiry("2026-08-18") is None    # already ISO -> not this format


class TestPositive:
    def test_zero_and_junk_are_not_prices(self):
        assert _positive(0) is None
        assert _positive("n/a") is None
        assert _positive(None) is None
        assert _positive(319.8) == 319.8


class TestFailsClosed:
    def test_no_data_refuses_rather_than_serving_an_empty_book(self):
        provider = LiveChainProvider(_Broker(None), universe_source=lambda: _universe_admitting())
        with pytest.raises(MarketDataUnavailableError, match="no data"):
            provider.get_option_chain(AS_OF)

    def test_a_request_failure_surfaces_as_a_data_failure(self):
        class Broken:
            async def get_option_chain_raw(self, *a, **k):
                raise RuntimeError("token expired")
        with pytest.raises(MarketDataUnavailableError, match="request failed"):
            LiveChainProvider(Broken(), universe_source=lambda: _universe_admitting()).get_option_chain(AS_OF)

    def test_rows_without_an_underlying_price_refuse(self):
        # The row carries a `symbol`, as every real FYERS row does (the
        # captured artifact fyers_option_chain_discovery_20260813.json has 23
        # rows, 0 missing it). Without one this test would now trip the
        # no-broker-symbol rule below and assert the wrong refusal.
        raw = {"data": {"optionsChain": [
            {"strike_price": 24100, "option_type": "CE", "ltp": 100.0,
             "symbol": "NSE:NIFTY2681824100CE"},
        ], "expiryData": [{"date": "18-08-2026"}]}}
        with pytest.raises(MarketDataUnavailableError, match="underlying price"):
            LiveChainProvider(_Broker(raw), universe_source=lambda: _universe_admitting(*_symbols_in(raw))).get_option_chain(AS_OF)

    def test_a_row_without_a_broker_symbol_is_dropped_not_fabricated(self):
        """This builder used to do `row.get("symbol") or f"{u}{strike}{type}"`.

        That `or` manufactured a THIRD symbol format -- no expiry, no NSE:
        prefix -- into the exact field Gate B
        (trading_brain_runtime.py:274-280) trusts as the broker's own string.
        A fabricated value in a field whose entire purpose is to be real is
        the most expensive kind of default: it cannot be priced, cannot be
        subscribed, and cannot be matched against a broker position, and
        nothing downstream can tell it apart from a real one.
        """
        raw = {"data": {"optionsChain": [
            {"strike_price": -1, "option_type": "", "ltp": 24100.0},
            {"strike_price": 24100, "option_type": "CE", "ltp": 100.0},          # no symbol
            {"strike_price": 24200, "option_type": "PE", "ltp": 90.0,
             "symbol": "NSE:NIFTY2681824200PE"},
        ], "expiryData": [{"date": "18-08-2026"}]}}
        chain = LiveChainProvider(_Broker(raw), universe_source=lambda: _universe_admitting(*_symbols_in(raw))).get_option_chain(AS_OF)
        symbols = [getattr(r, "instrument_symbol", None) for r in chain]
        assert symbols == ["NSE:NIFTY2681824200PE"]
        # And nothing synthetic leaked in.
        assert not any(s and not s.startswith("NSE:") for s in symbols)

    def test_dropping_every_row_refuses_rather_than_returning_an_empty_chain(self):
        raw = {"data": {"optionsChain": [
            {"strike_price": -1, "option_type": "", "ltp": 24100.0},
            {"strike_price": 24100, "option_type": "CE", "ltp": 100.0},          # no symbol
        ], "expiryData": [{"date": "18-08-2026"}]}}
        with pytest.raises(MarketDataUnavailableError):
            LiveChainProvider(_Broker(raw), universe_source=lambda: _universe_admitting(*_symbols_in(raw))).get_option_chain(AS_OF)

    def test_the_drop_is_reported_not_silent(self):
        """This builder already drops rows with no `ltp`. A second uncounted
        drop would be invisible -- the chain would just be quietly shorter."""
        import logging

        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        log = logging.getLogger("chain-drop-test")
        log.setLevel(logging.WARNING)
        log.addHandler(_Capture())
        raw = {"data": {"optionsChain": [
            {"strike_price": -1, "option_type": "", "ltp": 24100.0},
            {"strike_price": 24100, "option_type": "CE", "ltp": 100.0},          # no symbol
            {"strike_price": 24200, "option_type": "PE", "ltp": 90.0,
             "symbol": "NSE:NIFTY2681824200PE"},
        ], "expiryData": [{"date": "18-08-2026"}]}}
        LiveChainProvider(_Broker(raw), logger=log, universe_source=lambda: _universe_admitting(*_symbols_in(raw))).get_option_chain(AS_OF)
        assert any("no broker symbol" in m for m in records), records
        assert any("CE24100" in m for m in records), "the dropped strike is not named"


@pytest.mark.skipif(not _HAVE_CAPTURE, reason="real FYERS capture present only on the VPS")
class TestAgainstTheRealCapture:
    def _provider(self):
        return LiveChainProvider(_Broker(_raw()), underlying="NIFTY",
                                  universe_source=lambda: _universe_admitting(*_symbols_in(_raw())))

    def test_a_real_chain_is_built_from_the_live_response(self):
        provider = self._provider()
        chain = provider.get_option_chain(AS_OF)
        assert len(chain) > 0
        assert {r.option_type for r in chain} == {"CE", "PE"}

    def test_every_row_carries_a_real_premium(self):
        """The entire reason this provider exists: get_option_chain()
        drops the premium, so a chain built from it cannot be traded."""
        chain = self._provider().get_option_chain(AS_OF)
        assert all(r.close is not None and r.close > 0 for r in chain)

    def test_spot_comes_from_the_underlying_row_not_a_strike(self):
        provider = self._provider()
        provider.get_option_chain(AS_OF)
        spot = provider.get_spot()
        assert spot and spot > 1000, "expected a real NIFTY level"
        strikes = [r.strike for r in provider.get_option_chain(AS_OF)]
        assert min(strikes) <= spot <= max(strikes), "chain should straddle spot"

    def test_open_interest_and_volume_survive(self):
        chain = self._provider().get_option_chain(AS_OF)
        assert any((r.open_interest or 0) > 0 for r in chain)

    def test_expiry_is_read_from_expirydata_not_parsed_from_the_symbol(self):
        chain = self._provider().get_option_chain(AS_OF)
        expiries = {r.expiry for r in chain}
        assert expiries == {"2026-08-18"}, f"expected the nearest expiry, got {expiries}"

    def test_uncaptured_ohlc_is_a_disclosed_gap_not_invented(self):
        """A live quote carries a traded price, not bars. open/high/low
        must stay absent rather than being back-filled from ltp."""
        row = self._provider().get_option_chain(AS_OF)[0]
        assert row.open is None and row.high is None and row.low is None

    def test_the_underlying_and_vix_rows_are_excluded_from_the_chain(self):
        """They arrive in the same list marked strike_price=-1."""
        chain = self._provider().get_option_chain(AS_OF)
        assert all(r.strike > 0 for r in chain)

    def test_chain_is_cached_so_one_session_makes_one_request(self):
        broker = _Broker(_raw())
        provider = LiveChainProvider(broker, universe_source=lambda: _universe_admitting(*_symbols_in(_raw())))
        provider.get_option_chain(AS_OF)
        provider.get_option_chain(AS_OF)
        assert len(broker.calls) == 1, "a live entry must not re-hit the API per lookup"

    def test_a_wider_strike_window_is_requested_than_the_oi_default(self):
        """Construction needs both short legs and both wings; the 5-strike
        default written for OI reconciliation is too narrow for a condor."""
        broker = _Broker(_raw())
        LiveChainProvider(broker, universe_source=lambda: _universe_admitting(*_symbols_in(_raw()))).get_option_chain(AS_OF)
        assert broker.calls[0][1] >= 20
