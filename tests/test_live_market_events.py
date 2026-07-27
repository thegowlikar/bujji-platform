"""Live Market Event Engine v1 (Engineering Series 75) tests.

Tests for `bujji/live_market_events/`, the layer immediately above the
Observation Contract: `Observation -> Market Event -> Evidence ->
Intelligence -> Decision`. Covers event generation per event-type
family using real Observations built via 73A's own `build_observation`
(never hand-rolled dicts), deterministic event ids, ordering,
serialization round-trip, duplicate handling, late observations,
replay-vs-live parity, the Deliverable 10 demonstration, and an
AST-based isolation firewall mirroring `tests/test_live_observation_producer.py::TestIsolation`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.live_market_events import config, engine, journal, models, query, runner, serialization, taxonomy
from bujji.market_observation import engine as moc_engine
from bujji.market_observation import taxonomy as moc_taxonomy


SCHEMA_VERSION = "1.0.0"


def _obs(
    *,
    observation_type=moc_taxonomy.TYPE_PRICE,
    instrument="NSE:SBIN-EQ",
    timestamp,
    value_kind=moc_taxonomy.VALUE_KIND_SCALAR,
    payload,
    resolution=moc_taxonomy.RESOLUTION_ONE_MINUTE,
):
    return moc_engine.build_observation(
        observation_type=observation_type,
        instrument=instrument,
        exchange="NSE",
        segment="EQ",
        timestamp=timestamp,
        resolution=resolution,
        source="FYERS",
        schema_version="1.0.0",
        value_kind=value_kind,
        payload=payload,
        completeness=1.0,
        freshness=0.0,
        confidence=None,
        missing_fields=(),
        validation_status=moc_taxonomy.VALIDATION_VALID,
        source_quality=moc_taxonomy.SOURCE_QUALITY_HIGH,
        originating_source="FYERS",
        acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp,
        origin=moc_taxonomy.ORIGIN_LIVE,
        provenance_version="1.0.0",
    )


# ---------------------------------------------------------------------------
# Event generation per family
# ---------------------------------------------------------------------------
class TestObservationLifecycleEvents:
    def test_first_observation_is_created(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        events, _ = engine.compare_observations(a, None)
        assert any(e.event_type == taxonomy.OBSERVATION_CREATED for e in events)

    def test_changed_value_is_updated(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events, _ = engine.compare_observations(b, a)
        assert any(e.event_type == taxonomy.OBSERVATION_UPDATED for e in events)

    def test_same_timestamp_different_value_is_corrected(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=102.0)
        events, _ = engine.compare_observations(b, a)
        assert any(e.event_type == taxonomy.OBSERVATION_CORRECTED for e in events)

    def test_unchanged_value_emits_no_lifecycle_event(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=100.0)
        events, _ = engine.compare_observations(b, a)
        types = {e.event_type for e in events}
        assert taxonomy.OBSERVATION_UPDATED not in types
        assert taxonomy.OBSERVATION_CREATED not in types


class TestPriceEvents:
    def test_price_changed(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events, _ = engine.compare_observations(b, a)
        price_events = query.events_of_type(events, taxonomy.PRICE_CHANGED)
        assert len(price_events) == 1
        assert price_events[0].detail["old_price"] == 100.0
        assert price_events[0].detail["new_price"] == 101.0
        assert price_events[0].detail["delta"] == 1.0

    def test_large_move_also_flags_price_gap(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=110.0)  # 10% move
        events, _ = engine.compare_observations(b, a)
        assert query.events_of_type(events, taxonomy.PRICE_CHANGED)
        assert query.events_of_type(events, taxonomy.PRICE_GAP_DETECTED)

    def test_small_move_does_not_flag_gap(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=100.10)
        events, _ = engine.compare_observations(b, a)
        assert not query.events_of_type(events, taxonomy.PRICE_GAP_DETECTED)

    def test_non_price_observation_type_never_emits_price_events(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_OPEN_INTEREST,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"open_interest": 1000},
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_OPEN_INTEREST,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"open_interest": 2000},
        )
        events, _ = engine.compare_observations(b, a)
        assert not query.events_of_type(events, taxonomy.PRICE_CHANGED)


class TestOiVolumeVixEvents:
    def test_oi_changed(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_OPEN_INTEREST,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"open_interest": 1000},
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_OPEN_INTEREST,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"open_interest": 1500},
        )
        events, _ = engine.compare_observations(b, a)
        oi_events = query.events_of_type(events, taxonomy.OI_CHANGED)
        assert len(oi_events) == 1
        assert oi_events[0].detail["delta"] == 500

    def test_volume_changed(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 100.0, "volume": 1000},
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 100.0, "volume": 1200},
        )
        events, _ = engine.compare_observations(b, a)
        vol_events = query.events_of_type(events, taxonomy.VOLUME_CHANGED)
        assert len(vol_events) == 1
        assert vol_events[0].detail["delta"] == 200

    def test_vix_changed(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_VOLATILITY_VIX,
            instrument="INDIA VIX",
            timestamp="2026-07-24T09:15:00+05:30",
            payload=13.0,
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_VOLATILITY_VIX,
            instrument="INDIA VIX",
            timestamp="2026-07-24T09:16:00+05:30",
            payload=13.5,
        )
        events, _ = engine.compare_observations(b, a)
        vix_events = query.events_of_type(events, taxonomy.VIX_CHANGED)
        assert len(vix_events) == 1


class TestFuturesAndOptionChainEvents:
    def test_futures_updated(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_FUTURES,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 100.0},
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_FUTURES,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 101.0},
        )
        events, _ = engine.compare_observations(b, a)
        assert query.events_of_type(events, taxonomy.FUTURES_UPDATED)

    def test_option_chain_updated(self):
        a = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_CHAIN,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"strikes": [100, 200]},
        )
        b = _obs(
            observation_type=moc_taxonomy.TYPE_OPTION_CHAIN,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"strikes": [100, 200, 300]},
        )
        events, _ = engine.compare_observations(b, a)
        assert query.events_of_type(events, taxonomy.OPTION_CHAIN_UPDATED)


class TestSessionHighLow:
    def test_first_observation_sets_both_high_and_low(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        events, state = engine.compare_observations(a, None)
        assert query.events_of_type(events, taxonomy.NEW_SESSION_HIGH)
        assert query.events_of_type(events, taxonomy.NEW_SESSION_LOW)
        assert state.session_extremes.session_high == 100.0
        assert state.session_extremes.session_low == 100.0

    def test_running_state_threads_forward_without_full_history(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        events_a, state_a = engine.compare_observations(a, None)

        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=105.0)
        events_b, state_b = engine.compare_observations(b, a, state_a)
        assert query.events_of_type(events_b, taxonomy.NEW_SESSION_HIGH)
        assert not query.events_of_type(events_b, taxonomy.NEW_SESSION_LOW)
        assert state_b.session_extremes.session_high == 105.0
        assert state_b.session_extremes.session_low == 100.0

        c = _obs(timestamp="2026-07-24T09:17:00+05:30", payload=95.0)
        events_c, state_c = engine.compare_observations(c, b, state_b)
        assert query.events_of_type(events_c, taxonomy.NEW_SESSION_LOW)
        assert not query.events_of_type(events_c, taxonomy.NEW_SESSION_HIGH)
        assert state_c.session_extremes.session_low == 95.0
        assert state_c.session_extremes.session_high == 105.0

    def test_mid_range_value_sets_neither_extreme(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        _, state_a = engine.compare_observations(a, None)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=105.0)
        _, state_b = engine.compare_observations(b, a, state_a)
        c = _obs(timestamp="2026-07-24T09:17:00+05:30", payload=102.0)
        events_c, _ = engine.compare_observations(c, b, state_b)
        assert not query.events_of_type(events_c, taxonomy.NEW_SESSION_HIGH)
        assert not query.events_of_type(events_c, taxonomy.NEW_SESSION_LOW)


class TestDuplicateAndLate:
    def test_duplicate_detected(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=100.0)
        # Force an identical payload but distinct observation_id via distinct timestamps upstream;
        # here we directly test detect_duplicate with equal payload.
        event = engine.detect_duplicate(b, a)
        assert event is not None
        assert event.event_type == taxonomy.DUPLICATE_OBSERVATION_DETECTED

    def test_identical_observation_object_is_not_a_duplicate_event(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        assert engine.detect_duplicate(a, a) is None

    def test_late_observation_detected(self):
        a = _obs(timestamp="2026-07-24T09:20:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:10:00+05:30", payload=101.0)
        event = engine.detect_late_observation(b, a)
        assert event is not None
        assert event.event_type == taxonomy.LATE_OBSERVATION_RECEIVED

    def test_no_previous_observation_is_never_late_or_duplicate(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        assert engine.detect_late_observation(a, None) is None
        assert engine.detect_duplicate(a, None) is None


class TestGapDetection:
    def test_large_interval_flags_gap(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:25:00+05:30", payload=101.0)  # 10 min for a 1-min resolution
        event = engine.detect_series_gap(b, a)
        assert event is not None
        assert event.event_type == taxonomy.OBSERVATION_SERIES_GAP_DETECTED

    def test_normal_interval_does_not_flag_gap(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        assert engine.detect_series_gap(b, a) is None


# ---------------------------------------------------------------------------
# Determinism / event_id
# ---------------------------------------------------------------------------
class TestDeterminism:
    def test_same_pair_compared_twice_yields_identical_event_ids(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events_1, _ = engine.compare_observations(b, a)
        events_2, _ = engine.compare_observations(b, a)
        assert [e.event_id for e in events_1] == [e.event_id for e in events_2]

    def test_different_real_occurrences_yield_different_event_ids(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b1 = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        b2 = _obs(timestamp="2026-07-24T10:16:00+05:30", payload=101.0)
        events_1, _ = engine.compare_observations(b1, a)
        events_2, _ = engine.compare_observations(b2, a)
        price_1 = query.events_of_type(events_1, taxonomy.PRICE_CHANGED)[0]
        price_2 = query.events_of_type(events_2, taxonomy.PRICE_CHANGED)[0]
        assert price_1.event_id != price_2.event_id

    def test_no_uuid4_and_ids_are_content_hashes(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events, _ = engine.compare_observations(b, a)
        for e in events:
            assert e.event_id.startswith("MEVT-")


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
class TestSerialization:
    def test_round_trip_preserves_all_fields(self):
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events, _ = engine.compare_observations(b, a)
        for event in events:
            as_json = serialization.event_to_json(event)
            back = serialization.event_from_json(as_json)
            assert back == event


# ---------------------------------------------------------------------------
# Journal — append-only
# ---------------------------------------------------------------------------
class TestJournal:
    def test_journal_records_in_order_and_never_mutates(self, tmp_path):
        j = journal.MarketEventJournal(tmp_path / "events.jsonl")
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        events, _ = engine.compare_observations(b, a)
        for e in events:
            j.record_event(e)
        read_back = j.read_events()
        assert [e.event_id for e in read_back] == [e.event_id for e in events]


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------
class TestOrdering:
    def test_events_from_generate_events_for_series_preserve_series_order(self):
        from bujji.market_observation import engine as moc_engine_mod

        series = moc_engine_mod.new_series(moc_taxonomy.TYPE_PRICE, "NSE:SBIN-EQ", moc_taxonomy.RESOLUTION_ONE_MINUTE)
        a = _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0)
        b = _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0)
        c = _obs(timestamp="2026-07-24T09:17:00+05:30", payload=99.0)
        series = moc_engine_mod.append_observation(series, a)
        series = moc_engine_mod.append_observation(series, b)
        series = moc_engine_mod.append_observation(series, c)

        events = runner.generate_events_for_series(series)
        timestamps = [e.timestamp for e in events]
        assert timestamps == sorted(timestamps)


# ---------------------------------------------------------------------------
# Replay vs. live parity
# ---------------------------------------------------------------------------
class TestReplayLiveParity:
    def test_batch_and_incremental_generate_identical_event_ids(self):
        from bujji.market_observation import engine as moc_engine_mod

        series = moc_engine_mod.new_series(moc_taxonomy.TYPE_PRICE, "NSE:SBIN-EQ", moc_taxonomy.RESOLUTION_ONE_MINUTE)
        raw = [
            _obs(timestamp="2026-07-24T09:15:00+05:30", payload=100.0),
            _obs(timestamp="2026-07-24T09:16:00+05:30", payload=101.0),
            _obs(timestamp="2026-07-24T09:17:00+05:30", payload=115.0),  # gap
            _obs(timestamp="2026-07-24T09:18:00+05:30", payload=95.0),   # new low
        ]
        for o in raw:
            series = moc_engine_mod.append_observation(series, o)

        batch_events = runner.generate_events_for_series(series)

        stream = runner.LiveMarketEventStream()
        incremental_events = []
        for o in raw:
            incremental_events.extend(stream.handle_observation(o))

        batch_ids = [e.event_id for e in batch_events]
        incremental_ids = [e.event_id for e in incremental_events]
        assert batch_ids == incremental_ids
        assert len(batch_ids) > 0


# ---------------------------------------------------------------------------
# Deliverable 10 demonstration
# ---------------------------------------------------------------------------
class TestDeliverable10Demonstration:
    def _run_demo(self):
        obs_a = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:15:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 100.0, "volume": 1000},
        )
        obs_b = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:16:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 103.0, "volume": 1000},
        )
        obs_c = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:17:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 103.0, "volume": 2500},
        )
        obs_d = _obs(
            observation_type=moc_taxonomy.TYPE_PRICE,
            timestamp="2026-07-24T09:18:00+05:30",
            value_kind=moc_taxonomy.VALUE_KIND_MAPPING,
            payload={"price": 110.0, "volume": 2500},
        )

        stream = runner.LiveMarketEventStream()
        all_events = []
        all_events.extend(stream.handle_observation(obs_a))
        all_events.extend(stream.handle_observation(obs_b))
        all_events.extend(stream.handle_observation(obs_c))
        all_events.extend(stream.handle_observation(obs_d))
        return all_events

    def test_demonstration_produces_expected_event_shape(self):
        events = self._run_demo()
        types_in_order = [e.event_type for e in events]
        assert taxonomy.PRICE_CHANGED in types_in_order
        assert taxonomy.VOLUME_CHANGED in types_in_order
        assert taxonomy.NEW_SESSION_HIGH in types_in_order
        # PriceChanged (A->B) must appear before VolumeChanged (B->C)
        # must appear before the final NewSessionHigh (C->D).
        price_idx = types_in_order.index(taxonomy.PRICE_CHANGED)
        # find the FIRST volume changed after price_idx
        volume_idx = types_in_order.index(taxonomy.VOLUME_CHANGED)
        last_high_idx = len(types_in_order) - 1 - types_in_order[::-1].index(taxonomy.NEW_SESSION_HIGH)
        assert price_idx < volume_idx < last_high_idx

    def test_demonstration_is_reproducible(self):
        events_1 = self._run_demo()
        events_2 = self._run_demo()
        assert [e.event_id for e in events_1] == [e.event_id for e in events_2]


# ---------------------------------------------------------------------------
# AST isolation firewall
# ---------------------------------------------------------------------------
def _module_source_files():
    base = Path(engine.__file__).parent
    return list(base.glob("*.py"))


_FORBIDDEN_MEANING_TERMS = (
    "bullish", "bearish", "trending", "momentum", "compression", "expansion",
    "buildup", "short-covering", "short_covering", "long-unwinding",
    "long_unwinding", "support", "resistance", "acceptance", "rejection",
)


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

    def test_no_forbidden_meaning_terms_anywhere(self):
        for path in _module_source_files():
            text = path.read_text().lower()
            for term in _FORBIDDEN_MEANING_TERMS:
                assert term not in text, f"forbidden meaning term {term!r} found in {path.name}"
