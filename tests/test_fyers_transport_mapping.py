"""FyersBroker mapping correctness — verified against live FYERS response
shapes (see docs/FYERS_TRANSPORT_READINESS.md for the verification record).

These tests exercise FyersBroker's parsing/param-construction logic directly
against canned responses shaped exactly like what was empirically observed
from a live, authenticated FYERS session. `_call` itself remains unwired
(no real network transport is invoked); these prove the mapping logic sitting
on top of `_call` is correct for when a transport is wired in.
"""
import pytest

from bujji.broker.fyers import FyersBroker
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest


class RecordingFyers(FyersBroker):
    """Records every _call invocation's (action, params) and returns a
    caller-supplied canned response."""

    def __init__(self, config, logger):
        super().__init__(config, logger)
        self.calls: list[tuple] = []
        self.responses: dict[str, object] = {}

    async def _call(self, action, **params):
        self.calls.append((action, params))
        resp = self.responses.get(action, {"s": "ok"})
        return resp() if callable(resp) else resp


def _creds(config):
    config.broker.app_id = "test-app"
    config.broker.access_token = "test-token"
    return config


@pytest.mark.asyncio
async def test_get_spot_uses_verified_index_symbol_and_parses_shape(config, logger):
    """Regression test for a real bug caught during live verification:
    "NSE:{underlying}-INDEX" naively produces "NSE:NIFTY-INDEX", which is not
    a real instrument. The verified-live symbol is "NSE:NIFTY50-INDEX"."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"NSE:NIFTY50-INDEX": {"last_price": 24270.85}}
    spot = await broker.get_spot("NIFTY")
    assert spot == 24270.85
    action, params = broker.calls[0]
    assert action == "ltp"
    assert params["instruments"] == ["NSE:NIFTY50-INDEX"]  # list, not scalar.


@pytest.mark.asyncio
async def test_get_spot_unmapped_underlying_falls_back_unverified(config, logger):
    """Anything other than NIFTY has no verified mapping — falls back to the
    old, explicitly-unverified construction rather than a silent guess."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"NSE:BANKNIFTY-INDEX": {"last_price": 1.0}}
    await broker.get_spot("BANKNIFTY")
    _, params = broker.calls[0]
    assert params["instruments"] == ["NSE:BANKNIFTY-INDEX"]


@pytest.mark.asyncio
async def test_get_ltp_parses_verified_nested_shape(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    broker.responses["ltp"] = {"NSE:NIFTY25JAN22000CE": {"last_price": 118.5}}
    ltp = await broker.get_ltp(contract)
    assert ltp == 118.5


@pytest.mark.asyncio
async def test_get_recent_candles_uses_verified_symbol_and_parses_six_field_rows(
    config, logger
):
    """The historical endpoint's verified-live symbol form is "NSE:NIFTY 50"
    (space-separated) — confirmed DIFFERENT from the LTP endpoint's
    "NSE:NIFTY50-INDEX" form; the two were not assumed interchangeable."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["historical"] = {
        "s": "ok", "code": 200, "message": "",
        "candles": [[1783050300, 24375.65, 24378.15, 24295.15, 24302.6, 21779034]],
    }
    candles = await broker.get_recent_candles("NIFTY", 5, 1)
    action, params = broker.calls[0]
    assert action == "historical"
    assert params["symbol"] == "NSE:NIFTY 50"
    assert len(candles) == 1
    assert candles[0].open == 24375.65
    assert candles[0].volume == 21779034
    assert candles[0].timestamp.tzinfo is not None  # D2: tz-aware IST.


@pytest.mark.asyncio
async def test_place_order_uses_verified_field_names(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["place_order"] = {
        "s": "ok", "id": "FY-ORDER-1", "status": 2, "filledQty": 75,
        "tradedPrice": 118.5,
    }
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-1")
    result = await broker.place_order(req)

    action, params = broker.calls[0]
    assert action == "place_order"
    # Verified real tool schema: separate exchange/tradingsymbol, string
    # transaction_type/order_type, required product, tag (not orderTag).
    assert params["exchange"] == "NSE"
    assert params["tradingsymbol"] == "NIFTY25JAN22000CE"
    assert params["transaction_type"] == "SELL"
    assert params["order_type"] == "MARKET"
    assert params["product"] == "MIS"
    assert params["tag"] == "CID-1"
    assert result.broker_order_id == "FY-ORDER-1"
    assert result.filled_quantity == 75


@pytest.mark.asyncio
async def test_get_order_uses_cached_order_id_when_known(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker._cid_to_order_id["CID-1"] = "FY-ORDER-1"  # noqa: SLF001
    broker.responses["order_history"] = {"s": "ok", "id": "FY-ORDER-1", "status": 2}

    result = await broker.get_order("CID-1")
    action, params = broker.calls[0]
    assert action == "order_history"
    assert params["order_id"] == "FY-ORDER-1"
    assert result.broker_order_id == "FY-ORDER-1"


@pytest.mark.asyncio
async def test_get_order_falls_back_to_tag_scan_when_uncached(config, logger):
    """The C3-critical case: we don't yet know the FYERS order_id (e.g. a
    crash right after placing) — must find it by scanning today's order book
    for a matching tag, per the real tool surface (no direct tag lookup)."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["orders"] = {
        "s": "ok",
        "orderBook": [
            {"id": "FY-OTHER", "tag": "CID-OTHER", "status": 2},
            {"id": "FY-ORDER-1", "tag": "CID-1", "status": 2, "filledQty": 75},
        ],
    }
    result = await broker.get_order("CID-1")
    action, _ = broker.calls[0]
    assert action == "orders"
    assert result.broker_order_id == "FY-ORDER-1"
    assert broker._cid_to_order_id["CID-1"] == "FY-ORDER-1"  # noqa: SLF001


@pytest.mark.asyncio
async def test_get_order_reports_unknown_when_not_found_anywhere(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["orders"] = {"s": "ok", "orderBook": []}
    result = await broker.get_order("CID-NEVER-PLACED")
    assert result.status.value == "UNKNOWN"


@pytest.mark.asyncio
async def test_cancel_order_resolves_order_id_via_cache(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker._cid_to_order_id["CID-1"] = "FY-ORDER-1"  # noqa: SLF001
    broker.responses["cancel_order"] = {"s": "ok", "id": "FY-ORDER-1", "status": 6}
    await broker.cancel_order("CID-1")
    action, params = broker.calls[0]
    assert action == "cancel_order"
    assert params["order_id"] == "FY-ORDER-1"  # FYERS's id, not our client tag.


@pytest.mark.asyncio
async def test_get_open_positions_parses_verified_shape(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["positions"] = {
        "s": "ok", "code": 200, "message": "",
        "netPositions": [
            {"symbol": "NSE:NIFTY25JAN22000CE", "netQty": -75, "netAvg": 118.5},
            {"symbol": "NSE:SOMETHING-EQ", "netQty": 0, "netAvg": 0.0},
        ],
        "overall": {"count_open": 1},
    }
    positions = await broker.get_open_positions()
    assert len(positions) == 1  # The flat (netQty=0) leg is excluded.
    assert positions[0]["symbol"] == "NSE:NIFTY25JAN22000CE"
    assert positions[0]["side"] == "SELL"
    assert positions[0]["qty"] == 75
