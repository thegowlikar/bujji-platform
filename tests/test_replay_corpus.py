"""Tests — Engineering Series 59: historical replay corpus."""
import logging
from datetime import datetime

import pytest

from bujji.production_runtime.circuit_breaker import RuntimeCircuitBreaker
from bujji.production_runtime.config import RUNTIME_MODE_SHADOW
from bujji.production_runtime.health import RuntimeHealthAggregator
from bujji.production_runtime.rate_limiter import RateLimiterConfig, RuntimeRateLimiter
from bujji.production_runtime.startup import startup
from bujji.qualification.historical_runner import HistoricalQualificationRunner
from bujji.replay.corpus_builder import CorpusBuildResult, build_corpus, compute_checksum
from bujji.replay.manifest import CorpusManifest, build_manifest
from bujji.replay.validator import HistoricalSessionRecord, validate_corpus, validate_session

FIXED_CLOCK = lambda: datetime(2026, 1, 1, 9, 20, 0)


def _entries(trading_date, count=2):
    return tuple(
        (25150 + i * 50, "CE" if i % 2 == 0 else "PE", f"{trading_date}-EXP", f"NSE:X{i}") for i in range(count)
    )


def _record(session_id, trading_date, timestamp, spot=25148.0, entries=None, holiday=False):
    exp = f"{trading_date}-EXP"
    return HistoricalSessionRecord(
        session_id=session_id,
        trading_date=trading_date,
        timestamp=timestamp,
        market_context="TRENDING_UP",
        market_opinion="BULLISH",
        context_stability="STABLE",
        calibration="CALIBRATED",
        governance="APPROVED",
        lifecycle="ACTIVE",
        contract="COMPLETE",
        spot=spot,
        spot_as_of=timestamp,
        option_chain_entries=entries if entries is not None else _entries(trading_date),
        option_chain_expiries=(exp,),
        option_chain_as_of=timestamp,
        is_holiday=holiday,
    )


def test_valid_corpus_creation():
    records = [
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("S2", "2026-01-02", "2026-01-02T09:20:00"),
    ]
    result = build_corpus(records, source_description="test corpus", clock=FIXED_CLOCK)
    assert isinstance(result, CorpusBuildResult)
    assert len(result.scenarios) == 2
    assert len(result.timestamps) == 2
    assert result.validation_report.invalid_sessions == 0


def test_corrupted_session_detected_and_excluded():
    records = [
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("SBAD", "2026-01-02", "2026-01-02T09:20:00", entries=()),  # no option chain
    ]
    result = build_corpus(records, source_description="test corpus", clock=FIXED_CLOCK)
    assert len(result.scenarios) == 1
    assert result.scenarios[0].scenario_id == "S1"
    assert result.validation_report.invalid_sessions == 1
    bad_result = next(r for r in result.validation_report.session_results if r.session_id == "SBAD")
    assert not bad_result.valid
    assert any("option chain" in issue for issue in bad_result.issues)


def test_missing_spot_snapshot_detected():
    record = _record("S1", "2026-01-01", "2026-01-01T09:20:00", spot=None)
    result = validate_session(record)
    assert result.valid is False
    assert any("spot" in issue for issue in result.issues)


def test_missing_option_chain_detected():
    record = _record("S1", "2026-01-01", "2026-01-01T09:20:00", entries=())
    result = validate_session(record)
    assert result.valid is False
    assert any("option chain" in issue for issue in result.issues)


def test_inconsistent_option_chain_structure_detected():
    record = HistoricalSessionRecord(
        session_id="S1",
        trading_date="2026-01-01",
        timestamp="2026-01-01T09:20:00",
        spot=25148.0,
        option_chain_entries=((25150, "CE", "WRONG-EXPIRY", "X"),),
        option_chain_expiries=("2026-01-01-EXP",),
    )
    result = validate_session(record)
    assert result.valid is False
    assert any("not declared" in issue for issue in result.issues)


def test_manifest_generation():
    records = [_record("S1", "2026-01-01", "2026-01-01T09:20:00")]
    checksum = compute_checksum(records)
    manifest = build_manifest(
        source_description="test", trading_dates=("2026-01-01",), session_count=1, checksum=checksum, clock=FIXED_CLOCK
    )
    assert isinstance(manifest, CorpusManifest)
    assert manifest.session_count == 1
    assert manifest.checksum == checksum
    assert manifest.schema_version == "1.0.0"
    assert manifest.corpus_id.startswith("CORPUS-")


def test_checksum_is_stable_given_same_records():
    records = [
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("S2", "2026-01-02", "2026-01-02T09:20:00"),
    ]
    assert compute_checksum(records) == compute_checksum(records)


def test_checksum_differs_when_order_differs():
    a = [_record("S1", "2026-01-01", "2026-01-01T09:20:00"), _record("S2", "2026-01-02", "2026-01-02T09:20:00")]
    b = list(reversed(a))
    assert compute_checksum(a) != compute_checksum(b)


def test_deterministic_ordering_restores_chronology():
    records = [
        _record("S3", "2026-01-03", "2026-01-03T09:20:00"),
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("S2", "2026-01-02", "2026-01-02T09:20:00"),
    ]
    result = build_corpus(records, source_description="test corpus", clock=FIXED_CLOCK)
    assert [s.scenario_id for s in result.scenarios] == ["S1", "S2", "S3"]
    assert list(result.timestamps) == sorted(result.timestamps)


def test_holiday_sessions_excluded():
    records = [
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("SH", "2026-01-02", "2026-01-02T09:20:00", holiday=True),
    ]
    result = build_corpus(records, source_description="test corpus", clock=FIXED_CLOCK)
    assert [s.scenario_id for s in result.scenarios] == ["S1"]
    assert result.excluded_holiday_session_ids == ("SH",)


def test_holiday_by_declared_date():
    records = [_record("S1", "2026-01-01", "2026-01-01T09:20:00")]
    result = build_corpus(records, source_description="t", holidays=("2026-01-01",), clock=FIXED_CLOCK)
    assert result.scenarios == ()
    assert result.excluded_holiday_session_ids == ("S1",)


def test_empty_corpus_input():
    result = build_corpus([], source_description="empty", clock=FIXED_CLOCK)
    assert result.scenarios == ()
    assert result.manifest.session_count == 0


def test_replay_compatible_with_series_58_runner():
    records = [
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
        _record("S2", "2026-01-02", "2026-01-02T09:25:00"),
    ]
    result = build_corpus(records, source_description="test corpus", clock=FIXED_CLOCK)

    root, sreport = startup({"mode": RUNTIME_MODE_SHADOW, "broker_name": "paper"}, clock=FIXED_CLOCK)
    runner = HistoricalQualificationRunner(
        root=root,
        startup_report=sreport,
        health_aggregator=RuntimeHealthAggregator(),
        circuit_breaker=RuntimeCircuitBreaker(),
        rate_limiter=RuntimeRateLimiter(config=RateLimiterConfig(min_interval_seconds=1.0)),
        logger=logging.getLogger("test.replay"),
    )
    # No adapter, no wrapper -- passed straight through.
    recorder = runner.run_corpus(result.scenarios, result.timestamps)
    assert len(recorder) == 2
    assert [r.replay_identifier for r in recorder.records] == ["S1", "S2"]
    for record in recorder.records:
        assert record.qualification_fingerprint == root.config.qualification_fingerprint


def test_corpus_manifest_is_immutable():
    records = [_record("S1", "2026-01-01", "2026-01-01T09:20:00")]
    result = build_corpus(records, source_description="test", clock=FIXED_CLOCK)
    with pytest.raises(Exception):
        result.manifest.session_count = 999  # type: ignore[misc]
    with pytest.raises(Exception):
        result.manifest.checksum = "tampered"  # type: ignore[misc]


def test_session_records_never_mutated():
    record = _record("S1", "2026-01-01", "2026-01-01T09:20:00")
    original_repr = repr(record)
    build_corpus([record], source_description="test", clock=FIXED_CLOCK)
    assert repr(record) == original_repr


def test_validate_corpus_preserves_input_order():
    records = [
        _record("S3", "2026-01-03", "2026-01-03T09:20:00"),
        _record("S1", "2026-01-01", "2026-01-01T09:20:00"),
    ]
    report = validate_corpus(records, clock=FIXED_CLOCK)
    assert [r.session_id for r in report.session_results] == ["S3", "S1"]


def test_never_repairs_missing_data():
    record = _record("S1", "2026-01-01", "2026-01-01T09:20:00", spot=None, entries=())
    result = build_corpus([record], source_description="test", clock=FIXED_CLOCK)
    assert result.scenarios == ()
    bad = result.validation_report.session_results[0]
    assert bad.valid is False
    assert len(bad.issues) >= 2
