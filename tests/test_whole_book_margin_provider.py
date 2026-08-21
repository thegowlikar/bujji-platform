"""Tests — Numeric Risk Governor Gate C scaffold (whole-book margin
provider). All HTTP calls are injected fixtures -- NO network activity,
NO real broker call, ever, in this test file."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from bujji.trading_brain.risk_governor.position_group_fold import LegFillState, LegState, PositionGroupState
from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
    CertifiedWholeBookMarginProvider,
    IllegalMarginProjectionInputError,
    MarginLegRequest,
    MarginSnapshot,
    SPAN_MARGIN_ENDPOINT,
    UncertifiedWholeBookMarginProvider,
    build_span_margin_request,
    margin_snapshot_to_capital_check_input,
    parse_span_margin_response,
    project_whole_book_to_margin_legs,
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


# --------------------------------------------------------------------- #
# margin_snapshot_to_capital_check_input -- pure connector, no live call
# --------------------------------------------------------------------- #

def test_connector_passes_through_certified_snapshot_verbatim():
    snapshot = MarginSnapshot(
        required_margin=45000.0, margin_verified=True, margin_source="WHOLE_BOOK_CERTIFIED",
        as_of=_clock()(), quote=None,
    )
    result = margin_snapshot_to_capital_check_input(snapshot, available_capital=100000.0, configured_risk_capital=80000.0)
    assert result.margin_verified is True
    assert result.required_margin == 45000.0
    assert result.margin_source == "WHOLE_BOOK_CERTIFIED"
    assert result.available_capital == 100000.0
    assert result.configured_risk_capital == 80000.0


def test_connector_passes_through_uncertified_snapshot_verbatim():
    """The connector performs no certification judgment of its own --
    an uncertified snapshot stays margin_verified=False, exactly as
    the provider reported it, regardless of how rich the other fields
    look."""
    snapshot = MarginSnapshot(
        required_margin=45000.0, margin_verified=False, margin_source="WHOLE_BOOK_UNCERTIFIED",
        as_of=_clock()(), quote=None,
    )
    result = margin_snapshot_to_capital_check_input(snapshot, available_capital=100000.0, configured_risk_capital=80000.0)
    assert result.margin_verified is False
    assert result.margin_source == "WHOLE_BOOK_UNCERTIFIED"


def test_connector_passes_through_query_failure_snapshot():
    snapshot = MarginSnapshot(
        required_margin=None, margin_verified=False, margin_source="QUERY_FAILED",
        as_of=_clock()(), quote=None,
    )
    result = margin_snapshot_to_capital_check_input(snapshot, available_capital=100000.0, configured_risk_capital=80000.0)
    assert result.margin_verified is False
    assert result.required_margin is None
    assert result.margin_source == "QUERY_FAILED"


def test_connector_never_infers_available_capital_or_configured_risk_capital():
    """These two fields are NOT derivable from a MarginSnapshot -- a
    margin provider only ever reports what a book requires, never what
    capital the account actually has or how much of it policy allows
    risking. The connector must pass through exactly what the caller
    explicitly supplied, never substitute a default."""
    snapshot = MarginSnapshot(
        required_margin=1000.0, margin_verified=True, margin_source="WHOLE_BOOK_CERTIFIED",
        as_of=_clock()(), quote=None,
    )
    result = margin_snapshot_to_capital_check_input(snapshot, available_capital=None, configured_risk_capital=0.0)
    assert result.available_capital is None
    assert result.configured_risk_capital == 0.0


def test_connector_output_feeds_assess_capital_and_still_fails_closed_end_to_end():
    """Integration proof: the connector's output is a real,
    directly-usable CapitalCheckInput -- feeding a certified-but-
    over-budget snapshot through the real assess_capital still VETOes
    on CAPITAL_EXCEEDED, not silently ALLOWing just because
    margin_verified=True."""
    from bujji.trading_brain.risk_governor.capital_check import assess_capital

    snapshot = MarginSnapshot(
        required_margin=90000.0, margin_verified=True, margin_source="WHOLE_BOOK_CERTIFIED",
        as_of=_clock()(), quote=None,
    )
    capital_input = margin_snapshot_to_capital_check_input(
        snapshot, available_capital=100000.0, configured_risk_capital=50000.0,
    )
    result = assess_capital(capital_input, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "CAPITAL_EXCEEDED"


# --------------------------------------------------------------------- #
# project_whole_book_to_margin_legs -- pure, no live call
# --------------------------------------------------------------------- #

def _contract(symbol):
    return SimpleNamespace(contract_symbol=symbol)


def _open_state(pg_id, coid, filled_qty, reduced_qty=0):
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state="OPEN",
        legs={coid: LegState(
            client_order_id=coid, contract_id="C1", requested_quantity=None, submit_status="LEG_ACKED",
            fill=LegFillState(client_order_id=coid, cumulative_filled_quantity=filled_qty,
                               cumulative_average_fill_price=100.0, reduced_quantity=reduced_qty),
        )},
    )


def _constructed_state(pg_id, coid, requested_qty):
    return PositionGroupState(
        position_group_id=pg_id, lifecycle_state="CONSTRUCTED",
        legs={coid: LegState(
            client_order_id=coid, contract_id="C1", requested_quantity=requested_qty,
            submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id=coid),
        )},
    )


def test_projects_open_leg_using_net_quantity():
    state = _open_state("PG-1", "COID-1", filled_qty=75)
    legs = project_whole_book_to_margin_legs(
        [state],
        contracts_by_client_order_id={"COID-1": _contract("NSE:NIFTY26AUG24800CE")},
        sides_by_client_order_id={"COID-1": "SELL"},
        reference_prices_by_client_order_id={"COID-1": 60.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    assert len(legs) == 1
    assert legs[0].symbol == "NSE:NIFTY26AUG24800CE"
    assert legs[0].qty == 75
    assert legs[0].side == -1
    assert legs[0].limit_price == 60.0


def test_projects_constructed_leg_using_requested_quantity():
    state = _constructed_state("PG-2", "COID-2", requested_qty=75)
    legs = project_whole_book_to_margin_legs(
        [state],
        contracts_by_client_order_id={"COID-2": _contract("NSE:NIFTY26AUG24800PE")},
        sides_by_client_order_id={"COID-2": "BUY"},
        reference_prices_by_client_order_id={"COID-2": 45.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    assert len(legs) == 1
    assert legs[0].qty == 75
    assert legs[0].side == 1


def test_zero_net_quantity_leg_skipped_not_sent_as_meaningless_zero():
    state = _open_state("PG-3", "COID-3", filled_qty=75, reduced_qty=75)  # fully closed out -> net 0
    legs = project_whole_book_to_margin_legs(
        [state],
        contracts_by_client_order_id={"COID-3": _contract("X")},
        sides_by_client_order_id={"COID-3": "SELL"},
        reference_prices_by_client_order_id={"COID-3": 10.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    assert legs == []


def test_non_active_lifecycle_state_excluded_entirely():
    closed_state = dataclasses_replace_lifecycle(_open_state("PG-4", "COID-4", filled_qty=75), "CLOSED")
    legs = project_whole_book_to_margin_legs(
        [closed_state],
        contracts_by_client_order_id={"COID-4": _contract("X")},
        sides_by_client_order_id={"COID-4": "SELL"},
        reference_prices_by_client_order_id={"COID-4": 10.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    assert legs == []


def dataclasses_replace_lifecycle(state, new_lifecycle):
    import dataclasses
    return dataclasses.replace(state, lifecycle_state=new_lifecycle)


def test_missing_contract_raises_never_silently_omits_leg():
    state = _open_state("PG-5", "COID-5", filled_qty=75)
    with pytest.raises(IllegalMarginProjectionInputError):
        project_whole_book_to_margin_legs(
            [state],
            contracts_by_client_order_id={},   # missing
            sides_by_client_order_id={"COID-5": "SELL"},
            reference_prices_by_client_order_id={"COID-5": 10.0},
            instrument_type="OPTIDX", product_type="MIS",
        )


def test_missing_side_raises():
    state = _open_state("PG-6", "COID-6", filled_qty=75)
    with pytest.raises(IllegalMarginProjectionInputError):
        project_whole_book_to_margin_legs(
            [state],
            contracts_by_client_order_id={"COID-6": _contract("X")},
            sides_by_client_order_id={},   # missing
            reference_prices_by_client_order_id={"COID-6": 10.0},
            instrument_type="OPTIDX", product_type="MIS",
        )


def test_invalid_side_string_raises():
    state = _open_state("PG-7", "COID-7", filled_qty=75)
    with pytest.raises(IllegalMarginProjectionInputError):
        project_whole_book_to_margin_legs(
            [state],
            contracts_by_client_order_id={"COID-7": _contract("X")},
            sides_by_client_order_id={"COID-7": "LONG"},   # not "BUY"/"SELL"
            reference_prices_by_client_order_id={"COID-7": 10.0},
            instrument_type="OPTIDX", product_type="MIS",
        )


def test_missing_reference_price_raises():
    state = _open_state("PG-8", "COID-8", filled_qty=75)
    with pytest.raises(IllegalMarginProjectionInputError):
        project_whole_book_to_margin_legs(
            [state],
            contracts_by_client_order_id={"COID-8": _contract("X")},
            sides_by_client_order_id={"COID-8": "SELL"},
            reference_prices_by_client_order_id={},   # missing
            instrument_type="OPTIDX", product_type="MIS",
        )


def test_missing_quantity_on_one_leg_of_multileg_group_raises_not_silently_partial():
    """Audit finding: an earlier version silently skipped a leg with a
    missing/zero requested_quantity, which let a 2-leg CONSTRUCTED
    spread with one malformed leg produce a margin request for only
    the REMAINING leg -- e.g. dropping the short leg from a vertical
    spread and pricing only the long leg as if it were a naked
    position, dangerously understating the true margin requirement.
    Must raise instead, never silently price a partial structure."""
    state = PositionGroupState(
        position_group_id="PG-PARTIAL", lifecycle_state="CONSTRUCTED",
        legs={
            "LEG-A": LegState(client_order_id="LEG-A", contract_id="C1", requested_quantity=None,
                               submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-A")),
            "LEG-B": LegState(client_order_id="LEG-B", contract_id="C2", requested_quantity=75,
                               submit_status="LEG_NOT_SUBMITTED", fill=LegFillState(client_order_id="LEG-B")),
        },
    )
    with pytest.raises(IllegalMarginProjectionInputError):
        project_whole_book_to_margin_legs(
            [state],
            contracts_by_client_order_id={"LEG-A": _contract("SHORT_CE"), "LEG-B": _contract("LONG_CE")},
            sides_by_client_order_id={"LEG-A": "SELL", "LEG-B": "BUY"},
            reference_prices_by_client_order_id={"LEG-A": 60.0, "LEG-B": 20.0},
            instrument_type="OPTIDX", product_type="MIS",
        )


def test_multiple_position_groups_combined_into_one_flat_list():
    state_a = _open_state("PG-9", "COID-9", filled_qty=75)
    state_b = _constructed_state("PG-10", "COID-10", requested_qty=50)
    legs = project_whole_book_to_margin_legs(
        [state_a, state_b],
        contracts_by_client_order_id={"COID-9": _contract("A"), "COID-10": _contract("B")},
        sides_by_client_order_id={"COID-9": "SELL", "COID-10": "BUY"},
        reference_prices_by_client_order_id={"COID-9": 10.0, "COID-10": 20.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    assert len(legs) == 2
    assert {leg.symbol for leg in legs} == {"A", "B"}


def test_projected_legs_feed_build_span_margin_request_end_to_end():
    """Integration proof: the projected legs are directly usable by
    the existing pure request-builder, no adapter needed."""
    state = _open_state("PG-11", "COID-11", filled_qty=75)
    legs = project_whole_book_to_margin_legs(
        [state],
        contracts_by_client_order_id={"COID-11": _contract("NSE:NIFTY26AUG24800CE")},
        sides_by_client_order_id={"COID-11": "SELL"},
        reference_prices_by_client_order_id={"COID-11": 60.0},
        instrument_type="OPTIDX", product_type="MIS",
    )
    body = build_span_margin_request(legs)
    assert body["data"][0]["symbol"] == "NSE:NIFTY26AUG24800CE"
    assert body["data"][0]["side"] == -1
