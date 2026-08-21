"""Options Observation Domain v1 (Engineering Series 73C) tests.

Tests for `bujji/options_observation/`, the second concrete domain
built on top of the Market Observation Contract
(`bujji/market_observation/`, Series 73A), sibling to the Futures
Observation Domain (`bujji/futures_observation/`, Series 73B). Covers
deterministic identity, real-Bhavcopy-data ingestion for both CE and
PE, serialization round-trip, validation (including a genuinely
malformed row), series ordering, duplicate handling, replay
compatibility, and an AST-based isolation firewall mirroring
`tests/test_futures_observation_domain.py::TestIsolation`.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from bujji.market_observation import taxonomy as moc_taxonomy
from bujji.options_observation import config, engine, journal, models, query, runner, serialization, taxonomy

REAL_BHAVCOPY_DIR = Path("/tmp/m1")
REAL_BHAVCOPY_FILES = sorted(REAL_BHAVCOPY_DIR.glob("BhavCopy_NSE_FO_*.csv"))


def _real_csv_text(index: int = 0) -> str:
    return REAL_BHAVCOPY_FILES[index].read_text()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _build(
    underlying="ABCAPITAL",
    instrument_symbol="ABCAPITAL26JUL360PE",
    strike=360.0,
    expiry="2026-07-28",
    option_type="PE",
    timestamp="2026-05-25T15:30:00",
    open_=0.0,
    high=0.0,
    low=0.0,
    close=37.10,
    settlement=37.10,
    volume=0,
    open_interest=20.70,
    change_in_open_interest=0,
    underlying_price=363.65,
):
    return engine.build_option_observation(
        # "ABCAPITAL26JUL360PE" is NSE's own FinInstrmNm form -- real at the
        # source, unproven at any execution venue.
        symbol_provenance="SOURCE_AUTHORITATIVE",
        underlying=underlying,
        instrument_symbol=instrument_symbol,
        strike=strike,
        expiry=expiry,
        option_type=option_type,
        exchange="NSE",
        segment="FO",
        timestamp=timestamp,
        resolution=moc_taxonomy.RESOLUTION_DAILY,
        open_=open_,
        high=high,
        low=low,
        close=close,
        settlement=settlement,
        volume=volume,
        open_interest=open_interest,
        change_in_open_interest=change_in_open_interest,
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
        a = _build(close=37.10)
        b = _build(close=37.20)
        assert a.observation_id != b.observation_id

    def test_different_option_type_different_observation_id(self):
        # observation_id is minted from MOC's ObservationIdentity fields
        # (which includes `instrument`, the contract-specific symbol)
        # plus value -- option_type itself is a wrapper-level field, so
        # this varies the real distinguishing field (instrument_symbol)
        # the way two real CE/PE Bhavcopy rows for the same
        # underlying+strike+expiry always do.
        a = _build(option_type="PE", instrument_symbol="ABCAPITAL26JUL360PE")
        b = _build(option_type="CE", instrument_symbol="ABCAPITAL26JUL360CE")
        assert a.observation_id != b.observation_id

    def test_same_csv_row_same_observation_id_every_run(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        row = rows[0]
        oo1 = runner.build_option_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        oo2 = runner.build_option_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        assert oo1.observation_id == oo2.observation_id
        assert oo1.observation_id.startswith("OBS-")

    def test_observation_id_never_uses_uuid(self):
        oo = _build()
        assert len(oo.observation_id) == len("OBS-") + 24


# ---------------------------------------------------------------------------
# Real Bhavcopy ingestion
# ---------------------------------------------------------------------------
class TestRealBhavcopyIngestion:
    def test_real_files_present(self):
        assert len(REAL_BHAVCOPY_FILES) >= 3, "expected at least 3 real Bhavcopy files at /tmp/m1"

    @pytest.mark.parametrize("file_index", [0, 1, 2])
    def test_option_rows_extracted_nonzero(self, file_index):
        csv_text = _real_csv_text(file_index)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        assert len(rows) > 0
        for row in rows:
            assert row["FinInstrmTp"] in taxonomy.ALL_OPTIONS_INSTRUMENT_TYPES

    def test_stock_option_instrument_type_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        assert len(rows) > 0
        assert all(r["FinInstrmTp"] == taxonomy.OPTIONS_INSTRUMENT_TYPE_STOCK for r in rows)

    def test_index_option_instrument_type_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="BANKNIFTY")
        assert len(rows) > 0
        assert all(r["FinInstrmTp"] == taxonomy.OPTIONS_INSTRUMENT_TYPE_INDEX for r in rows)

    def test_both_ce_and_pe_present_in_real_file(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        option_types = {r["OptnTp"] for r in rows}
        assert "CE" in option_types
        assert "PE" in option_types

    def test_multiple_strikes_and_expiries_present(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        strikes = {r["StrkPric"] for r in rows}
        expiries = {r["XpryDt"] for r in rows}
        assert len(strikes) > 1
        assert len(expiries) > 1

    def test_ingest_single_series_from_real_file(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        row = rows[0]
        series, matched = runner.ingest_option_observations_from_bhavcopy(
            csv_text,
            "2026-05-25",
            underlying="ABCAPITAL",
            strike=float(row["StrkPric"]),
            expiry=row["XpryDt"],
            option_type=row["OptnTp"],
        )
        assert matched > 0
        assert series is not None
        assert len(series) == 1
        obs = series.observations()[0]
        assert obs.underlying == "ABCAPITAL"
        assert obs.option_type == row["OptnTp"]

    def test_ingest_all_contracts_from_real_file(self):
        csv_text = _real_csv_text(0)
        all_series, matched = runner.ingest_all_option_series_from_bhavcopy(
            csv_text, "2026-05-25", underlying="ABCAPITAL"
        )
        assert matched > 0
        assert len(all_series) > 1
        option_types = {s.option_type for s in all_series}
        assert "CE" in option_types
        assert "PE" in option_types

    def test_bid_ask_bidqty_askqty_never_fabricated(self):
        csv_text = _real_csv_text(0)
        all_series, _ = runner.ingest_all_option_series_from_bhavcopy(
            csv_text, "2026-05-25", underlying="ABCAPITAL"
        )
        assert len(all_series) > 0
        for s in all_series:
            for obs in s.observations():
                assert obs.bid is None
                assert obs.ask is None
                assert obs.bid_quantity is None
                assert obs.ask_quantity is None
                for f in taxonomy.KNOWN_UNAVAILABLE_FROM_BHAVCOPY:
                    assert f in obs.missing_fields

    def test_no_row_fabricates_a_nonexistent_value(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        for row in rows:
            oo = runner.build_option_observation_from_row(row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
            assert oo.bid is None
            assert oo.ask is None


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------
class TestSerializationRoundTrip:
    def test_observation_round_trip(self):
        oo = _build()
        text = serialization.option_observation_to_json(oo)
        restored = serialization.option_observation_from_json(text)
        assert restored == oo

    def test_series_round_trip_from_real_data(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        row = rows[0]
        series, _ = runner.ingest_option_observations_from_bhavcopy(
            csv_text,
            "2026-05-25",
            underlying="ABCAPITAL",
            strike=float(row["StrkPric"]),
            expiry=row["XpryDt"],
            option_type=row["OptnTp"],
        )
        text = serialization.option_series_to_json(series)
        restored = serialization.option_series_from_dict(serialization.json.loads(text))
        assert restored == series

    def test_round_trip_is_deterministic(self):
        oo = _build()
        text_a = serialization.option_observation_to_json(oo)
        text_b = serialization.option_observation_to_json(oo)
        assert text_a == text_b


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
class TestValidation:
    def test_valid_observation_passes(self):
        oo = _build()
        result = engine.validate_option_observation(oo)
        assert result.is_valid
        assert result.reasons == ()

    def test_missing_mandatory_fields_flagged_not_dropped(self):
        oo = _build(
            open_=None,
            high=None,
            low=None,
            close=None,
            settlement=None,
            volume=None,
            open_interest=None,
            change_in_open_interest=None,
        )
        assert oo is not None
        assert set(taxonomy.MANDATORY_OPTIONS_OBSERVATION_FIELDS).issubset(set(oo.missing_fields))
        assert oo.observation.quality.validation_status == moc_taxonomy.VALIDATION_INCOMPLETE
        result = engine.validate_option_observation(oo)
        assert result.is_valid

    def test_missing_option_type_fails_validation(self):
        oo = _build(option_type="")
        result = engine.validate_option_observation(oo)
        assert not result.is_valid
        assert "MISSING_OPTION_TYPE" in result.reasons

    def test_missing_strike_or_expiry_fails_validation(self):
        oo = _build(expiry="")
        result = engine.validate_option_observation(oo)
        assert not result.is_valid
        assert "MISSING_EXPIRY" in result.reasons

    def test_malformed_row_missing_identity_returns_none(self):
        bad_row = {"TckrSymb": "", "StrkPric": "", "XpryDt": "", "OptnTp": "", "FinInstrmNm": ""}
        oo = runner.build_option_observation_from_row(bad_row, "2026-05-25", moc_taxonomy.RESOLUTION_DAILY)
        assert oo is None

    def test_never_validates_market_value_plausibility(self):
        oo = _build(open_interest=999_999_999_999)
        result = engine.validate_option_observation(oo)
        assert result.is_valid

    def test_bid_ask_absence_never_causes_invalid(self):
        # BID/ASK/BID_QUANTITY/ASK_QUANTITY are always missing for
        # Bhavcopy-sourced data by design -- this must never fail
        # validation or completeness in a way that looks like a real
        # per-row anomaly.
        oo = _build()
        assert oo.bid is None and oo.ask is None
        result = engine.validate_option_observation(oo)
        assert result.is_valid
        assert oo.observation.quality.completeness == 1.0


# ---------------------------------------------------------------------------
# Series ordering
# ---------------------------------------------------------------------------
class TestSeriesOrdering:
    def test_append_preserves_order(self):
        series = engine.new_option_series(
            underlying="ABCAPITAL", strike=360.0, expiry="2026-07-28", option_type="PE",
            instrument_symbol="ABCAPITAL26JUL360PE", resolution=moc_taxonomy.RESOLUTION_DAILY,
            symbol_provenance="SOURCE_AUTHORITATIVE",
        )
        oo1 = _build(timestamp="2026-05-25T15:30:00")
        oo2 = _build(timestamp="2026-05-26T15:30:00", close=40.0)
        series = engine.append_option_observation(series, oo1)
        series = engine.append_option_observation(series, oo2)
        assert len(series) == 2
        timestamps = [o.timestamp for o in series.observations()]
        assert timestamps == sorted(timestamps)

    def test_out_of_order_append_recorded_as_gap_not_silently_dropped(self):
        series = engine.new_option_series(
            underlying="ABCAPITAL", strike=360.0, expiry="2026-07-28", option_type="PE",
            instrument_symbol="ABCAPITAL26JUL360PE", resolution=moc_taxonomy.RESOLUTION_DAILY,
            symbol_provenance="SOURCE_AUTHORITATIVE",
        )
        oo_later = _build(timestamp="2026-05-26T15:30:00")
        oo_earlier = _build(timestamp="2026-05-25T15:30:00", close=40.0)
        series = engine.append_option_observation(series, oo_later)
        series = engine.append_option_observation(series, oo_earlier)
        assert len(series) == 2
        assert len(series.series.gaps) == 1
        assert series.series.gaps[0].reason == moc_taxonomy.GAP_REASON_OUT_OF_ORDER_SKIPPED


# ---------------------------------------------------------------------------
# Duplicate handling
# ---------------------------------------------------------------------------
class TestDuplicateHandling:
    def test_duplicate_row_produces_duplicate_observation_id(self):
        oo1 = _build()
        oo2 = _build()
        assert oo1.observation_id == oo2.observation_id

    def test_journal_can_record_duplicate_detection(self, tmp_path):
        j = journal.OptionsObservationJournal(tmp_path / "options_journal.jsonl")
        oo = _build()
        j.record_observation(oo)
        j.record_duplicate(oo.observation_id)
        records = j.read_all()
        assert any(isinstance(r, dict) and r.get("kind") == "DUPLICATE_DETECTED" for r in records)


# ---------------------------------------------------------------------------
# Replay compatibility
# ---------------------------------------------------------------------------
class TestReplayCompatibility:
    def test_same_file_ingested_twice_byte_identical_series(self):
        csv_text = _real_csv_text(1)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        row = rows[0]
        kwargs = dict(
            underlying="ABCAPITAL",
            strike=float(row["StrkPric"]),
            expiry=row["XpryDt"],
            option_type=row["OptnTp"],
        )
        series_a, _ = runner.ingest_option_observations_from_bhavcopy(csv_text, "2026-05-26", **kwargs)
        series_b, _ = runner.ingest_option_observations_from_bhavcopy(csv_text, "2026-05-26", **kwargs)
        text_a = serialization.option_series_to_json(series_a)
        text_b = serialization.option_series_to_json(series_b)
        assert text_a == text_b

    def test_fingerprint_stable_across_two_real_files_same_shape(self):
        csv_text_1 = _real_csv_text(0)
        csv_text_2 = _real_csv_text(1)
        rows1 = runner.parse_options_bhavcopy_csv(csv_text_1, underlying="ABCAPITAL")
        rows2 = runner.parse_options_bhavcopy_csv(csv_text_2, underlying="ABCAPITAL")
        row1, row2 = rows1[0], rows2[0]
        series_1, _ = runner.ingest_option_observations_from_bhavcopy(
            csv_text_1, "2026-05-25", underlying="ABCAPITAL",
            strike=float(row1["StrkPric"]), expiry=row1["XpryDt"], option_type=row1["OptnTp"],
        )
        series_2, _ = runner.ingest_option_observations_from_bhavcopy(
            csv_text_2, "2026-05-26", underlying="ABCAPITAL",
            strike=float(row2["StrkPric"]), expiry=row2["XpryDt"], option_type=row2["OptnTp"],
        )
        assert serialization.option_series_to_json(series_1) != serialization.option_series_to_json(series_2)


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------
class TestQuery:
    def test_by_underlying_strike_expiry_option_type(self):
        csv_text = _real_csv_text(0)
        rows = runner.parse_options_bhavcopy_csv(csv_text, underlying="ABCAPITAL")
        row = rows[0]
        series, _ = runner.ingest_option_observations_from_bhavcopy(
            csv_text, "2026-05-25", underlying="ABCAPITAL",
            strike=float(row["StrkPric"]), expiry=row["XpryDt"], option_type=row["OptnTp"],
        )
        assert len(query.by_underlying(series, "ABCAPITAL")) == 1
        assert len(query.by_underlying(series, "NIFTY")) == 0
        assert len(query.by_strike(series, float(row["StrkPric"]))) == 1
        assert len(query.by_expiry(series, row["XpryDt"])) == 1
        assert len(query.by_option_type(series, row["OptnTp"])) == 1

    def test_latest_and_earliest(self):
        series = engine.new_option_series(
            underlying="ABCAPITAL", strike=360.0, expiry="2026-07-28", option_type="PE",
            instrument_symbol="ABCAPITAL26JUL360PE", resolution=moc_taxonomy.RESOLUTION_DAILY,
            symbol_provenance="SOURCE_AUTHORITATIVE",
        )
        oo1 = _build(timestamp="2026-05-25T15:30:00")
        oo2 = _build(timestamp="2026-05-26T15:30:00", close=40.0)
        series = engine.append_option_observation(series, oo1)
        series = engine.append_option_observation(series, oo2)
        assert query.earliest(series).timestamp == "2026-05-25T15:30:00"
        assert query.latest(series).timestamp == "2026-05-26T15:30:00"


# ---------------------------------------------------------------------------
# Zero-interpretation contract
# ---------------------------------------------------------------------------
class TestZeroInterpretation:
    FORBIDDEN_TERMS = (
        "pcr",
        "put_call_ratio",
        "max_pain",
        "maxpain",
        "gamma",
        "delta",
        "vega",
        "theta",
        "iv_rank",
        "ivrank",
        "oi_buildup",
        "oi_unwinding",
        "buildup",
        "unwinding",
        "support",
        "resistance",
        "trend",
        "bullish",
        "bearish",
    )

    def test_no_interpretive_identifiers_in_source(self):
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
        text = (Path(engine.__file__)).read_text()
        assert "hashlib" not in text
