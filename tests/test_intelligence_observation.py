"""BUJJI Options OS, Integration Series 2, Sprint 2 -- Continuous
Intelligence Observation Framework regression suite: healthy, degraded,
stale, unavailable, unknown, serialization, journaling, deterministic
metrics, feature flag off, feature flag on, byte-identical trading
behavior, isolation audit.
"""
import dataclasses
import json
import logging
from datetime import datetime, time as dtime
from pathlib import Path

import pytest

from bujji.intelligence.mic_adapter.adapter import IntelligenceAdapter
from bujji.intelligence.mic_adapter.config import IntelligenceAdapterConfig
from bujji.intelligence.mic_adapter.monitor import query as mquery
from bujji.intelligence.mic_adapter.monitor.config import ObservationMonitorConfig
from bujji.intelligence.mic_adapter.monitor.health import (
    DEGRADED, HEALTHY, OBSERVATION_HEALTH_STATES, STALE, UNAVAILABLE, UNKNOWN,
)
from bujji.intelligence.mic_adapter.monitor.metrics import ObservationMetrics, ObservationProvenance
from bujji.intelligence.mic_adapter.monitor.monitor import ObservationMonitor
from bujji.intelligence.mic_adapter.monitor.serialization import observation_health_from_dict, observation_health_to_dict
from bujji.journal.intelligence_observation_journal import IntelligenceObservationJournal
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

REAL_CONSUMER_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")
FIXED_NOW = datetime(2026, 7, 21, 14, 20)


def _real_adapter():
    return IntelligenceAdapter(IntelligenceAdapterConfig(enabled=True, consumer_journal_path=REAL_CONSUMER_JOURNAL))


def _missing_adapter(tmp_path):
    return IntelligenceAdapter(IntelligenceAdapterConfig(enabled=True, consumer_journal_path=tmp_path / "nope.jsonl"))


# ---------------------------------------------------------------------- #
# Healthy
# ---------------------------------------------------------------------- #
def test_healthy_when_fresh_and_fast():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    monitor = ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    assert health.health_status == HEALTHY
    assert health.reason == "nominal"


# ---------------------------------------------------------------------- #
# Degraded
# ---------------------------------------------------------------------- #
def test_degraded_when_latency_exceeds_threshold():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    calls = iter([0.0, 1.0])
    monitor = ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW, latency_clock=lambda: next(calls))
    health = monitor.observe(feature_flag_enabled=True)
    assert health.health_status == DEGRADED
    assert health.latency_seconds == 1.0


# ---------------------------------------------------------------------- #
# Stale
# ---------------------------------------------------------------------- #
def test_stale_when_snapshot_too_old():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    monitor = ObservationMonitor(
        _real_adapter(), config=ObservationMonitorConfig(stale_after_seconds=60), clock=lambda: datetime(2026, 8, 1),
    )
    health = monitor.observe(feature_flag_enabled=True)
    assert health.health_status == STALE
    assert health.snapshot_age_seconds > 60


# ---------------------------------------------------------------------- #
# Unavailable
# ---------------------------------------------------------------------- #
def test_unavailable_when_journal_missing(tmp_path):
    monitor = ObservationMonitor(_missing_adapter(tmp_path), clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    assert health.health_status == UNAVAILABLE
    assert health.reason == "no_snapshot_available"


def test_unavailable_when_adapter_raises(tmp_path, monkeypatch):
    adapter = _missing_adapter(tmp_path)

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(adapter, "load_latest_snapshot", _raise)
    monitor = ObservationMonitor(adapter, clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    assert health.health_status == UNAVAILABLE
    assert health.reason == "adapter_load_exception"


# ---------------------------------------------------------------------- #
# Unknown
# ---------------------------------------------------------------------- #
def test_unknown_when_flag_disabled():
    monitor = ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=False)
    assert health.health_status == UNKNOWN
    assert health.reason == "adapter_disabled"


def test_every_status_is_in_taxonomy(tmp_path):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    statuses = [
        ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW).observe(True).health_status,
        ObservationMonitor(_missing_adapter(tmp_path), clock=lambda: FIXED_NOW).observe(True).health_status,
        ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW).observe(False).health_status,
    ]
    for s in statuses:
        assert s in OBSERVATION_HEALTH_STATES


# ---------------------------------------------------------------------- #
# Serialization
# ---------------------------------------------------------------------- #
def test_serialization_round_trip():
    monitor = ObservationMonitor(_real_adapter() if REAL_CONSUMER_JOURNAL.exists() else _missing_adapter(Path("/tmp")),
                                 clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    restored = observation_health_from_dict(observation_health_to_dict(health))
    assert restored == health


def test_health_and_provenance_are_frozen():
    monitor = ObservationMonitor(_missing_adapter(Path("/tmp")), clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        health.health_status = "hacked"
    with pytest.raises(dataclasses.FrozenInstanceError):
        health.provenance.source = "hacked"


def test_metrics_has_no_trading_fields():
    fields = {f.name for f in dataclasses.fields(ObservationMetrics)}
    forbidden = {"buy", "sell", "position", "pnl", "order", "broker", "execution", "risk", "quantity", "lots"}
    assert fields.isdisjoint(forbidden)


# ---------------------------------------------------------------------- #
# Journaling
# ---------------------------------------------------------------------- #
def test_journal_round_trips(tmp_path):
    logger = logging.getLogger("test")
    journal = IntelligenceObservationJournal(tmp_path / "obs.jsonl", logger)
    monitor = ObservationMonitor(_missing_adapter(tmp_path), clock=lambda: FIXED_NOW)
    health = monitor.observe(feature_flag_enabled=True)
    journal.record(health)
    assert journal.read_all() == [health]


def test_journal_is_append_only(tmp_path):
    logger = logging.getLogger("test")
    journal = IntelligenceObservationJournal(tmp_path / "obs.jsonl", logger)
    monitor = ObservationMonitor(_missing_adapter(tmp_path), clock=lambda: FIXED_NOW)
    journal.record(monitor.observe(feature_flag_enabled=True))
    journal.record(monitor.observe(feature_flag_enabled=True))
    assert len(journal.read_all()) == 2


def test_journal_never_touches_decision_journal_file():
    """The observation journal never imports or opens the Decision
    Journal's module or file -- only prose mentions of "Decision Journal"
    (e.g. in the module docstring) are permitted."""
    import ast
    f = Path("/opt/bujji/app/bujji/journal/intelligence_observation_journal.py")
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names]
            module = getattr(node, "module", None) or ""
            assert "decision_journal" not in module
            assert not any("decision_journal" in n for n in names)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "decision_journal.jsonl" not in node.value
            assert "DecisionJournal(" not in node.value


# ---------------------------------------------------------------------- #
# Deterministic metrics
# ---------------------------------------------------------------------- #
def test_deterministic_health_identity_given_same_clock():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    h1 = ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW, latency_clock=lambda: 0.0).observe(True)
    h2 = ObservationMonitor(_real_adapter(), clock=lambda: FIXED_NOW, latency_clock=lambda: 0.0).observe(True)
    assert h1 == h2  # identical clock AND identical (fake) latency clock -> byte-identical


def test_metrics_counters_increment_correctly(tmp_path):
    monitor = ObservationMonitor(_missing_adapter(tmp_path), clock=lambda: FIXED_NOW)
    monitor.observe(feature_flag_enabled=True)
    monitor.observe(feature_flag_enabled=True)
    metrics = mquery.latest_metrics(monitor)
    assert metrics.load_attempts == 2
    assert metrics.load_failures == 2
    assert metrics.missing_snapshot_count == 2


# ---------------------------------------------------------------------- #
# Feature flag off / on (through the real decision pipeline)
# ---------------------------------------------------------------------- #
def _timed_config():
    from bujji.core.config import AppConfig
    cfg = AppConfig()
    cfg.timing.orb_start = dtime(9, 15)
    cfg.timing.orb_end = dtime(9, 20)
    cfg.timing.trading_start = dtime(9, 20)
    cfg.timing.trading_end = dtime(15, 15)
    cfg.timing.hard_exit = dtime(15, 5)
    return cfg


def _replay_config(base_config, tmp_dir, enable_adapter):
    cfg = base_config
    cfg.paths.journal_csv = tmp_dir / "j.csv"
    cfg.paths.database = tmp_dir / "b.db"
    cfg.paths.state_file = tmp_dir / "s.json"
    cfg.paths.decision_journal = tmp_dir / "decision_journal.jsonl"
    cfg.paths.ops_restart_count = tmp_dir / "ops_restart_count.json"
    cfg.paths.incident_log = tmp_dir / "incident_log.jsonl"
    cfg.paths.intelligence_observation_journal = tmp_dir / "intelligence_observation_journal.jsonl"
    cfg.broker.order_timeout_seconds = 0.05
    cfg.broker.poll_interval_seconds = 0.01
    cfg.intelligence_adapter.enabled = enable_adapter
    cfg.intelligence_adapter.consumer_journal_path = REAL_CONSUMER_JOURNAL
    return cfg


@pytest.mark.asyncio
async def test_flag_off_produces_no_observation_journal(tmp_path, logger, config):
    cfg = _replay_config(config, tmp_path, enable_adapter=False)
    engine = ReplayEngine(cfg, logger)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(15, 5, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)
    assert not cfg.paths.intelligence_observation_journal.exists()


@pytest.mark.asyncio
async def test_flag_on_produces_observation_journal_entry(tmp_path, logger, config):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    cfg = _replay_config(config, tmp_path, enable_adapter=True)
    engine = ReplayEngine(cfg, logger)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(15, 5, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)
    rows = [json.loads(l) for l in cfg.paths.intelligence_observation_journal.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["health_status"] in OBSERVATION_HEALTH_STATES


# ---------------------------------------------------------------------- #
# Byte-identical trading behavior
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_trading_behaviour_byte_identical_with_observation_on_or_off(tmp_path, logger, config):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 25, 22000, 22040, 21990, 22035, vol=1200),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    dir_off, dir_on = tmp_path / "off", tmp_path / "on"
    dir_off.mkdir()
    dir_on.mkdir()

    cfg_off = _replay_config(config, dir_off, enable_adapter=False)
    cfg_on = _replay_config(_timed_config(), dir_on, enable_adapter=True)

    result_off = await ReplayEngine(cfg_off, logger).run(candles)
    result_on = await ReplayEngine(cfg_on, logger).run(candles)

    assert result_off.final_state == result_on.final_state
    assert result_off.trades == result_on.trades

    csv_off = cfg_off.paths.journal_csv.read_text() if cfg_off.paths.journal_csv.exists() else ""
    csv_on = cfg_on.paths.journal_csv.read_text() if cfg_on.paths.journal_csv.exists() else ""
    assert csv_off == csv_on

    rows_off = [json.loads(l) for l in cfg_off.paths.decision_journal.read_text().splitlines()]
    rows_on = [json.loads(l) for l in cfg_on.paths.decision_journal.read_text().splitlines()]
    for row_off, row_on in zip(rows_off, rows_on):
        row_on_stripped = dict(row_on)
        row_on_stripped.pop("intelligence_reference", None)
        assert row_off == row_on_stripped
        assert "health_status" not in row_off
        assert "health_status" not in row_on


# ---------------------------------------------------------------------- #
# Isolation audit
# ---------------------------------------------------------------------- #
def test_monitor_never_imports_mic_reasoning_engines():
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/monitor")
    forbidden_substrings = (
        "mic_v2.fusion", "mic_v2.hypothesis", "mic_v2.candidate", "mic_v2.qualification",
        "mic_v2.memory", "mic_v2.quality", "mic_v2.graph", "mic_v2.counterfactual",
        "mic_v2.consistency", "mic_v2.laboratory", "mic_v2.experiment", "mic_v2.simulation",
        "mic_v2.certification", "mic_v2.registry", "mic_v2.lineage", "mic_v2.compatibility",
        "mic_v2.artifact", "mic_v2.environment", "mic_v2.archive", "mic_v2.diff",
        "mic_v2.explain", "mic_v2.knowledge", "mic_v2.publication", "mic_v2.runtime", "mic_v2.live",
        "mic_v2.replay", "mic_v2.observation_builder", "mic_v2.engine", "mic_v2.consumer",
    )
    for f in root.rglob("*.py"):
        text = f.read_text()
        for forbidden in forbidden_substrings:
            assert forbidden not in text, f"{f.name} references forbidden MIC v2 module {forbidden}"


def test_monitor_never_reads_broker_or_position_state():
    import ast
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/monitor")
    forbidden = ("broker", "position", "pnl", "buy", "sell", "order", "execute", "trade", "risk", "strategy")
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name_parts = node.name.lower().split("_")
                for word in forbidden:
                    assert word not in name_parts, f"{f.name} defines suspicious method '{node.name}'"


def test_monitor_never_mutates_non_self_attributes():
    import ast
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/monitor")
    for fname in ("monitor.py", "metrics.py", "health.py", "query.py"):
        f = root / fname
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assert isinstance(target.value, ast.Name) and target.value.id == "self", (
                            f"{fname} mutates a non-self attribute -- forbidden"
                        )


def test_observation_journal_only_opens_files_in_append_or_read_mode():
    import ast
    f = Path("/opt/bujji/app/bujji/journal/intelligence_observation_journal.py")
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value in ("a", "r")


def test_no_forbidden_trading_identifiers_in_monitor_code():
    import ast
    forbidden = ("buy", "sell", "position", "pnl", "profit", "loss", "risk")
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/monitor")
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        parts = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                parts.update(node.id.lower().split("_"))
            if isinstance(node, ast.FunctionDef):
                parts.update(node.name.lower().split("_"))
            if isinstance(node, ast.arg):
                parts.update(node.arg.lower().split("_"))
            if isinstance(node, ast.Attribute):
                parts.update(node.attr.lower().split("_"))
        for word in forbidden:
            assert word not in parts, f"{f.name} defines an identifier containing the whole word '{word}'"
