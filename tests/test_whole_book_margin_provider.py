"""Tests — Numeric Risk Governor Gate C scaffold (whole-book margin
provider). All HTTP calls are injected fixtures -- NO network activity,
NO real broker call, ever, in this test file."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    CertifiedWholeBookMarginProvider,
    MarginLegRequest,
    SPAN_MARGIN_ENDPOINT,
    UncertifiedWholeBookMarginProvider,
    build_span_margin_request,
    parse_span_margin_response,
)


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _legs():
    return [
        MarginLegRequest(symbol="NSE:NIFTY26AUG24800CE", qty=50, side=-1,
                          instrument_type="OPTIDX", product_type="MIS", limit_price=100.0),
        MarginLegRequest(symbol="NSE:NIFTY26AUG24900CE", qty=50, side=1,
                          instrument_type="OPTIDX", product_type="MIS", limit_price=40.0),
    ]


# --------------------------------------------------------------------- #
# Pure request/response functions
# --------------------------------------------------------------------- #

def test_build_request_shape():
    body = build_span_margin_request(_legs())
    assert body["data"][0]["symbol"] == "NSE:NIFTY26AUG24800CE"
    assert body["data"][0]["side"] == -1
    assert body["data"][1]["side"] == 1
    assert len(body["data"]) == 2


def test_build_request_requires_at_least_one_leg():
    with pytest.raises(ValueError):
        build_span_margin_request([])


def test_parse_response_happy_path():
    quote = parse_span_margin_response(
        {"benefit": 500.0, "expo": 3000.0, "span": 2500.0, "total": 3000.0, "individual_info": {"a": 1}}
    )
    assert quote.parsed_successfully is True
    assert quote.total_margin == 3000.0
    assert quote.individual_info == {"a": 1}


def test_parse_response_missing_total_fails_closed():
    quote = parse_span_margin_response({"benefit": 500.0})
    assert quote.parsed_successfully is False
    assert quote.total_margin is None
    assert "total" in quote.parse_error


def test_parse_response_non_numeric_total_fails_closed():
    quote = parse_span_margin_response({"total": "not-a-number"})
    assert quote.parsed_successfully is False


def test_parse_response_not_a_dict_fails_closed():
    quote = parse_span_margin_response(["unexpected", "list"])
    assert quote.parsed_successfully is False
    assert quote.total_margin is None


def test_parse_response_bool_total_rejected():
    # isinstance(True, int) is True in Python -- must not silently accept a bool as a margin figure.
    quote = parse_span_margin_response({"total": True})
    assert quote.parsed_successfully is False


def test_parse_response_malformed_individual_info_ignored_not_crashed():
    quote = parse_span_margin_response({"total": 100.0, "individual_info": "not-a-dict"})
    assert quote.parsed_successfully is True
    assert quote.individual_info is None


# --------------------------------------------------------------------- #
# Uncertified provider -- structurally incapable of margin_verified=True
# --------------------------------------------------------------------- #

def test_uncertified_provider_never_verified_even_with_a_perfect_response():
    def fixture_caller(url, body):
        assert url == SPAN_MARGIN_ENDPOINT
        return {"total": 3000.0, "benefit": 500.0}

    provider = UncertifiedWholeBookMarginProvider(fixture_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.required_margin == 3000.0
    assert snapshot.margin_source == "WHOLE_BOOK_UNCERTIFIED"


def test_uncertified_provider_query_failure_fails_closed():
    def failing_caller(url, body):
        raise ConnectionError("simulated network failure")

    provider = UncertifiedWholeBookMarginProvider(failing_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.required_margin is None
    assert snapshot.margin_source == "QUERY_FAILED"


def test_uncertified_provider_unparseable_response_fails_closed():
    def bad_shape_caller(url, body):
        return {"unexpected": "shape"}

    provider = UncertifiedWholeBookMarginProvider(bad_shape_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.margin_source == "QUERY_FAILED_UNPARSEABLE"
    assert snapshot.quote.parsed_successfully is False


def test_uncertified_provider_empty_legs_fails_closed():
    def caller(url, body):
        return {"total": 100.0}

    provider = UncertifiedWholeBookMarginProvider(caller)
    snapshot = provider.get_portfolio_margin([], clock=_clock())
    assert snapshot.margin_verified is False
    assert snapshot.margin_source == "QUERY_FAILED_EMPTY_LEGS"


# --------------------------------------------------------------------- #
# Certified provider -- only class that can ever return verified=True,
# and only because it is a distinct subclass, never a runtime flag.
# --------------------------------------------------------------------- #

def test_certified_provider_returns_verified_true_on_success():
    def fixture_caller(url, body):
        return {"total": 3000.0}

    provider = CertifiedWholeBookMarginProvider(fixture_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is True
    assert snapshot.margin_source == "WHOLE_BOOK_CERTIFIED"


def test_certified_provider_still_fails_closed_on_query_failure():
    def failing_caller(url, body):
        raise TimeoutError("simulated timeout")

    provider = CertifiedWholeBookMarginProvider(failing_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is False   # certification never overrides a real query failure
    assert snapshot.margin_source == "QUERY_FAILED"


def test_certified_provider_still_fails_closed_on_unparseable_response():
    def bad_caller(url, body):
        return {"garbage": True}

    provider = CertifiedWholeBookMarginProvider(bad_caller)
    snapshot = provider.get_portfolio_margin(_legs(), clock=_clock())
    assert snapshot.margin_verified is False


def test_uncertified_and_certified_are_distinct_classes_not_a_flag():
    """Structural proof: there is no constructor argument on either
    class that flips verification -- the ONLY way to get verified=True
    is to use the distinct CertifiedWholeBookMarginProvider class."""
    import inspect
    uncertified_sig = inspect.signature(UncertifiedWholeBookMarginProvider.__init__)
    certified_sig = inspect.signature(CertifiedWholeBookMarginProvider.__init__)
    assert "verified" not in uncertified_sig.parameters
    assert "certified" not in uncertified_sig.parameters
    assert uncertified_sig.parameters.keys() == certified_sig.parameters.keys()
    assert UncertifiedWholeBookMarginProvider is not CertifiedWholeBookMarginProvider
