"""FYERS whole-book span margin transport + wiring.

The response fixture below is the EXACT live-certified 2026-07-19 shape from
FyersBroker.get_order_margin's certification evidence (figures nested under
"data") -- these tests prove the caller's normalization makes Gate C's
unchanged parser interpret that real shape correctly, and that verification
semantics survive the whole chain into capital_check's ALLOW/VETO.
"""
from __future__ import annotations

import datetime
import importlib.util
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import bujji.broker.fyers_span_margin as span  # noqa: E402
from bujji.trading_brain.risk_governor.capital_check import assess_capital  # noqa: E402
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (  # noqa: E402
    MarginLegRequest,
    UncertifiedWholeBookMarginProvider,
    margin_snapshot_to_capital_check_input,
)

_spec = importlib.util.spec_from_file_location(
    "bujji_options_os_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(runner)

LOG = logging.getLogger("test")
CLOCK = lambda: datetime.datetime(2026, 8, 19, 10, 0, 0)  # noqa: E731

# Live-certified 2026-07-19 response shape, verbatim structure.
CERTIFIED_RESPONSE = {
    "code": 200, "message": "", "s": "ok", "latency": "",
    "data": {"span": 83210.5, "expo": 41605.25, "total": 124815.75, "benefit": 76409.0},
    "individual_info": {"101126": {"span": 100000.0, "expo": 50000.0, "total": 150000.0}},
}


class _FakeResponse:
    def __init__(self, status_code, payload, json_raises=False):
        self.status_code = status_code
        self._payload = payload
        self._json_raises = json_raises

    def json(self):
        if self._json_raises:
            raise ValueError("not json")
        return self._payload


class _FakePost:
    def __init__(self, status_code=200, payload=None, json_raises=False):
        self.calls = []
        self._resp = _FakeResponse(status_code, payload, json_raises)

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return self._resp


def _short_straddle(price_ce=101.5, price_pe=91.5):
    return [
        MarginLegRequest(symbol="NSE:NIFTY26AUG24400CE", qty=65, side=-1,
                         instrument_type="2", product_type="INTRADAY", limit_price=price_ce),
        MarginLegRequest(symbol="NSE:NIFTY26AUG24400PE", qty=65, side=-1,
                         instrument_type="2", product_type="INTRADAY", limit_price=price_pe),
    ]


# ------------------------------------------------------------------ caller

def test_certified_shape_flows_through_the_unchanged_parser():
    post = _FakePost(payload=CERTIFIED_RESPONSE)
    provider = UncertifiedWholeBookMarginProvider(
        span.FyersSpanMarginCaller("APPID-100", "tok", post=post))
    snap = provider.get_portfolio_margin(_short_straddle(), CLOCK)
    assert snap.required_margin == 124815.75
    assert snap.margin_verified is False
    assert snap.margin_source == "WHOLE_BOOK_UNCERTIFIED"
    assert snap.quote.parsed_successfully is True
    assert (snap.quote.span, snap.quote.expo, snap.quote.benefit) == (83210.5, 41605.25, 76409.0)
    assert snap.quote.individual_info == CERTIFIED_RESPONSE["individual_info"]
    # Nothing discarded: the untouched broker response rides along.
    assert snap.quote.raw_response["broker_response"] == CERTIFIED_RESPONSE


def test_auth_header_and_request_body_match_the_certified_contract():
    post = _FakePost(payload=CERTIFIED_RESPONSE)
    caller = span.FyersSpanMarginCaller("APPID-100", "sekret", post=post)
    caller("https://api.fyers.in/api/v2/span_margin", {"data": [{"symbol": "X"}]})
    call = post.calls[0]
    assert call["headers"] == {"Authorization": "APPID-100:sekret"}
    assert call["url"] == "https://api.fyers.in/api/v2/span_margin"
    assert call["json"] == {"data": [{"symbol": "X"}]}


def test_every_call_takes_a_pacer_slot(monkeypatch):
    hits = []
    monkeypatch.setattr(span, "_wait_for_slot", lambda: hits.append(1))
    caller = span.FyersSpanMarginCaller("a", "b", post=_FakePost(payload=CERTIFIED_RESPONSE))
    caller("u", {"data": []})
    caller("u", {"data": []})
    assert len(hits) == 2


@pytest.mark.parametrize("status,payload", [
    (401, {"s": "error", "code": -17, "message": "Could not authenticate the user"}),
    (400, {"s": "error", "code": -310, "message": "Please provide valid symbols"}),
    (200, {"s": "ok", "code": 200}),           # ok but no data object
    (200, {"s": "ok", "data": "not-a-dict"}),  # malformed data
])
def test_certified_error_shapes_raise(status, payload):
    caller = span.FyersSpanMarginCaller("a", "b", post=_FakePost(status_code=status, payload=payload))
    with pytest.raises(span.SpanMarginCallError):
        caller("u", {"data": []})


def test_a_failing_caller_fails_the_provider_closed_not_open():
    provider = UncertifiedWholeBookMarginProvider(
        span.FyersSpanMarginCaller("a", "b", post=_FakePost(status_code=401, payload={"s": "error"})))
    snap = provider.get_portfolio_margin(_short_straddle(), CLOCK)
    assert snap.required_margin is None
    assert snap.margin_verified is False
    assert snap.margin_source == "QUERY_FAILED"


def test_missing_credentials_refuse_construction():
    with pytest.raises(ValueError):
        span.FyersSpanMarginCaller("", "tok")


# ------------------------------------------------------- explanation adapter

def _provider(certified=False, payload=CERTIFIED_RESPONSE):
    return span.build_fyers_margin_provider(
        app_id="a", access_token="b", certified=certified,
        post=_FakePost(payload=payload))


def test_explanation_never_diverges_from_the_plain_number():
    p = _provider()
    snap, explanation = p.get_portfolio_margin_with_explanation(_short_straddle(), CLOCK)
    assert explanation.total_required_margin == snap.required_margin == 124815.75
    assert explanation.explanation_source == snap.margin_source


def test_short_straddle_is_flagged_naked_and_contributions_sum_to_total():
    _, e = _provider().get_portfolio_margin_with_explanation(_short_straddle(), CLOCK)
    assert "NAKED_SHORT_EXPOSURE" in e.risk_flags
    assert "BROKER_QUOTED" in e.risk_flags
    assert "HEDGE_PRESENT" not in e.risk_flags
    assert sum(c.margin_contribution for c in e.contributing_legs) == pytest.approx(124815.75)


def test_hedged_book_is_flagged_hedged():
    legs = _short_straddle() + [
        MarginLegRequest(symbol="NSE:NIFTY26AUG24600CE", qty=65, side=1,
                         instrument_type="2", product_type="INTRADAY", limit_price=40.0),
    ]
    _, e = _provider().get_portfolio_margin_with_explanation(legs, CLOCK)
    assert "HEDGE_PRESENT" in e.risk_flags
    assert e.total_long_exposure == pytest.approx(65 * 40.0)


def test_query_failure_yields_invalid_state_explanation():
    p = span.build_fyers_margin_provider(
        app_id="a", access_token="b", certified=False,
        post=_FakePost(status_code=401, payload={"s": "error"}))
    snap, e = p.get_portfolio_margin_with_explanation(_short_straddle(), CLOCK)
    assert snap.margin_source == "QUERY_FAILED"
    assert e.risk_classification == "INVALID_STATE"
    assert e.total_required_margin is None


# ------------------------------------------- verification -> ALLOW/VETO chain

def test_uncertified_margin_vetoes_capital_check():
    snap = _provider(certified=False).get_portfolio_margin(_short_straddle(), CLOCK)
    inputs = margin_snapshot_to_capital_check_input(
        snap, available_capital=10_000_000.0, configured_risk_capital=500_000.0)
    assert assess_capital(inputs, CLOCK).decision == "VETO"


def test_certified_margin_with_ample_capital_allows():
    snap = _provider(certified=True).get_portfolio_margin(_short_straddle(), CLOCK)
    assert snap.margin_verified is True
    assert snap.margin_source == "WHOLE_BOOK_CERTIFIED"
    inputs = margin_snapshot_to_capital_check_input(
        snap, available_capital=10_000_000.0, configured_risk_capital=500_000.0)
    assert assess_capital(inputs, CLOCK).decision == "ALLOW"


# ---------------------------------------------------------------- runner

def test_runner_defaults_to_simulated_with_a_warning(caplog):
    with caplog.at_level(logging.WARNING):
        p = runner._make_margin_provider({}, log=LOG)
    assert type(p).__name__ == "SimulatedMarginProvider"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_runner_builds_uncertified_fyers_provider(monkeypatch):
    monkeypatch.setenv("FYERS_APP_ID", "APP")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TOK")
    p = runner._make_margin_provider({"margin": {"type": "fyers_uncertified"}}, log=LOG)
    assert p.margin_source_label == "WHOLE_BOOK_UNCERTIFIED"


def test_runner_builds_certified_fyers_provider(monkeypatch):
    monkeypatch.setenv("FYERS_APP_ID", "APP")
    monkeypatch.setenv("FYERS_ACCESS_TOKEN", "TOK")
    p = runner._make_margin_provider({"margin": {"type": "fyers_certified"}}, log=LOG)
    assert p.margin_source_label == "WHOLE_BOOK_CERTIFIED"


def test_runner_fails_closed_without_credentials(monkeypatch):
    monkeypatch.delenv("FYERS_APP_ID", raising=False)
    monkeypatch.delenv("FYERS_ACCESS_TOKEN", raising=False)
    with pytest.raises(RuntimeError):
        runner._make_margin_provider({"margin": {"type": "fyers_certified"}}, log=LOG)


def test_runner_rejects_a_bare_string_margin_entry():
    # providers: entries are type-blocks; the paper-only deployment guard
    # iterates them on that assumption, so a stray string must fail loudly.
    with pytest.raises(RuntimeError):
        runner._make_margin_provider({"margin": "fyers_certified"}, log=LOG)


def test_runner_rejects_unknown_margin_mode():
    with pytest.raises(RuntimeError):
        runner._make_margin_provider({"margin": {"type": "definitely_real"}}, log=LOG)
