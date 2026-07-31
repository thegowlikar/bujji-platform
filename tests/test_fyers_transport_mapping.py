"""FyersBroker mapping correctness — verified against a real, authenticated
FYERS session via the official fyers-apiv3 SDK (see
docs/FYERS_TRANSPORT_READINESS.md for the live verification record).

These tests exercise FyersBroker's parsing/param-construction logic directly
against canned responses shaped exactly like what was empirically observed
live. `_call` itself is overridden here (no real network call is made in the
test suite); the real implementation is exercised separately by a live
verification script, not by the automated test suite.
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
    a real instrument. The verified-live symbol is "NSE:NIFTY50-INDEX", and
    the real SDK's quotes() response is a LIST under "d", not a dict keyed by
    symbol (that shape was the MCP tool's own reshaping, not the raw API)."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:NIFTY50-INDEX", "v": {"lp": 24270.85}}],
    }
    spot = await broker.get_spot("NIFTY")
    assert spot == 24270.85
    action, params = broker.calls[0]
    assert action == "ltp"
    assert params["symbols"] == "NSE:NIFTY50-INDEX"  # Single symbol string.


@pytest.mark.asyncio
async def test_get_spot_unmapped_underlying_falls_back_unverified(config, logger):
    """Anything other than NIFTY has no verified mapping — falls back to the
    old, explicitly-unverified construction rather than a silent guess."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok", "d": [{"n": "NSE:BANKNIFTY-INDEX", "v": {"lp": 1.0}}],
    }
    await broker.get_spot("BANKNIFTY")
    _, params = broker.calls[0]
    assert params["symbols"] == "NSE:BANKNIFTY-INDEX"


@pytest.mark.asyncio
async def test_get_ltp_parses_verified_list_shape(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:NIFTY25JAN22000CE", "v": {"lp": 118.5}}],
    }
    ltp = await broker.get_ltp(contract)
    assert ltp == 118.5


@pytest.mark.asyncio
async def test_get_recent_candles_uses_verified_symbol_and_parses_six_field_rows(
    config, logger
):
    """Both LTP and historical use the same canonical "NSE:NIFTY50-INDEX"
    symbol — an earlier pass believed history() needed "NSE:NIFTY 50"
    (space-separated) based on an MCP-mediated verification session that
    silently normalizes that alias; calling the real SDK directly proved
    that form is rejected outright ("Invalid symbol provided")."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["historical"] = {
        "s": "ok", "code": 200, "message": "",
        "candles": [[1783050300, 24375.65, 24378.15, 24295.15, 24302.6, 21779034]],
    }
    candles = await broker.get_recent_candles("NIFTY", 5, 1)
    action, params = broker.calls[0]
    assert action == "historical"
    assert params["symbol"] == "NSE:NIFTY50-INDEX"
    assert "range_from" in params and "range_to" in params  # Real SDK: date range, not count.
    assert len(candles) == 1
    assert candles[0].open == 24375.65
    assert candles[0].volume == 21779034
    assert candles[0].timestamp.tzinfo is not None  # D2: tz-aware IST.


@pytest.mark.asyncio
async def test_get_recent_candles_truncates_to_requested_count(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["historical"] = {
        "s": "ok",
        "candles": [
            [1783050300 + i * 300, 100 + i, 101 + i, 99 + i, 100 + i, 1000]
            for i in range(10)
        ],
    }
    candles = await broker.get_recent_candles("NIFTY", 5, 3)
    assert len(candles) == 3
    assert candles[-1].open == 109  # The most recent 3, in order.


@pytest.mark.asyncio
async def test_get_recent_candles_raises_on_rate_limit_instead_of_silent_empty(
    config, logger
):
    """Regression test for a real gap found during unattended-readiness
    verification: a rate-limited historical response (verified live —
    {"s": "error", "code": 429, "message": "request limit reached"}) was
    previously indistinguishable from "no candles in this window" and
    silently returned []. It must now raise, so ExecutionEngine's existing
    retry/backoff actually retries it — the same behavior get_spot/get_ltp
    already had via their own missing-key lookup."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["historical"] = {
        "s": "error", "code": 429, "message": "request limit reached",
    }
    with pytest.raises(RuntimeError, match="429"):
        await broker.get_recent_candles("NIFTY", 5, 1)


@pytest.mark.asyncio
async def test_get_recent_candles_still_raises_authentication_error_first(
    config, logger
):
    """An auth-classified error must still raise AuthenticationError, not the
    new generic RuntimeError — the two must not collide."""
    from bujji.broker.errors import AuthenticationError
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["historical"] = {
        "s": "error", "code": -8, "message": "Your token has expired",
    }
    with pytest.raises(AuthenticationError):
        await broker.get_recent_candles("NIFTY", 5, 1)


@pytest.mark.asyncio
async def test_place_order_uses_verified_sdk_field_names(config, logger):
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
    # Verified against the real fyers-apiv3 SDK's place_order() signature
    # (introspected directly): combined symbol, int side/type, productType.
    assert params["symbol"] == "NSE:NIFTY25JAN22000CE"
    assert params["side"] == -1  # SELL.
    assert params["type"] == 2   # Market.
    assert params["productType"] == "INTRADAY"
    assert params["orderTag"] == "CID-1"
    assert result.broker_order_id == "FY-ORDER-1"
    assert result.filled_quantity == 75


@pytest.mark.asyncio
async def test_place_order_buy_and_limit(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["place_order"] = {"s": "ok", "id": "FY-2", "status": 1}
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    req = OrderRequest(contract, Side.BUY, 75, "CID-2", limit_price=100.0)
    await broker.place_order(req)
    _, params = broker.calls[0]
    assert params["side"] == 1
    assert params["type"] == 1
    assert params["limitPrice"] == 100.0


@pytest.mark.asyncio
async def test_market_order_with_reference_price_still_sends_market(config, logger):
    """Semantic Cleanup Sprint: a MARKET order carrying an observed
    reference_price (limit_price left None) must still reach the real
    SDK as type=2 (Market), not be silently converted to a Limit order.
    This is the exact production safety issue the sprint's own
    micro-review found and this sprint closes."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["place_order"] = {"s": "ok", "id": "FY-3", "status": 2}
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-3", reference_price=124.15)
    await broker.place_order(req)
    _, params = broker.calls[0]
    assert params["type"] == 2  # Market, unaffected by reference_price.
    assert params["limitPrice"] == 0  # reference_price never reaches limitPrice.


@pytest.mark.asyncio
async def test_fyers_broker_ignores_reference_price_entirely(config, logger):
    """FyersBroker must never read reference_price for anything --
    varying it while holding limit_price constant must produce an
    identical SDK call every time."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["place_order"] = {"s": "ok", "id": "FY-4", "status": 1}
    contract = OptionContract("NSE:NIFTY25JAN22000CE", "NIFTY", 22000,
                              OptionType.CE, "25JAN", 75)
    req_a = OrderRequest(contract, Side.BUY, 75, "CID-A", limit_price=100.0, reference_price=1.0)
    req_b = OrderRequest(contract, Side.BUY, 75, "CID-B", limit_price=100.0, reference_price=9999.0)
    await broker.place_order(req_a)
    await broker.place_order(req_b)
    _, params_a = broker.calls[0]
    _, params_b = broker.calls[1]
    assert params_a["type"] == params_b["type"] == 1
    assert params_a["limitPrice"] == params_b["limitPrice"] == 100.0


@pytest.mark.asyncio
async def test_get_order_uses_cached_order_id_when_known(config, logger):
    """No separate order-history-by-id method exists on the real SDK —
    get_order always fetches the full orderbook and filters locally."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker._cid_to_order_id["CID-1"] = "FY-ORDER-1"  # noqa: SLF001
    broker.responses["orders"] = {
        "s": "ok",
        "orderBook": [{"id": "FY-ORDER-1", "status": 2, "filledQty": 75}],
    }
    result = await broker.get_order("CID-1")
    action, _ = broker.calls[0]
    assert action == "orders"
    assert result.broker_order_id == "FY-ORDER-1"


@pytest.mark.asyncio
async def test_get_order_falls_back_to_tag_scan_when_uncached(config, logger):
    """The C3-critical case: we don't yet know the FYERS order_id (e.g. a
    crash right after placing) — must find it by scanning today's order book
    for a matching orderTag."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["orders"] = {
        "s": "ok",
        "orderBook": [
            {"id": "FY-OTHER", "orderTag": "CID-OTHER", "status": 2},
            {"id": "FY-ORDER-1", "orderTag": "CID-1", "status": 2, "filledQty": 75},
        ],
    }
    result = await broker.get_order("CID-1")
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
    assert params["id"] == "FY-ORDER-1"  # FYERS's own id, not our client tag.


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


@pytest.mark.asyncio
async def test_get_quote_parses_verified_shape(config, logger):
    """Regression fixture: shaped exactly like the real live capture used
    to build the Liquidity Brain (2026-07-20) -- bid/ask/spread under the
    quote's `v` dict, spread == ask - bid."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:NIFTY2672124250CE",
              "v": {"lp": 82.4, "bid": 82.4, "ask": 82.6, "spread": 0.2}}],
    }
    contract = OptionContract(symbol="NSE:NIFTY2672124250CE", underlying="NIFTY",
                              strike=24250, option_type=OptionType.CE,
                              expiry="2026-07-21", lot_size=65)
    quote = await broker.get_quote(contract)
    assert quote == {"bid": 82.4, "ask": 82.6, "spread": 0.2}


@pytest.mark.asyncio
async def test_get_quote_returns_none_when_symbol_not_in_response(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "ok", "d": []}
    contract = OptionContract(symbol="NSE:MISSING", underlying="NIFTY", strike=24250,
                              option_type=OptionType.CE, expiry="2026-07-21", lot_size=65)
    assert await broker.get_quote(contract) is None


@pytest.mark.asyncio
async def test_get_quote_returns_none_on_crossed_or_zero_quote(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:X", "v": {"lp": 0, "bid": 0, "ask": 0}}],
    }
    contract = OptionContract(symbol="NSE:X", underlying="NIFTY", strike=24250,
                              option_type=OptionType.CE, expiry="2026-07-21", lot_size=65)
    assert await broker.get_quote(contract) is None


@pytest.mark.asyncio
async def test_get_option_chain_parses_the_nested_data_key(config, logger):
    """Regression test for a real bug caught during live verification: the
    optionchain endpoint's payload is nested under a top-level "data" key
    (unlike the plain quotes endpoint, which uses "d" at the top level) --
    an earlier draft read `data["optionsChain"]` directly and silently got
    zero strikes back every time. Shaped exactly like the real live capture
    used to build the Structure Brain (2026-07-20)."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["optionchain"] = {
        "s": "ok", "code": 200, "message": "",
        "data": {
            "optionsChain": [
                {"symbol": "NSE:NIFTY50-INDEX", "strike_price": -1, "option_type": "",
                 "ltp": 24243.1},  # Underlying row -- must be skipped.
                {"symbol": "NSE:NIFTY2672124100PE", "strike_price": 24100,
                 "option_type": "PE", "oi": 17299295, "prev_oi": 10241300, "oich": 7057995},
                {"symbol": "NSE:NIFTY2672124100CE", "strike_price": 24100,
                 "option_type": "CE", "oi": 3940820, "prev_oi": 2905820, "oich": 1035000},
            ],
        },
    }
    chain = await broker.get_option_chain("NIFTY", 24243.1, strike_count=3)
    assert chain == [(24100.0, 3940820.0, 17299295.0)]
    action, params = broker.calls[0]
    assert action == "optionchain"
    assert params["symbol"] == "NSE:NIFTY50-INDEX"
    assert params["strikecount"] == 3


@pytest.mark.asyncio
async def test_get_option_chain_returns_empty_list_for_no_real_strikes(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["optionchain"] = {
        "s": "ok", "data": {"optionsChain": [
            {"symbol": "NSE:NIFTY50-INDEX", "strike_price": -1, "option_type": "", "ltp": 24243.1},
        ]},
    }
    chain = await broker.get_option_chain("NIFTY", 24243.1)
    assert chain == []


@pytest.mark.asyncio
async def test_get_vix_parses_verified_shape(config, logger):
    """Regression fixture: shaped exactly like the real live capture used
    to build the Event Brain's VIX half (2026-07-20)."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:INDIAVIX-INDEX",
              "v": {"lp": 13.02, "prev_close_price": 13.15}}],
    }
    vix = await broker.get_vix()
    assert vix == {"level": 13.02, "prev_close": 13.15}
    action, params = broker.calls[0]
    assert action == "ltp"
    assert params["symbols"] == "NSE:INDIAVIX-INDEX"


@pytest.mark.asyncio
async def test_get_vix_returns_none_when_symbol_not_in_response(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "ok", "d": []}
    assert await broker.get_vix() is None


@pytest.mark.asyncio
async def test_get_vix_returns_none_for_nonpositive_level(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:INDIAVIX-INDEX", "v": {"lp": 0, "prev_close_price": 13.15}}],
    }
    assert await broker.get_vix() is None


@pytest.mark.asyncio
async def test_get_vix_omits_prev_close_when_nonpositive_or_missing(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:INDIAVIX-INDEX", "v": {"lp": 13.02}}],
    }
    vix = await broker.get_vix()
    assert vix == {"level": 13.02}
