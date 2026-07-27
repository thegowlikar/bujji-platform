"""Live Observation Producer Framework v1 (Engineering Series 74) tests.

Tests for `bujji/live_observation/`, the live-production framework that
feeds the SAME Series 73A Observation Contract as Series 73B/73C's
historical-replay ingestion, but from real-time events instead of
Bhavcopy rows. Covers event translation (reusing 73A/73B/73C builders,
never duplicating them), observation-creation determinism, serialization
determinism, aggregation window ordering (including out-of-order/late
ticks), lifecycle transitions (valid + invalid), the Deliverable 10
end-to-end demonstration, and an AST-based isolation firewall mirroring
`tests/test_futures_observation_domain.py::TestIsolation` plus a FYERS
SDK import check specific to this live-producer package.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.live_observation import config, engine, journal, models, query, runner, serialization, taxonomy
from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.market_observation.models import Observation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _tick_event(event_id: str, seq: int, ts: str, instrument: str, price: float, volume=None) -> models.LiveObservationEvent:
    payload = {"instrument": instrument, "price": price}
    if volume is not None:
        payload["volume"] = volume
    return models.LiveObservationEvent(
        event_id=event_id,
        event_type=taxonomy.EVENT_TICK_RECEIVED,
        timestamp=ts,
        source="FYERS",
        payload=payload,
        sequence=seq,
    )


# ---------------------------------------------------------------------------
# Event translation -- reuse, never duplicate
# ---------------------------------------------------------------------------
class TestEventTranslation:
    def test_tick_received_translates_to_price_observation(self):
        event = _tick_event("e1", 0, "2026-07-24T09:15:00+05:30", "NSE:SBIN-EQ", 500.0)
        obs = engine.translate_event(event)
        assert isinstance(obs, Observation)
        assert obs.identity.observation_type == moc_taxonomy.TYPE_PRICE
        assert obs.value.value_kind == moc_taxonomy.VALUE_KIND_SCALAR
        assert obs.value.payload == 500.0
        assert obs.identity.observation_id.startswith("OBS-")

    def test_vix_updated_translates_to_vix_observation(self):
        event = models.LiveObservationEvent(
            event_id="e2", event_type=taxonomy.EVENT_VIX_UPDATED,
            timestamp="2026-07-24T09:15:00+05:30", source="FYERS",
            payload={"instrument": "INDIA VIX", "price": 13.2}, sequence=0,
        )
        obs = engine.translate_event(event)
        assert obs.identity.observation_type == moc_taxonomy.TYPE_VOLATILITY_VIX

    def test_candle_closed_translates_to_ohlc_observation(self):
        event = models.LiveObservationEvent(
            event_id="e3", event_type=taxonomy.EVENT_CANDLE_CLOSED,
            timestamp="2026-07-24T09:16:00+05:30", source="FYERS",
            payload={"instrument": "NSE:SBIN-EQ", "open": 100, "high": 102, "low": 99, "close": 101},
            sequence=0,
        )
        obs = engine.translate_event(event)
        assert obs.value.value_kind == moc_taxonomy.VALUE_KIND_OHLC
        assert obs.value.payload["close"] == 101

    def test_futures_updated_delegates_to_futures_observation_builder(self):
        event = models.LiveObservationEvent(
            event_id="e4", event_type=taxonomy.EVENT_FUTURES_UPDATED,
            timestamp="2026-07-24T09:15:00+05:30", source="FYERS",
            payload={
                "underlying": "NIFTY", "instrument_symbol": "NIFTY26JULFUT", "expiry": "2026-07-30",
                "close": 25000.0, "open": 24950.0, "high": 25050.0, "low": 24900.0,
                "volume": 1000.0, "open_interest": 500.0,
            },
            sequence=0,
        )
        obs = engine.translate_event(event)
        from bujji.futures_observation.models import FuturesObservation
        assert isinstance(obs, FuturesObservation)
        assert obs.observation.identity.observation_type == moc_taxonomy.TYPE_FUTURES

    def test_option_chain_updated_delegates_to_options_observation_builder(self):
        event = models.LiveObservationEvent(
            event_id="e5", event_type=taxonomy.EVENT_OPTION_CHAIN_UPDATED,
            timestamp="2026-07-24T09:15:00+05:30", source="FYERS",
            payload={
                "underlying": "NIFTY", "instrument_symbol": "NIFTY26JUL25000CE", "strike": 25000.0,
                "expiry": "2026-07-30", "option_type": "CE",
                "close": 120.0, "open": 110.0, "high": 130.0, "low": 105.0,
                "settlement": None, "volume": 200.0, "open_interest": 300.0,
                "underlying_price": 25010.0,
            },
            sequence=0,
        )
        obs = engine.translate_event(event)
        from bujji.options_observation.models import OptionObservation
        assert isinstance(obs, OptionObservation)
        assert obs.observation.identity.observation_type == moc_taxonomy.TYPE_OPTION_CHAIN

    @pytest.mark.parametrize("event_type", [
        taxonomy.EVENT_CONNECTION_ESTABLISHED,
        taxonomy.EVENT_CONNECTION_LOST,
        taxonomy.EVENT_HEARTBEAT,
        taxonomy.EVENT_PRODUCER_ERROR,
    ])
    def test_non_translatable_events_return_none(self, event_type):
        event = models.LiveObservationEvent(
            event_id="e6", event_type=event_type, timestamp="2026-07-24T09:15:00+05:30",
            source="FYERS", payload={}, sequence=0,
        )
        assert engine.translate_event(event) is None

    def test_engine_never_reimplements_id_minting(self):
        text = Path(engine.__file__).read_text()
        assert "hashlib" not in text


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_event_translates_to_same_observation_id(self):
        event = _tick_event("e1", 0, "2026-07-24T09:15:00+05:30", "NSE:SBIN-EQ", 500.0)
        obs1 = engine.translate_event(event)
        obs2 = engine.translate_event(event)
        assert obs1.identity.observation_id == obs2.identity.observation_id

    def test_serialization_round_trip_event(self):
        event = _tick_event("e1", 0, "2026-07-24T09:15:00+05:30", "NSE:SBIN-EQ", 500.0)
        text = serialization.event_to_json(event)
        restored = serialization.event_from_json(text)
        assert restored == event

    def test_serialization_deterministic_across_calls(self):
        event = _tick_event("e1", 0, "2026-07-24T09:15:00+05:30", "NSE:SBIN-EQ", 500.0)
        assert serialization.event_to_json(event) == serialization.event_to_json(event)

    def test_producer_state_round_trip(self):
        state = engine.new_producer_state("prod-1")
        state = engine.apply_transition(state, taxonomy.STATE_CONNECTING, "t1", "start")
        text = serialization.producer_state_to_json(state)
        restored = serialization.producer_state_from_json(text)
        assert restored == state


# ---------------------------------------------------------------------------
# Aggregation window ordering
# ---------------------------------------------------------------------------
class TestAggregationWindow:
    def test_ticks_accumulate_in_order(self):
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        t1 = models.Tick(timestamp="2026-07-24T09:15:01", price=100.0)
        t2 = models.Tick(timestamp="2026-07-24T09:15:02", price=101.0)
        window = engine.add_tick(window, t1)
        window = engine.add_tick(window, t2)
        assert [t.price for t in window.ticks] == [100.0, 101.0]

    def test_window_closes_at_boundary(self):
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        assert engine.should_close(window, "2026-07-24T09:15:30") is False
        assert engine.should_close(window, "2026-07-24T09:16:00") is True

    def test_close_window_produces_single_ohlc_observation(self):
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        for price in (100.0, 101.0, 99.5, 102.0):
            window = engine.add_tick(window, models.Tick(timestamp="2026-07-24T09:15:30", price=price))
        closed, obs = engine.close_window(window, source="FYERS")
        assert closed.is_closed is True
        assert obs.value.value_kind == moc_taxonomy.VALUE_KIND_OHLC
        assert obs.value.payload == {"open": 100.0, "high": 102.0, "low": 99.5, "close": 102.0}

    def test_close_window_with_no_ticks_produces_no_observation(self):
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        closed, obs = engine.close_window(window, source="FYERS")
        assert closed.is_closed is True
        assert obs is None

    def test_out_of_window_order_tick_is_never_dropped(self):
        """A tick timestamped before window_start is recorded as a
        late_tick, never silently discarded -- consistent with 73B's
        'no silent data gaps' precedent."""
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        early = models.Tick(timestamp="2026-07-24T09:14:59", price=99.0)
        window = engine.add_tick(window, early)
        assert len(window.ticks) == 0
        assert len(window.late_ticks) == 1
        assert window.late_ticks[0].reason == taxonomy.LATE_TICK_REASON_BEFORE_WINDOW_START
        assert window.late_ticks[0].tick == early

    def test_cannot_add_tick_to_closed_window(self):
        window = engine.new_window("SBIN", taxonomy.INTERVAL_ONE_MINUTE, "2026-07-24T09:15:00", "2026-07-24T09:16:00")
        closed, _ = engine.close_window(window, source="FYERS")
        with pytest.raises(ValueError):
            engine.add_tick(closed, models.Tick(timestamp="2026-07-24T09:15:30", price=100.0))


# ---------------------------------------------------------------------------
# Lifecycle transitions
# ---------------------------------------------------------------------------
class TestLifecycleTransitions:
    @pytest.mark.parametrize("current,nxt", [
        (taxonomy.STATE_CREATED, taxonomy.STATE_CONNECTING),
        (taxonomy.STATE_CONNECTING, taxonomy.STATE_CONNECTED),
        (taxonomy.STATE_CONNECTED, taxonomy.STATE_STREAMING),
        (taxonomy.STATE_STREAMING, taxonomy.STATE_RECONNECTING),
        (taxonomy.STATE_RECONNECTING, taxonomy.STATE_STREAMING),
        (taxonomy.STATE_STREAMING, taxonomy.STATE_STOPPED),
        (taxonomy.STATE_CONNECTING, taxonomy.STATE_FAILED),
    ])
    def test_valid_transitions_succeed(self, current, nxt):
        assert engine.is_valid_transition(current, nxt) is True

    @pytest.mark.parametrize("current,nxt", [
        (taxonomy.STATE_STOPPED, taxonomy.STATE_STREAMING),
        (taxonomy.STATE_FAILED, taxonomy.STATE_CONNECTING),
        (taxonomy.STATE_CREATED, taxonomy.STATE_STREAMING),
        (taxonomy.STATE_CONNECTED, taxonomy.STATE_CREATED),
    ])
    def test_invalid_transitions_rejected(self, current, nxt):
        assert engine.is_valid_transition(current, nxt) is False

    def test_apply_transition_records_history(self):
        state = engine.new_producer_state("prod-1")
        state = engine.apply_transition(state, taxonomy.STATE_CONNECTING, "t1", "start")
        state = engine.apply_transition(state, taxonomy.STATE_CONNECTED, "t2", "handshake ok")
        assert state.current_state == taxonomy.STATE_CONNECTED
        assert [t.to_state for t in state.history] == [taxonomy.STATE_CONNECTING, taxonomy.STATE_CONNECTED]

    def test_apply_invalid_transition_raises(self):
        state = engine.new_producer_state("prod-1")
        with pytest.raises(ValueError):
            engine.apply_transition(state, taxonomy.STATE_STREAMING, "t1", "skip ahead")

    def test_stopped_to_streaming_is_invalid(self):
        assert engine.is_valid_transition(taxonomy.STATE_STOPPED, taxonomy.STATE_STREAMING) is False


# ---------------------------------------------------------------------------
# Deliverable 10 — end-to-end demonstration
# ---------------------------------------------------------------------------
class TestEndToEndDemonstration:
    def test_tick_tick_tick_to_one_minute_observation(self):
        events = [
            _tick_event("e0", 0, "2026-07-24T09:15:00+05:30", "NSE:SBIN-EQ", 100.0),
            _tick_event("e1", 1, "2026-07-24T09:15:20+05:30", "NSE:SBIN-EQ", 101.0),
            _tick_event("e2", 2, "2026-07-24T09:15:40+05:30", "NSE:SBIN-EQ", 99.5),
            _tick_event("e3", 3, "2026-07-24T09:15:59+05:30", "NSE:SBIN-EQ", 102.0),
            # Crosses the one-minute boundary -> closes the first window.
            _tick_event("e4", 4, "2026-07-24T09:16:05+05:30", "NSE:SBIN-EQ", 103.0),
        ]
        pipeline = runner.LiveObservationPipeline()
        producer = runner.SyntheticEventProducer(events, pipeline)

        results = producer.run()
        pipeline.close_window("NSE:SBIN-EQ")  # Force-close the trailing partial window.

        # Every tick individually translated to a PRICE Observation via
        # the reused MOC builder.
        assert len(results) == 5
        assert all(isinstance(r, Observation) for r in results)
        assert all(r.identity.observation_type == moc_taxonomy.TYPE_PRICE for r in results)

        # Exactly one fully-closed 1-minute window (09:15:00-09:16:00)
        # produced a single OHLC Observation from the first four ticks.
        assert len(pipeline.closed_window_observations) == 2
        first_candle = pipeline.closed_window_observations[0]
        assert first_candle.value.value_kind == moc_taxonomy.VALUE_KIND_OHLC
        assert first_candle.value.payload == {"open": 100.0, "high": 102.0, "low": 99.5, "close": 102.0}

        # Delivered ("published") to the in-memory recorder stub: 5 tick
        # observations + 2 candle observations.
        assert len(pipeline.observation_recorder.recorded) == 7
        assert first_candle in pipeline.observation_recorder.recorded

    def test_demonstration_uses_no_wall_clock(self):
        """The demonstration path must never call time.sleep or read the
        wall clock -- every timestamp originates from the synthetic
        event data itself."""
        source = Path(runner.__file__).read_text()
        assert "time.sleep(" not in source


# ---------------------------------------------------------------------------
# AST isolation firewall
# ---------------------------------------------------------------------------
def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


class TestIsolation:
    def test_no_mic_v2_import_anywhere(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "mic_v2" not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "mic_v2" not in node.module

    def test_no_forbidden_bujji_module_imports(self):
        forbidden = (
            "bujji.mic_replay",
            "bujji.production_runtime",
            "bujji.trading_brain",
            "bujji.strategy_selector",
        )
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        for f in forbidden:
                            assert f not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    for f in forbidden:
                        assert f not in node.module

    def test_no_fyers_sdk_import_anywhere(self):
        """Confirmed live in Step 0: the real FYERS SDK is imported as
        `fyers_apiv3` (see bujji/broker/fyers_ws.py:
        `from fyers_apiv3.FyersWebsocket import data_ws`). The Producer
        Interface in this package must remain broker-neutral and never
        import it directly."""
        forbidden_module = "fyers_apiv3"
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert forbidden_module not in alias.name
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert forbidden_module not in node.module

    def test_no_uuid4_used_anywhere_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")
                if isinstance(node, ast.Name) and node.id == "uuid4":
                    raise AssertionError(f"uuid4 used in {path.name}")

    def test_no_unseeded_randomness(self):
        forbidden_calls = {"random", "randint", "choice", "uniform", "shuffle"}
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
                    raise AssertionError(f"unseeded randomness ({node.attr}) used in {path.name}")

    def test_no_time_sleep_in_package(self):
        for path in _module_source_files():
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute) and node.attr == "sleep":
                    raise AssertionError(f"time.sleep-shaped call used in {path.name}")
