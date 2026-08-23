"""Phase 1: the authoritative receiving path, proved rather than described.

Every control here perturbs the thing under test and asserts the test notices.
"""
import logging
import threading
import time

import pytest

from bujji.market_perception.quote import (
    AVAILABLE, NULL_REPORTED, SOURCE_REST, SOURCE_TICK, UNAVAILABLE,
    FieldValue, Quote, from_rest_price, project, unavailable)

LOG = logging.getLogger("test-quote-path")


# --------------------------------------------------------------- projection
class TestProjection:
    def test_full_payload_projected_and_unknown_keys_named(self):
        q = project({"symbol": "NSE:X", "ltp": 100.0, "bid_price": 99.5,
                     "ask_price": 100.5, "bid_size": 50, "ask_size": 75,
                     "open_interest": 1200, "vol_traded_today": 9000,
                     "exch_feed_time": 1_700_000_000, "brand_new_field": 1},
                    recv_wall=1.0, recv_mono=2.0)
        assert q.ltp == 100.0 and q.spread == 1.0
        assert q.value("bid_size") == 50 and q.value("open_interest") == 1200
        # A field this projection does not model is NAMED, not silently lost;
        # the verbatim payload is in the journal.
        assert "brand_new_field" in q.unprojected_keys

    @pytest.mark.parametrize("name", ["bid", "ask", "bid_size", "ask_size",
                                      "open_interest", "volume"])
    def test_missing_field_is_unavailable_never_zero(self, name):
        q = project({"symbol": "NSE:X", "ltp": 100.0})
        fv = q.get(name)
        assert fv.value is None, f"{name} fabricated a value"
        assert fv.value != 0, f"{name} became zero -- zero is a measurement"
        assert fv.availability == UNAVAILABLE
        assert not fv.is_available

    def test_explicit_null_is_distinguished_from_absent(self):
        """The SDK saying 'null' and the SDK not sending the key are different
        facts, and an operator debugging a silent field needs to tell them
        apart."""
        q = project({"symbol": "NSE:X", "ltp": 1.0, "open_interest": None})
        assert q.get("open_interest").availability == NULL_REPORTED
        assert q.get("volume").availability == UNAVAILABLE

    def test_malformed_value_is_not_a_price(self):
        q = project({"symbol": "NSE:X", "ltp": "not-a-number"})
        assert q.ltp is None
        assert q.get("ltp").availability == NULL_REPORTED

    def test_spread_requires_both_sides(self):
        assert project({"symbol": "X", "bid_price": 99.0}).spread is None
        assert project({"symbol": "X", "ask_price": 101.0}).spread is None
        assert project({"symbol": "X", "bid_price": 99.0,
                        "ask_price": 101.0}).spread == 2.0

    def test_rest_quote_declares_itself_and_claims_no_book(self):
        r = from_rest_price("NSE:X", 100.0)
        assert r.source == SOURCE_REST
        assert r.ltp == 100.0
        for name in ("bid", "ask", "open_interest", "volume"):
            assert r.get(name).availability == UNAVAILABLE, name

    def test_projection_into_the_existing_option_leg_model(self):
        """Extends the existing analytical family rather than competing."""
        from bujji.market_perception.models import OptionLeg
        q = project({"symbol": "NSE:X", "ltp": 10.0, "bid_price": 9.5,
                     "ask_price": 10.5, "open_interest": 500})
        leg = q.to_option_leg(strike=24000.0, option_type="CE")
        assert isinstance(leg, OptionLeg)
        assert leg.ltp == 10.0 and leg.spread == 1.0 and leg.open_interest == 500
        assert leg.iv is None, "a tick carries no greeks; inventing them is fabrication"


# ------------------------------------------------------------------ freshness
class TestFreshness:
    def test_age_uses_monotonic_not_wall(self):
        q = project({"symbol": "X", "ltp": 1.0}, recv_wall=1_700_000_000.0,
                    recv_mono=100.0)
        assert q.age_seconds(105.0) == 5.0
        assert q.is_fresh(105.0, 10.0)
        assert not q.is_fresh(200.0, 10.0)

    def test_wall_clock_step_does_not_distort_freshness(self):
        """CONTROL for the defect this replaces. The wall clock jumps an hour
        backwards; monotonic age is unchanged, because it never consulted the
        wall clock."""
        q = project({"symbol": "X", "ltp": 1.0}, recv_wall=1_700_000_000.0,
                    recv_mono=100.0)
        assert q.age_seconds(105.0) == 5.0
        q_stepped = project({"symbol": "X", "ltp": 1.0},
                            recv_wall=1_700_000_000.0 - 3600, recv_mono=100.0)
        assert q_stepped.age_seconds(105.0) == 5.0, (
            "a wall-clock step changed a monotonic age")
        # And the wall figures DO differ, so the control is not vacuous.
        assert q.recv_wall != q_stepped.recv_wall

    def test_a_quote_without_a_monotonic_stamp_is_never_fresh(self):
        """Unknown age is not youth."""
        q = Quote(symbol="X", fields={"ltp": FieldValue(1.0, AVAILABLE, SOURCE_TICK)})
        assert q.age_seconds(999.0) is None
        assert not q.is_fresh(999.0, 10_000.0)


# ------------------------------------------------------------- feed callback
class _Journal:
    """Records exactly what the feed handed it, when it handed it over."""

    def __init__(self):
        self.records = []
        self.at_offer = []

    def offer(self, payload):
        # Freeze at offer time, exactly as the real TickJournal does.
        import json
        self.records.append(json.dumps(payload, sort_keys=True, default=str))
        self.at_offer.append(dict(payload))


def _feed(journal=None, session_id="S1"):
    from bujji.broker.fyers_ws import FyersTickFeed
    return FyersTickFeed("app", "token", LOG, journal=journal,
                         session_id=session_id)


def _deliver(feed, msg):
    """Invoke the real on_message closure the SDK would call."""
    holder = {}

    class _Sock:
        def __init__(self, **kw):
            holder.update(kw)

        def connect(self):
            pass

        def subscribe(self, **kw):
            pass

        def close_connection(self):
            pass

    import bujji.broker.fyers_ws as mod
    real = getattr(mod, "data_ws", None)

    class _Mod:
        FyersDataSocket = _Sock
    mod.data_ws = _Mod()
    try:
        with feed._lifecycle_lock:
            feed._connect_locked()
        holder["on_message"](msg)
    finally:
        if real is not None:
            mod.data_ws = real
    return feed


class TestFeedCallback:
    def test_journal_receives_the_payload_before_projection(self):
        j = _Journal()
        f = _deliver(_feed(j), {"symbol": "NSE:X", "ltp": 100.0,
                                "bid_price": 99.0})
        assert j.records, "the journal was never offered the callback"
        assert "100.0" in j.records[0]
        assert f.latest_quote("NSE:X") is not None

    def test_mutating_the_payload_after_return_cannot_alter_evidence(self):
        """The SDK owns its dict once the callback returns."""
        j = _Journal()
        msg = {"symbol": "NSE:X", "ltp": 100.0}
        f = _deliver(_feed(j), msg)
        msg["ltp"] = 999999.0
        msg["symbol"] = "NSE:MUTATED"
        assert "999999" not in j.records[0], "mutation reached the journal"
        assert f.latest_quote("NSE:X").ltp == 100.0
        assert msg["ltp"] == 999999.0, "CONTROL: the dict really was mutated"

    def test_acknowledgement_is_journaled_and_never_a_price(self):
        """An ack proves the subscription was accepted. It is not data."""
        j = _Journal()
        f = _deliver(_feed(j), {"symbol": "NSE:X", "type": "cn"})
        assert j.records, "the ack was not journaled"
        assert f.acknowledged("NSE:X"), "the ack was not recorded as coverage"
        assert f.latest("NSE:X") is None, "an ack became a price"

    def test_quote_carries_session_and_source(self):
        f = _deliver(_feed(_Journal(), session_id="SESSION-9"),
                     {"symbol": "NSE:X", "ltp": 5.0})
        q = f.latest_quote("NSE:X")
        assert q.session_id == "SESSION-9"
        assert q.source == SOURCE_TICK

    def test_float_store_and_quote_store_cannot_disagree(self):
        """Both are written from the same callback under the same lock, which
        is why the float store may safely remain until its readers migrate."""
        f = _deliver(_feed(_Journal()), {"symbol": "NSE:X", "ltp": 42.5})
        assert f.latest("NSE:X") == f.latest_quote("NSE:X").ltp == 42.5
