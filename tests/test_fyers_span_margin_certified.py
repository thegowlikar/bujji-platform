"""FYERS span_margin / funds — live-certified response-shape regression tests.

These fixtures are the EXACT raw responses captured from a real FYERS
account on 2026-07-19 during live certification (see docs/AUDIT_LOG.md Pass 8
and docs/CAPITAL_MANAGEMENT_ENGINE.md). They exist so a future SDK/endpoint
change that breaks parsing is caught immediately, instead of silently
returning None (fail-safe) in production without anyone noticing why.
"""
import pytest

from bujji.broker.fyers import FyersBroker
from bujji.core.config import BrokerConfig
from bujji.core.enums import Direction, OptionType
from bujji.core.models import OptionContract


def _broker():
    cfg = BrokerConfig(app_id="test-app", access_token="test-token")
    import logging
    return FyersBroker(cfg, logging.getLogger("test"))


def _contracts():
    ce = OptionContract("NSE:NIFTY2672124350CE", "NIFTY", 24350, OptionType.CE, "2026-07-21", 65)
    pe = OptionContract("NSE:NIFTY2672124350PE", "NIFTY", 24350, OptionType.PE, "2026-07-21", 65)
    return ce, pe


# ---------------------------------------------------------------------- #
# span_margin -- captured LIVE 2026-07-19 raw responses
# ---------------------------------------------------------------------- #
LIVE_SPAN_MARGIN_STRADDLE_RESPONSE = {
    "code": 200, "message": "", "s": "ok", "latency": "",
    "data": {"span": 143177, "expo": 0, "total": 143177, "benefit": 142048.25},
    "individual_info": {
        "101126072157354": {"ltp_info": 115.3, "span": 142048.25, "expo": 0, "total": 142048.25},
        "101126072157355": {"ltp_info": 132.55, "span": 143177, "expo": 0, "total": 143177},
    },
}

LIVE_SPAN_MARGIN_SINGLE_LEG_RESPONSE = {
    "code": 200, "message": "", "s": "ok", "latency": "",
    "data": {"span": 142048.25, "expo": 0, "total": 142048.25, "benefit": 0},
    "individual_info": {
        "101126072157354": {"ltp_info": 115.3, "span": 142048.25, "expo": 0, "total": 142048.25},
    },
}

LIVE_SPAN_MARGIN_INVALID_SYMBOL_RESPONSE = {
    "s": "error", "code": -310, "message": "Please provide valid symbols",
}

LIVE_SPAN_MARGIN_MALFORMED_RESPONSE = {
    "s": "error", "code": -50, "message": "Invalid input",
}

LIVE_SPAN_MARGIN_BAD_AUTH_RESPONSE = {
    "s": "error", "code": -17, "message": "Could not authenticate the user",
}

LIVE_FUNDS_RESPONSE = {
    "code": 200, "message": "", "s": "ok",
    "fund_limit": [
        {"id": 1, "title": "Total Balance", "equityAmount": 180000, "commodityAmount": 0},
        {"id": 2, "title": "Utilized Amount", "equityAmount": 0, "commodityAmount": 0},
        {"id": 3, "title": "Clear Balance", "equityAmount": 180000, "commodityAmount": 0},
        {"id": 4, "title": "Realized Profit and Loss", "equityAmount": 0, "commodityAmount": 0},
        {"id": 5, "title": "Collaterals", "equityAmount": 0, "commodityAmount": 0},
        {"id": 6, "title": "Fund Transfer", "equityAmount": 0, "commodityAmount": 0},
        {"id": 7, "title": "Receivables", "equityAmount": 0, "commodityAmount": 0},
        {"id": 8, "title": "Adhoc Limit", "equityAmount": 0, "commodityAmount": 0},
        {"id": 9, "title": "Limit at start of the day", "equityAmount": 180000, "commodityAmount": 0},
        {"id": 10, "title": "Available Balance", "equityAmount": 180000, "commodityAmount": 0},
    ],
}


@pytest.mark.asyncio
async def test_parses_live_captured_straddle_response(monkeypatch):
    broker = _broker()

    class FakeResp:
        def json(self_inner):
            return LIVE_SPAN_MARGIN_STRADDLE_RESPONSE

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp())
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)
    assert result is not None
    assert result["margin_per_lot"] == 143177.0
    assert result["verified"] is False
    assert result["source"] == "fyers_span_margin"


@pytest.mark.asyncio
async def test_parses_live_captured_single_leg_response(monkeypatch):
    class FakeResp:
        def json(self_inner):
            return LIVE_SPAN_MARGIN_SINGLE_LEG_RESPONSE

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp())
    broker = _broker()
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)
    assert result["margin_per_lot"] == 142048.25


@pytest.mark.asyncio
async def test_multi_leg_hedging_benefit_is_reflected_not_additive(monkeypatch):
    """Live-verified: combined CE+PE margin is barely above a single leg's
    margin (143177 vs 142048.25), not the naive sum -- proves SPAN hedging
    benefit is genuinely applied, not just two legs priced independently."""
    single_leg_total = LIVE_SPAN_MARGIN_SINGLE_LEG_RESPONSE["data"]["total"]
    combined_total = LIVE_SPAN_MARGIN_STRADDLE_RESPONSE["data"]["total"]
    naive_sum_if_no_benefit = single_leg_total * 2
    assert combined_total < naive_sum_if_no_benefit
    assert combined_total == pytest.approx(single_leg_total + 1128.75, abs=1.0)


@pytest.mark.asyncio
async def test_invalid_symbol_error_response_returns_none(monkeypatch):
    class FakeResp:
        def json(self_inner):
            return LIVE_SPAN_MARGIN_INVALID_SYMBOL_RESPONSE

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp())
    broker = _broker()
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)
    assert result is None  # Fail safe -- never a guessed margin.


@pytest.mark.asyncio
async def test_malformed_payload_error_response_returns_none(monkeypatch):
    class FakeResp:
        def json(self_inner):
            return LIVE_SPAN_MARGIN_MALFORMED_RESPONSE

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp())
    broker = _broker()
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)
    assert result is None


@pytest.mark.asyncio
async def test_bad_auth_error_response_returns_none(monkeypatch):
    class FakeResp:
        def json(self_inner):
            return LIVE_SPAN_MARGIN_BAD_AUTH_RESPONSE

    monkeypatch.setattr("requests.post", lambda *a, **k: FakeResp())
    broker = _broker()
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)
    assert result is None


@pytest.mark.asyncio
async def test_network_exception_returns_none_never_raises(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("simulated network failure")

    monkeypatch.setattr("requests.post", boom)
    broker = _broker()
    ce, pe = _contracts()
    result = await broker.get_order_margin(ce, pe)  # Must not raise.
    assert result is None


# ---------------------------------------------------------------------- #
# funds -- captured LIVE 2026-07-19 raw response
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_parses_live_captured_funds_response(monkeypatch):
    broker = _broker()

    async def fake_call(action):
        assert action == "funds"
        return LIVE_FUNDS_RESPONSE

    monkeypatch.setattr(broker, "_call", fake_call)
    result = await broker.get_funds()
    assert result["account_equity"] == 180000.0
    assert result["available_margin"] == 180000.0
    assert result["cash_balance"] == 180000.0  # "Clear Balance", not the pre-certification "Clear Cash" guess.
    assert result["used_margin"] == 0.0
    assert result["collateral"] == 0.0  # "Collaterals" row, newly mapped after certification.
