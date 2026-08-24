"""The gate refuses a decision whose required fields it cannot supply."""
import ast
import pathlib

import pytest

from bujji.market_perception.quote import (
    AVAILABLE, SOURCE_REPLAY, SOURCE_REST, SOURCE_TICK, FieldValue, Quote,
    from_rest_price, project)
from bujji.production_runtime.market_data_gate import (
    QUALITY_GOOD, QUALITY_INVALID, REASON_DISALLOWED_PROVENANCE,
    REASON_REQUIRED_FIELD_STALE, REASON_REQUIRED_FIELD_UNAVAILABLE,
    assess_quote_fields)

NOW = 1000.0


def q(**fields):
    return project({"symbol": "NSE:X", **fields}, recv_wall=1.0, recv_mono=NOW)


class TestFieldRequirements:
    def test_all_required_fields_present_and_fresh_permits(self):
        v = assess_quote_fields({"NSE:X": q(ltp=100.0, bid_price=99.0, ask_price=101.0)},
                                ["ltp", "bid", "ask"], now_mono=NOW,
                                max_age_seconds=30)
        assert v.may_trade and v.quality == QUALITY_GOOD

    def test_missing_required_field_refuses(self):
        v = assess_quote_fields({"NSE:X": q(ltp=100.0)}, ["ltp", "bid"],
                                now_mono=NOW, max_age_seconds=30)
        assert not v.may_trade
        assert any(REASON_REQUIRED_FIELD_UNAVAILABLE in r for r in v.reasons)
        assert "NSE:X:bid" in v.missing_fields

    def test_stale_field_refuses(self):
        v = assess_quote_fields({"NSE:X": q(ltp=100.0)}, ["ltp"],
                                now_mono=NOW + 500, max_age_seconds=30)
        assert not v.may_trade
        assert any(REASON_REQUIRED_FIELD_STALE in r for r in v.reasons)

    def test_a_field_not_required_may_be_missing(self):
        """CONTROL: the gate must not refuse on fields nobody asked for."""
        v = assess_quote_fields({"NSE:X": q(ltp=100.0)}, ["ltp"],
                                now_mono=NOW, max_age_seconds=30)
        assert v.may_trade, "refused over a field the decision never needed"


class TestProvenance:
    def test_rest_price_refused_when_tick_evidence_required(self):
        r = from_rest_price("NSE:X", 100.0, observed_wall=1.0, observed_mono=NOW)
        v = assess_quote_fields({"NSE:X": r}, ["ltp"], now_mono=NOW,
                                max_age_seconds=30, allowed_sources=("LIVE_TICK",))
        assert not v.may_trade
        assert any(REASON_DISALLOWED_PROVENANCE in x for x in v.reasons)

    def test_rest_permitted_when_the_decision_allows_it(self):
        r = from_rest_price("NSE:X", 100.0, observed_wall=1.0, observed_mono=NOW)
        v = assess_quote_fields({"NSE:X": r}, ["ltp"], now_mono=NOW,
                                max_age_seconds=30,
                                allowed_sources=("LIVE_TICK", "REST_FALLBACK"))
        assert v.may_trade

    def test_replayed_data_cannot_reach_a_live_decision(self):
        rq = Quote(symbol="NSE:X", recv_mono=NOW, source=SOURCE_REPLAY,
                   fields={"ltp": FieldValue(100.0, AVAILABLE, SOURCE_REPLAY,
                                             1.0, NOW)})
        v = assess_quote_fields({"NSE:X": rq}, ["ltp"], now_mono=NOW,
                                max_age_seconds=30, allowed_sources=("LIVE_TICK",))
        assert not v.may_trade

    def test_unknown_provenance_refuses(self):
        uq = Quote(symbol="NSE:X", recv_mono=NOW,
                   fields={"ltp": FieldValue(100.0, AVAILABLE, "SOMETHING_NEW",
                                             1.0, NOW)})
        v = assess_quote_fields({"NSE:X": uq}, ["ltp"], now_mono=NOW,
                                max_age_seconds=30)
        assert not v.may_trade, "an unrecognised source was permitted"

    def test_mixed_sources_stay_visible(self):
        v = assess_quote_fields(
            {"A": q(ltp=1.0), "B": from_rest_price("B", 2.0, observed_mono=NOW)},
            ["ltp"], now_mono=NOW, max_age_seconds=30,
            allowed_sources=("LIVE_TICK", "REST_FALLBACK"))
        assert v.may_trade
        assert "LIVE_TICK" in (v.origin or "") and "REST_FALLBACK" in (v.origin or "")


class TestFailClosed:
    def test_symbol_with_no_quote_refuses(self):
        v = assess_quote_fields({}, ["ltp"], now_mono=NOW, max_age_seconds=30,
                                required_symbols=["NSE:MISSING"])
        assert not v.may_trade

    def test_quote_without_monotonic_stamp_refuses(self):
        bad = Quote(symbol="NSE:X",
                    fields={"ltp": FieldValue(1.0, AVAILABLE, SOURCE_TICK)})
        v = assess_quote_fields({"NSE:X": bad}, ["ltp"], now_mono=NOW,
                                max_age_seconds=30)
        assert not v.may_trade, "a quote of unknowable age was permitted"

    def test_no_symbols_at_all_refuses(self):
        assert not assess_quote_fields({}, ["ltp"], now_mono=NOW,
                                       max_age_seconds=30).may_trade


class TestNoParallelSystem:
    """Structural guarantees: one path, not two."""

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_exactly_one_sdk_field_map_exists(self):
        """Monday's evidence must have ONE place to be applied."""
        hits = []
        for f in (self.ROOT / "bujji").rglob("*.py"):
            if "PROVISIONAL_SDK_FIELD_MAP" in f.read_text():
                hits.append(str(f.relative_to(self.ROOT)))
        assert hits == ["bujji/market_perception/quote.py"], hits

    def test_the_runtime_feed_has_no_second_journal(self):
        src = (self.ROOT / "bujji/broker/fyers_ws.py").read_text()
        assert src.count("self._journal.offer(") == 1, (
            "more than one journal offer in the feed -- a second store")

    def test_the_gate1_harness_is_not_imported_by_runtime(self):
        """The harness is a measurement instrument, never a runtime component."""
        offenders = []
        for f in list((self.ROOT / "bujji").rglob("*.py")) + \
                [self.ROOT / "bujji_options_os_runner.py"]:
            t = f.read_text()
            if "gate1_measure" in t or "gate1_opening_capture" in t:
                offenders.append(str(f.relative_to(self.ROOT)))
        assert offenders == [], offenders

    def test_providers_implement_the_typed_contract(self):
        from bujji.production_runtime.intraday_price_provider import (
            HistoricalTickProvider, IntradayPriceProvider, LiveTickProvider,
            WebsocketTickProvider)
        for cls in (HistoricalTickProvider, WebsocketTickProvider, LiveTickProvider):
            assert "get_quotes" in cls.__dict__, f"{cls.__name__} not migrated"
        # get_prices must be DERIVED, defined once on the base only.
        for cls in (HistoricalTickProvider, WebsocketTickProvider, LiveTickProvider):
            assert "get_prices" not in cls.__dict__, (
                f"{cls.__name__} overrides get_prices -- that is a second authority")
        assert "get_prices" in IntradayPriceProvider.__dict__

    def test_the_float_adapter_is_derived_from_quotes(self):
        """It cannot disagree with the typed path, because it is made from it."""
        class _P:
            from bujji.production_runtime.intraday_price_provider import (
                IntradayPriceProvider as _B)

        from bujji.production_runtime.intraday_price_provider import (
            IntradayPriceProvider)

        class Stub(IntradayPriceProvider):
            def get_quotes(self, contracts_by_symbol, as_of):
                return {s: project({"symbol": s, "ltp": 7.0}, recv_mono=NOW)
                        for s in contracts_by_symbol}

        s = Stub()
        assert s.get_prices({"A": object()}, "x") == {"A": 7.0}


class TestRuntimeConstruction:
    """The enabled runtime must actually build the authoritative path."""

    ROOT = pathlib.Path(__file__).resolve().parent.parent

    def test_runner_constructs_the_feed_with_journal_and_session(self):
        src = (self.ROOT / "bujji_options_os_runner.py").read_text()
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "FyersTickFeed"]
        assert calls, "the runner no longer constructs FyersTickFeed"
        for c in calls:
            kw = {k.arg for k in c.keywords}
            missing = {"journal", "session_id"} - kw
            assert not missing, (
                f"the runtime builds the feed without {missing} -- callbacks "
                f"would not be durable or attributable")

    def test_feed_and_journal_are_the_same_object(self):
        """One journal, not one per component."""
        src = (self.ROOT / "bujji_options_os_runner.py").read_text()
        assert "journal=self._tick_journal" in src
        assert src.count("TickJournal(") == 1, "more than one TickJournal built"
