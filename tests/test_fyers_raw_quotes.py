"""Phase 17I.6.1 — Raw FYERS Response Preservation Discovery Layer.

`get_spot_raw()`/`get_futures_quote_raw()`/`get_vix_raw()` mirror the same
raw-passthrough discipline already proven by `get_option_chain_raw()` and
`get_depth()` (see `test_fyers_transport_mapping.py`'s own coverage of
those two): no extraction, no renaming, no discarding. These tests prove
each new method preserves every field of a canned response -- including
fields no processed method has ever looked at -- and correctly propagates
both authentication and non-auth broker failures rather than swallowing
them.

Same posture as `test_fyers_transport_mapping.py`: `_call` is overridden
via `RecordingFyers` with a caller-supplied canned response; no real
network call is made.
"""
import pytest

from bujji.broker.errors import AuthenticationError
from bujji.broker.fyers import FyersBroker, _futures_symbol, _index_symbol


class RecordingFyers(FyersBroker):
    """Same recording double as test_fyers_transport_mapping.py's own --
    reproduced here rather than imported, matching this project's existing
    convention of not sharing fixtures/doubles across test files."""

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


# --- get_spot_raw() ---------------------------------------------------------
@pytest.mark.asyncio
async def test_get_spot_raw_returns_the_full_response_unmodified(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    canned = {
        "s": "ok", "code": 200, "message": "",
        "d": [{"n": "NSE:NIFTY50-INDEX", "v": {
            "lp": 24270.85, "prev_close_price": 24180.0, "exch_feed_time": 1755071233,
            "some_unknown_field": "unexplored",
        }}],
    }
    broker.responses["ltp"] = canned
    raw = await broker.get_spot_raw("NIFTY")
    assert raw == canned
    action, params = broker.calls[0]
    assert action == "ltp"
    assert params["symbols"] == _index_symbol("NIFTY")


@pytest.mark.asyncio
async def test_get_spot_raw_does_not_transform_lp_or_other_fields(config, logger):
    """The specific discipline this method exists to prove: unlike
    get_spot()/_quote() (which extract only `lp` as a float and raise
    KeyError on a symbol miss), every field FYERS sent must survive
    untouched, at its original type, at its original nesting."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:NIFTY50-INDEX", "v": {"lp": 24270.85, "ch": 90.85, "chp": 0.38}}],
    }
    raw = await broker.get_spot_raw("NIFTY")
    row = raw["d"][0]
    assert row["v"]["lp"] == 24270.85  # Untransformed -- not coerced away from whatever type it arrived as.
    assert row["v"]["ch"] == 90.85
    assert row["v"]["chp"] == 0.38


@pytest.mark.asyncio
async def test_get_spot_raw_raises_authentication_error(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "error", "code": -8, "message": "Your token has expired"}
    with pytest.raises(AuthenticationError):
        await broker.get_spot_raw("NIFTY")


@pytest.mark.asyncio
async def test_get_spot_raw_does_not_swallow_non_auth_broker_failures(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "error", "code": 429, "message": "request limit reached"}
    with pytest.raises(RuntimeError):
        await broker.get_spot_raw("NIFTY")


# --- get_futures_quote_raw() -------------------------------------------------
@pytest.mark.asyncio
async def test_get_futures_quote_raw_returns_both_legs_unmodified(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    symbol = _futures_symbol("NIFTY")
    ltp_response = {
        "s": "ok",
        "d": [{"n": symbol, "v": {"lp": 24427.9, "volume": 700440, "some_unknown_field": True}}],
    }
    depth_response = {
        "s": "ok",
        "d": {symbol: {
            "oi": 12803765, "pdoi": 12645685, "oipercent": 1.25,
            "bids": [{"price": 24427.5, "volume": 65}],
            "asks": [{"price": 24428.0, "volume": 130}],
        }},
    }
    broker.responses["ltp"] = ltp_response
    broker.responses["depth"] = depth_response

    raw = await broker.get_futures_quote_raw("NIFTY")

    assert raw["symbol"] == symbol
    assert raw["ltp_response"] == ltp_response
    assert raw["depth_response"] == depth_response


@pytest.mark.asyncio
async def test_get_futures_quote_raw_preserves_oi_and_depth_fields(config, logger):
    """The specific discipline this method exists to prove for futures:
    unlike get_futures_quote() (which extracts only ltp/volume from the
    ltp leg and only oi from the depth leg), OI and every depth-related
    field (pdoi, oipercent, bids, asks, or anything else) must survive
    untouched in the raw depth_response."""
    broker = RecordingFyers(_creds(config).broker, logger)
    symbol = _futures_symbol("NIFTY")
    broker.responses["ltp"] = {"s": "ok", "d": [{"n": symbol, "v": {"lp": 24427.9}}]}
    broker.responses["depth"] = {
        "s": "ok",
        "d": {symbol: {
            "oi": 12803765, "pdoi": 12645685, "oipercent": 1.25,
            "tick_Size": 0.05, "upper_ckt": 26800.0, "lower_ckt": 22000.0,
        }},
    }
    raw = await broker.get_futures_quote_raw("NIFTY")
    depth_row = raw["depth_response"]["d"][symbol]
    assert depth_row["oi"] == 12803765
    assert depth_row["pdoi"] == 12645685
    assert depth_row["oipercent"] == 1.25
    assert depth_row["tick_Size"] == 0.05


@pytest.mark.asyncio
async def test_get_futures_quote_raw_raises_authentication_error_on_ltp_leg(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "error", "code": -8, "message": "Your token has expired"}
    with pytest.raises(AuthenticationError):
        await broker.get_futures_quote_raw("NIFTY")


@pytest.mark.asyncio
async def test_get_futures_quote_raw_raises_authentication_error_on_depth_leg(config, logger):
    """Unlike get_futures_quote() (which treats a failed depth call as
    best-effort and returns oi=None), this diagnostic method must not
    swallow a depth-leg failure -- it exists purely to inspect what FYERS
    sends, so both legs must succeed or the caller finds out."""
    symbol = _futures_symbol("NIFTY")
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "ok", "d": [{"n": symbol, "v": {"lp": 24427.9}}]}
    broker.responses["depth"] = {"s": "error", "code": -8, "message": "Your token has expired"}
    with pytest.raises(AuthenticationError):
        await broker.get_futures_quote_raw("NIFTY")


@pytest.mark.asyncio
async def test_get_futures_quote_raw_does_not_swallow_non_auth_depth_failure(config, logger):
    symbol = _futures_symbol("NIFTY")
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "ok", "d": [{"n": symbol, "v": {"lp": 24427.9}}]}
    broker.responses["depth"] = {"s": "error", "code": 429, "message": "request limit reached"}
    with pytest.raises(RuntimeError):
        await broker.get_futures_quote_raw("NIFTY")


# --- get_vix_raw() -----------------------------------------------------------
@pytest.mark.asyncio
async def test_get_vix_raw_returns_the_full_response_unmodified(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    canned = {
        "s": "ok",
        "d": [{"n": "NSE:INDIAVIX-INDEX", "v": {
            "lp": 13.02, "prev_close_price": 13.15, "exch_feed_time": 1755071233,
            "another_unexplored_field": 42,
        }}],
    }
    broker.responses["ltp"] = canned
    raw = await broker.get_vix_raw()
    assert raw == canned
    action, params = broker.calls[0]
    assert action == "ltp"
    assert params["symbols"] == "NSE:INDIAVIX-INDEX"


@pytest.mark.asyncio
async def test_get_vix_raw_preserves_level_prev_close_and_unknown_fields(config, logger):
    """The specific discipline this method exists to prove for VIX: unlike
    get_vix() (which extracts only level/prev_close and returns None
    below a positivity threshold), every field -- including ones get_vix()
    has never looked at -- must survive untouched."""
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {
        "s": "ok",
        "d": [{"n": "NSE:INDIAVIX-INDEX", "v": {
            "lp": 11.53, "prev_close_price": 11.69, "ch": -0.16, "chp": -1.37,
        }}],
    }
    raw = await broker.get_vix_raw()
    v = raw["d"][0]["v"]
    assert v["lp"] == 11.53
    assert v["prev_close_price"] == 11.69
    assert v["ch"] == -0.16
    assert v["chp"] == -1.37


@pytest.mark.asyncio
async def test_get_vix_raw_raises_authentication_error(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "error", "code": -8, "message": "Your token has expired"}
    with pytest.raises(AuthenticationError):
        await broker.get_vix_raw()


@pytest.mark.asyncio
async def test_get_vix_raw_does_not_swallow_non_auth_broker_failures(config, logger):
    broker = RecordingFyers(_creds(config).broker, logger)
    broker.responses["ltp"] = {"s": "error", "code": 429, "message": "request limit reached"}
    with pytest.raises(RuntimeError):
        await broker.get_vix_raw()
