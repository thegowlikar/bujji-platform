"""Futures Observation Domain v1 (Engineering Series 73B) tests.

Tests for `bujji/futures_observation/`, the first concrete domain built
on top of the Market Observation Contract (`bujji/market_observation/`,
Series 73A). Covers deterministic identity, real-Bhavcopy-data
ingestion, serialization round-trip, validation (including a genuinely
malformed row), series ordering, duplicate handling, replay
compatibility, and an AST-based isolation firewall mirroring
`tests/test_market_observation_contract.py::TestIsolation` (with
`bujji.strategy_selector` added to the forbidden import list, per this
sprint's own broadened isolation scope).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.futures_observation import config, engine, journal, models, query, runner, serialization, taxonomy
from bujji.market_observation import taxonomy as moc_taxonomy

REAL_BHAVCOPY_DIR = Path("/tmp/m1")
REAL_BHAVCOPY_FILES = sorted(REAL_BHAVCOPY_DIR.glob("BhavCopy_NSE_FO_*.csv"))


def _real_csv_text(index: int = 0) -> str:
    return REAL_BHAVCOPY_FILES[index].read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(
    underlying="ABCAPITAL",
    instrument_symbol="ABCAPITAL26MAYFUT",
    expiry="2026-05-26",
    timestamp="2026-05-25T15:30:00",
    open_=359.95,
    high=365.70,
    low=359.95,
    close=364.35,
    volume=4940,
    open_interest=7582600,
    change_in_open_interest=-12598400,
    settlement_price=364.35,
    underlying_price=363.65,
):
    return engine.build_futures_observation(
        underlying=underlying,
        instrument_symbol=instrument_symbol,
        expiry=expiry,
        exchange="NSE",
        segment="FO",
        timestamp=timestamp,
        resolution=moc_taxonomy.RESOLUTION_DAILY,
        open_=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        open_interest=open_interest,
        change_in_open_interest=change_in_open_interest,
        settlement_price=settlement_price,
        underlying_price=underlying_price,
        origin=moc_taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION,
        acquisition_timestamp=timestamp,
        normalization_timestamp=timestamp,
    )


# ---------------------------------------------------------------------------
# Identity determinism
# ---------------------------------------------------------------------------
class TestIdentityDeterminism:
    def test_same_inputs_same_observation_id(self):
        a = _build()
        b = _build()
        assert a.observation_id == b.observation_id

    def test_different_close_different_observation_id(self):
        a = _build(close=364.35)
        b = _build(close=364.40)
        assert a.observation_id != b.observation_id

    def test_same_csv_row_same_observation_id_every_run(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        row = rows[0]
        fo1 = runner.build_futures_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        fo2 = runner.build_futures_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        assert fo1.observation_id == fo2.observation_id
        assert fo1.observation_id.startswith("OBS-")

    def test_observation_id_never_uses_uuid(self):
        fo = _build()
        # md5-derived ids are 24 hex chars after the OBS- prefix (per
        # MOC's own _observation_id convention) -- a uuid4 hex digest
        # would be 32 chars and dash-formatted differently in repr.
        assert len(fo.observation_id) == len("OBS-") + 24


# ---------------------------------------------------------------------------
# Real Bhavcopy ingestion
# ---------------------------------------------------------------------------
class TestRealBhavcopyIngestion:
    def test_real_files_present(self):
        assert len(REAL_BHAVCOPY_FILES) >= 3, "expected at least 3 real Bhavcopy files at /tmp/m1"

    @pytest.mark.parametrize("file_index", [0, 1, 2])
    def test_futures_rows_extracted_nonzero(self, file_index):
        csv_text = _real_csv_text(file_index)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        assert len(rows) > 0
        for row in rows:
            assert row["FinInstrmTp"] in taxonomy.ALL_FUTURES_INSTRUMENT_TYPES

    def test_stock_futures_instrument_type_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        assert len(rows) > 0
        assert all(r["FinInstrmTp"] == taxonomy.FUTURES_INSTRUMENT_TYPE_STOCK for r in rows)

    def test_index_futures_instrument_type_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        assert len(rows) > 0
        assert all(r["FinInstrmTp"] == taxonomy.FUTURES_INSTRUMENT_TYPE_INDEX for r in rows)

    def test_ingest_single_series_from_real_file(self):
        csv_text = _real_csv_text(0)
        series, matched = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-25", underlying="BANKNIFTY"
        )
        assert matched > 0
        assert series is not None
        assert len(series) == 1
        obs = series.observations()[0]
        assert obs.close is not None
        assert obs.open_interest is not None
        assert obs.underlying == "BANKNIFTY"

    def test_ingest_all_expiries_from_real_file(self):
        csv_text = _real_csv_text(0)
        all_series, matched = runner.ingest_all_futures_series_from_bhavcopy(
            csv_text, "2026-05-25", underlying="BANKNIFTY"
        )
        assert matched == 3
        assert len(all_series) == 3
        expiries = {s.expiry for s in all_series}
        assert len(expiries) == 3

    def test_basis_computed_from_real_row_when_underlying_price_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        row = rows[0]
        underlying_price = float(row["UndrlygPric"])
        settlement_price = float(row["SttlmPric"])
        series, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-25", underlying="BANKNIFTY"
        )
        obs = series.observations()[0]
        assert obs.basis is not None
        assert obs.basis == pytest.approx(settlement_price - underlying_price)

    def test_no_row_fabricates_a_zero_field(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_futures_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        for row in rows:
            fo = runner.build_futures_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
            assert fo.close != 0 or row.get("ClsPric") in ("0", "0.00")


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
class TestSerializationRoundTrip:
    def test_observation_round_trip(self):
        fo = _build()
        text = serialization.futures_observation_to_json(fo)
        restored = serialization.futures_observation_from_json(text)
        assert restored == fo

    def test_series_round_trip_from_real_data(self):
        csv_text = _real_csv_text(0)
        series, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-25", underlying="BANKNIFTY"
        )
        text = serialization.futures_series_to_json(series)
        restored = serialization.futures_series_from_dict(serialization.json.loads(text))
        assert restored == series

    def test_round_trip_is_deterministic(self):
        fo = _build()
        text_a = serialization.futures_observation_to_json(fo)
        text_b = serialization.futures_observation_to_json(fo)
        assert text_a == text_b


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_valid_observation_passes(self):
        fo = _build()
        result = engine.validate_futures_observation(fo)
        assert result.is_valid
        assert result.reasons == ()

    def test_missing_mandatory_fields_flagged_not_dropped(self):
        # Genuinely malformed/incomplete row: no OHLC/volume/OI at all.
        fo = _build(
            open_=None,
            high=None,
            low=None,
            close=None,
            volume=None,
            open_interest=None,
            change_in_open_interest=None,
            settlement_price=None,
        )
        assert fo is not None  # never silently dropped -- still constructed
        assert set(fo.missing_fields) == set(taxonomy.MANDATORY_FUTURES_OBSERVATION_FIELDS)
        assert fo.observation.quality.validation_status == moc_taxonomy.VALIDATION_INCOMPLETE
        result = engine.validate_futures_observation(fo)
        # Structural validation itself (identity/timestamp/schema
        # checks) still passes -- MOC never validates market values,
        # missing_fields is the disclosed-gap channel, not a hard
        # validation failure.
        assert result.is_valid

    def test_missing_expiry_fails_validation(self):
        fo = _build(expiry="")
        result = engine.validate_futures_observation(fo)
        assert not result.is_valid
        assert "MISSING_EXPIRY" in result.reasons

    def test_malformed_row_missing_identity_returns_none(self):
        bad_row = {"TckrSymb": "", "XpryDt": "", "FinInstrmNm": ""}
        fo = runner.build_futures_observation_from_row(bad_row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        assert fo is None

    def test_never_validates_market_value_plausibility(self):
        # A wildly implausible OI is still structurally valid -- MOC
        # never judges whether a market value is "reasonable".
        fo = _build(open_interest=999_999_999_999)
        result = engine.validate_futures_observation(fo)
        assert result.is_valid


# ---------------------------------------------------------------------------
# Series ordering
# ---------------------------------------------------------------------------
class TestSeriesOrdering:
    def test_append_preserves_order(self):
        series = engine.new_futures_series(
            underlying="ABCAPITAL", expiry="2026-05-26", instrument_symbol="ABCAPITAL26MAYFUT",
            resolution=moc_taxonomy.RESOLUTION_DAILY,
        )
        fo1 = _build(timestamp="2026-05-25T15:30:00")
        fo2 = _build(timestamp="2026-05-26T15:30:00", close=370.0)
        series = engine.append_futures_observation(series, fo1)
        series = engine.append_futures_observation(series, fo2)
        assert len(series) == 2
        timestamps = [o.timestamp for o in series.observations()]
        assert timestamps == sorted(timestamps)

    def test_out_of_order_append_recorded_as_gap_not_silently_dropped(self):
        series = engine.new_futures_series(
            underlying="ABCAPITAL", expiry="2026-05-26", instrument_symbol="ABCAPITAL26MAYFUT",
            resolution=moc_taxonomy.RESOLUTION_DAILY,
        )
        fo_later = _build(timestamp="2026-05-26T15:30:00")
        fo_earlier = _build(timestamp="2026-05-25T15:30:00", close=370.0)
        series = engine.append_futures_observation(series, fo_later)
        series = engine.append_futures_observation(series, fo_earlier)
        assert len(series) == 2  # still appended, never dropped
        assert len(series.series.gaps) == 1
        assert series.series.gaps[0].reason == moc_taxonomy.GAP_REASON_OUT_OF_ORDER_SKIPPED


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------
class TestDuplicateHandling:
    def test_duplicate_row_produces_duplicate_observation_id(self):
        fo1 = _build()
        fo2 = _build()
        assert fo1.observation_id == fo2.observation_id  # same content -> same id, detectable as duplicate

    def test_journal_can_record_duplicate_detection(self, tmp_path):
        j = journal.FuturesObservationJournal(tmp_path / "futures_journal.jsonl")
        fo = _build()
        j.record_observation(fo)
        j.record_duplicate(fo.observation_id)
        records = j.read_all()
        assert any(isinstance(r, dict) and r.get("kind") == "DUPLICATE_DETECTED" for r in records)


# ---------------------------------------------------------------------------
# Replay compatibility
# ---------------------------------------------------------------------------
class TestReplayCompatibility:
    def test_same_file_ingested_twice_byte_identical_series(self):
        csv_text = _real_csv_text(1)
        series_a, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-26", underlying="NIFTY"
        )
        series_b, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-26", underlying="NIFTY"
        )
        text_a = serialization.futures_series_to_json(series_a)
        text_b = serialization.futures_series_to_json(series_b)
        assert text_a == text_b

    def test_fingerprint_stable_across_two_real_files_same_shape(self):
        # Two different trading days -> different content -> different
        # fingerprint (sanity check that the fingerprint actually
        # reflects content, not a constant).
        csv_text_1 = _real_csv_text(0)
        csv_text_2 = _real_csv_text(1)
        series_1, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text_1, "2026-05-25", underlying="BANKNIFTY"
        )
        series_2, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text_2, "2026-05-26", underlying="BANKNIFTY"
        )
        assert serialization.futures_series_to_json(series_1) != serialization.futures_series_to_json(series_2)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
class TestQuery:
    def test_by_underlying_and_by_instrument_symbol(self):
        csv_text = _real_csv_text(0)
        series, _ = runner.ingest_futures_observations_from_bhavcopy(
            csv_text, "2026-05-25", underlying="BANKNIFTY"
        )
        assert len(query.by_underlying(series, "BANKNIFTY")) == 1
        assert len(query.by_underlying(series, "NIFTY")) == 0
        symbol = series.instrument_symbol
        assert len(query.by_instrument_symbol(series, symbol)) == 1

    def test_latest_and_earliest(self):
        series = engine.new_futures_series(
            underlying="ABCAPITAL", expiry="2026-05-26", instrument_symbol="ABCAPITAL26MAYFUT",
            resolution=moc_taxonomy.RESOLUTION_DAILY,
        )
        fo1 = _build(timestamp="2026-05-25T15:30:00")
        fo2 = _build(timestamp="2026-05-26T15:30:00", close=370.0)
        series = engine.append_futures_observation(series, fo1)
        series = engine.append_futures_observation(series, fo2)
        assert query.earliest(series).timestamp == "2026-05-25T15:30:00"
        assert query.latest(series).timestamp == "2026-05-26T15:30:00"


# ---------------------------------------------------------------------------
# Zero-interpretation contract
# ---------------------------------------------------------------------------
class TestZeroInterpretation:
    FORBIDDEN_TERMS = (
        "long_buildup",
        "short_buildup",
        "short_covering",
        "long_unwinding",
        "bullish",
        "bearish",
        "trend",
    )

    def test_no_interpretive_identifiers_in_source(self):
        # Checks Python identifiers only (names/attributes) -- never
        # docstring prose, since this module's own docstrings
        # legitimately *name* these concepts only to disclaim them
        # ("this module never classifies trend/bullish/bearish...").
        base = Path(taxonomy.__file__).parent
        for path in base.glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                identifier = None
                if isinstance(node, ast.Name):
                    identifier = node.id
                elif isinstance(node, ast.Attribute):
                    identifier = node.attr
                if identifier is None:
                    continue
                lowered = identifier.lower()
                for term in self.FORBIDDEN_TERMS:
                    assert term not in lowered, f"forbidden interpretive identifier {identifier!r} found in {path.name}"


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

    def test_only_wraps_moc_never_reimplements_id_minting(self):
        # engine.py must call bujji.market_observation.engine.build_observation
        # to mint an id -- it must never construct an
        # ObservationIdentity with observation_id set from hashlib
        # itself (that would duplicate MOC's own id-minting authority).
        text = (Path(engine.__file__)).read_text()
        assert "hashlib" not in text
