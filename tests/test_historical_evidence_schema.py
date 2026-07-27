"""Tests — Engineering Series 64: historical evidence schema expansion."""
from datetime import datetime

import pytest

from bujji.replay.historical_session import HistoricalSessionRecord, OptionLiquiditySnapshot
from bujji.replay.schema_version import (
    ALL_SCHEMA_VERSIONS,
    CURRENT_SCHEMA_VERSION,
    SCHEMA_VERSION_V1,
    SCHEMA_VERSION_V2,
    SCHEMA_VERSION_V3,
)
from bujji.replay.validator import validate_corpus, validate_session
from bujji.replay.validator import HistoricalSessionRecord as ValidatorHistoricalSessionRecord
from bujji.replay.corpus_builder import build_corpus, compute_checksum
from bujji.replay.manifest import build_manifest
from bujji.qualification.historical_runner import HistoricalQualificationRunner

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)


def _legacy_record(session_id="S1", trading_date="2026-01-01"):
    """A record built exactly as Series 59 / Data Acquisition Sprint A
    code has always built it -- no Series 64 keyword arguments at all.
    """
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date=trading_date,
        timestamp=trading_date + "T09:20:00",
        market_context="TRENDING_UP",
        spot=25000.0,
        spot_as_of=trading_date + "T09:20:00",
        option_chain_entries=((25000, "CE", "2026-07-31", "NSE:X"),),
        option_chain_expiries=("2026-07-31",),
        option_chain_as_of=trading_date + "T09:20:00",
    )


def _enriched_record(session_id="S1", trading_date="2026-01-01"):
    liquidity = (
        OptionLiquiditySnapshot(
            strike=25000, option_type="CE", expiry="2026-07-31", contract_symbol="NSE:X",
            open_interest=12345, change_in_open_interest=100, bid=10.5, ask=11.0,
        ),
    )
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date=trading_date,
        timestamp=trading_date + "T09:20:00",
        market_context="TRENDING_UP",
        spot=25000.0,
        spot_as_of=trading_date + "T09:20:00",
        option_chain_entries=((25000, "CE", "2026-07-31", "NSE:X"),),
        option_chain_expiries=("2026-07-31",),
        option_chain_as_of=trading_date + "T09:20:00",
        option_chain_liquidity=liquidity,
        vix=13.28,
        vix_as_of=trading_date + "T09:20:00",
        session_exchange="NSE",
        session_segment="FO",
    )


# ---------------------------------------------------------------------------
# Legacy corpus compatibility
# ---------------------------------------------------------------------------


def test_legacy_construction_still_works_unchanged():
    record = _legacy_record()
    assert record.session_id == "S1"
    assert record.spot == 25000.0
    assert record.schema_version == CURRENT_SCHEMA_VERSION  # new default, never required


def test_legacy_record_has_empty_new_fields_by_default():
    record = _legacy_record()
    assert record.option_chain_liquidity == ()
    assert record.vix is None
    assert record.vix_as_of is None
    assert record.session_exchange is None
    assert record.session_segment is None


def test_validator_reexport_is_the_same_class():
    assert ValidatorHistoricalSessionRecord is HistoricalSessionRecord


def test_legacy_and_enriched_records_both_validate_identically_on_shared_fields():
    legacy_result = validate_session(_legacy_record())
    enriched_result = validate_session(_enriched_record())
    assert legacy_result.valid == enriched_result.valid is True
    assert legacy_result.issues == enriched_result.issues == ()


# ---------------------------------------------------------------------------
# Optional-field serialization / deserialization
# ---------------------------------------------------------------------------


def test_optional_fields_round_trip_through_dataclasses_asdict():
    from dataclasses import asdict

    record = _enriched_record()
    as_dict = asdict(record)
    assert as_dict["vix"] == 13.28
    assert as_dict["option_chain_liquidity"][0]["open_interest"] == 12345

    # asdict() recursively flattens nested dataclasses to plain dicts,
    # so a faithful round-trip must rebuild the nested
    # OptionLiquiditySnapshot objects explicitly -- this is a property
    # of dataclasses.asdict() itself, not a schema defect.
    rebuilt = HistoricalSessionRecord(
        **{**as_dict, "option_chain_liquidity": tuple(OptionLiquiditySnapshot(**d) for d in as_dict["option_chain_liquidity"])}
    )
    assert rebuilt == record


def test_missing_liquidity_fields_represented_as_none_not_zero():
    snapshot = OptionLiquiditySnapshot(strike=25000, option_type="CE", expiry="2026-07-31", contract_symbol="NSE:X")
    assert snapshot.open_interest is None
    assert snapshot.bid is None
    assert snapshot.open_interest != 0  # explicit distinction: absent, not a real zero reading


# ---------------------------------------------------------------------------
# Manifest compatibility
# ---------------------------------------------------------------------------


def test_manifest_gains_session_schema_version_field_with_sane_default():
    manifest = build_manifest(
        source_description="test", trading_dates=("2026-01-01",), session_count=1, checksum="x", clock=FIXED_CLOCK
    )
    assert manifest.session_schema_version == CURRENT_SCHEMA_VERSION
    assert manifest.schema_version == "1.0.0"  # manifest's own format version, unchanged


def test_manifest_session_schema_version_is_overridable():
    manifest = build_manifest(
        source_description="test", trading_dates=("2026-01-01",), session_count=1, checksum="x",
        clock=FIXED_CLOCK, session_schema_version=SCHEMA_VERSION_V1,
    )
    assert manifest.session_schema_version == SCHEMA_VERSION_V1


# ---------------------------------------------------------------------------
# Validator compatibility
# ---------------------------------------------------------------------------


def test_validator_never_reads_series_64_fields():
    import ast
    import inspect
    from bujji.replay import validator as validator_module

    tree = ast.parse(inspect.getsource(validator_module))
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    series64_fields = {"option_chain_liquidity", "vix", "vix_as_of", "session_exchange", "session_segment"}
    assert not (attrs & series64_fields)


def test_validate_corpus_unchanged_behavior_with_enriched_records():
    report = validate_corpus([_enriched_record(), _legacy_record(session_id="S2", trading_date="2026-01-02")], clock=FIXED_CLOCK)
    assert report.total_sessions == 2
    assert report.valid_sessions == 2
    assert report.invalid_sessions == 0


# ---------------------------------------------------------------------------
# Replay / corpus-builder compatibility
# ---------------------------------------------------------------------------


def test_corpus_builder_checksum_unaffected_by_new_fields():
    legacy = _legacy_record()
    enriched = _enriched_record()
    # compute_checksum only reads session_id/trading_date/timestamp/spot/
    # expiries/entries -- identical for these two records -- so the
    # presence of Series 64 fields must not change the checksum.
    assert compute_checksum([legacy]) == compute_checksum([enriched])


def test_build_corpus_carries_new_fields_through_unmodified():
    result = build_corpus([_enriched_record()], source_description="test", clock=FIXED_CLOCK)
    assert len(result.scenarios) == 1
    # ReplayScenario (Series 46, frozen) never receives OI/VIX -- Series
    # 64 explicitly does not expose new fields to the Trading Brain yet.
    scenario = result.scenarios[0]
    assert not hasattr(scenario, "vix")
    assert not hasattr(scenario, "option_chain_liquidity")


def test_build_corpus_manifest_has_session_schema_version():
    result = build_corpus([_enriched_record()], source_description="test", clock=FIXED_CLOCK)
    assert result.manifest.session_schema_version == CURRENT_SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Deterministic serialization / schema-version compatibility
# ---------------------------------------------------------------------------


def test_deterministic_checksum_given_enriched_records():
    a = compute_checksum([_enriched_record()])
    b = compute_checksum([_enriched_record()])
    assert a == b


def test_all_schema_versions_are_finite_and_ordered():
    # Series 67 added SCHEMA_VERSION_V3 (open/high/low/close transport).
    assert ALL_SCHEMA_VERSIONS == (SCHEMA_VERSION_V1, SCHEMA_VERSION_V2, SCHEMA_VERSION_V3)
    assert CURRENT_SCHEMA_VERSION == SCHEMA_VERSION_V3


def test_explicit_legacy_schema_version_still_constructs():
    record = HistoricalSessionRecord(
        session_id="S1", trading_date="2026-01-01", timestamp="2026-01-01T09:20:00",
        spot=25000.0, option_chain_entries=((25000, "CE", "2026-07-31", "NSE:X"),),
        option_chain_expiries=("2026-07-31",), schema_version=SCHEMA_VERSION_V1,
    )
    assert record.schema_version == SCHEMA_VERSION_V1
    assert validate_session(record).valid is True


# ---------------------------------------------------------------------------
# No runtime behavioral change
# ---------------------------------------------------------------------------


def test_no_runtime_mutation_enriched_record_untouched():
    record = _enriched_record()
    before = repr(record)
    build_corpus([record], source_description="test", clock=FIXED_CLOCK)
    assert repr(record) == before


def test_qualification_runner_module_never_references_series_64_fields():
    import ast
    import inspect
    from bujji.qualification import historical_runner as runner_module

    tree = ast.parse(inspect.getsource(runner_module))
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    series64_fields = {"option_chain_liquidity", "vix", "vix_as_of", "session_exchange", "session_segment"}
    assert not (attrs & series64_fields)
