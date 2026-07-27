"""BUJJI Options OS, Integration Series 1, Sprint 1 -- Intelligence
Adapter regression suite: mapping correctness, immutable snapshot,
adapter loading, feature flag disabled, feature flag enabled, no
behavioural change, byte-identical replay output, isolation audit.
"""
import asyncio
import dataclasses
import json
from datetime import datetime
from pathlib import Path

import pytest

from bujji.intelligence.mic_adapter.adapter import IntelligenceAdapter
from bujji.intelligence.mic_adapter.config import IntelligenceAdapterConfig
from bujji.intelligence.mic_adapter.mapper import map_consumer_record
from bujji.intelligence.mic_adapter.models import IntelligenceSnapshot, IntelligenceSnapshotProvenance
from bujji.intelligence.mic_adapter.query import latest_record, record_by_publication_id, record_by_replay_id
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

REAL_CONSUMER_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")


class _FakeConsumerRecord:
    """A duck-typed stand-in for mic_v2.models.consumer.ConsumerRecord --
    proves the mapper has zero import dependency on MIC v2 itself."""

    def __init__(self):
        self.consumer_id = "CONSUMER-FAKE"
        self.timestamp = datetime(2026, 7, 21, 9, 30)
        self.consumer_status = "AVAILABLE"
        self.publication_id = "PUBLICATION-FAKE"
        self.replay_id = "REPLAY-FAKE"
        self.certification_id = "CERTIFICATION-FAKE"
        self.archive_id = "ARCHIVE-FAKE"
        self.lineage_id = "LINEAGE-FAKE"
        self.compatibility_id = "COMPATIBILITY-FAKE"
        self.environment_id = "ENVIRONMENT-FAKE"
        self.artifact_id = "ARTIFACT-FAKE"


# ---------------------------------------------------------------------- #
# Mapping correctness
# ---------------------------------------------------------------------- #
def test_mapper_copies_every_field_verbatim():
    fake = _FakeConsumerRecord()
    snapshot = map_consumer_record(fake)
    assert snapshot.snapshot_id == fake.consumer_id
    assert snapshot.timestamp == fake.timestamp
    assert snapshot.consumer_status == fake.consumer_status
    assert snapshot.replay_id == fake.replay_id
    assert snapshot.publication_id == fake.publication_id
    assert snapshot.certification_id == fake.certification_id
    assert snapshot.archive_id == fake.archive_id
    assert snapshot.lineage_id == fake.lineage_id
    assert snapshot.compatibility_id == fake.compatibility_id
    assert snapshot.environment_id == fake.environment_id
    assert snapshot.artifact_id == fake.artifact_id


def test_mapper_never_imports_mic_v2():
    """The mapper works against a plain duck-typed object with zero
    mic_v2 import -- proving it performs pure field mapping only."""
    import ast
    f = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter" / "mapper.py"
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            names = [n.name for n in node.names]
            assert "mic_v2" not in module and not any("mic_v2" in n for n in names)


# ---------------------------------------------------------------------- #
# Immutable snapshot
# ---------------------------------------------------------------------- #
def test_snapshot_is_frozen():
    snapshot = map_consumer_record(_FakeConsumerRecord())
    with pytest.raises(dataclasses.FrozenInstanceError):
        snapshot.replay_id = "hacked"


def test_provenance_is_frozen():
    prov = IntelligenceSnapshotProvenance(source="mic_v2_consumer_api", ruleset_version="1.0.0", mic_schema_version="1.0.0")
    with pytest.raises(dataclasses.FrozenInstanceError):
        prov.source = "hacked"


def test_snapshot_has_no_trading_fields():
    fields = {f.name for f in dataclasses.fields(IntelligenceSnapshot)}
    forbidden = {"buy", "sell", "position", "pnl", "order", "broker", "execution", "risk", "quantity", "lots"}
    assert fields.isdisjoint(forbidden)


# ---------------------------------------------------------------------- #
# Adapter loading
# ---------------------------------------------------------------------- #
def test_adapter_loads_latest_from_real_journal():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    config = IntelligenceAdapterConfig(enabled=True, consumer_journal_path=REAL_CONSUMER_JOURNAL)
    adapter = IntelligenceAdapter(config)
    snapshot = adapter.load_latest_snapshot()
    assert snapshot is not None
    assert isinstance(snapshot, IntelligenceSnapshot)


def test_adapter_load_by_publication_and_replay_match_latest():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    config = IntelligenceAdapterConfig(enabled=True, consumer_journal_path=REAL_CONSUMER_JOURNAL)
    adapter = IntelligenceAdapter(config)
    latest = adapter.load_latest_snapshot()
    assert adapter.load_snapshot(latest.publication_id) == latest
    assert adapter.load_snapshot_by_replay(latest.replay_id) == latest


def test_adapter_returns_none_for_unknown_ids():
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    config = IntelligenceAdapterConfig(enabled=True, consumer_journal_path=REAL_CONSUMER_JOURNAL)
    adapter = IntelligenceAdapter(config)
    assert adapter.load_snapshot("PUBLICATION-DOES-NOT-EXIST") is None
    assert adapter.load_snapshot_by_replay("REPLAY-DOES-NOT-EXIST") is None


def test_adapter_returns_none_for_missing_journal_file(tmp_path):
    config = IntelligenceAdapterConfig(enabled=True, consumer_journal_path=tmp_path / "nope.jsonl")
    adapter = IntelligenceAdapter(config)
    assert adapter.load_latest_snapshot() is None


def test_adapter_never_caches_stale_state(tmp_path):
    """Appending a new record to the journal between two calls must be
    reflected immediately -- proving the adapter never caches mutable
    state across calls."""
    journal_path = tmp_path / "consumer.jsonl"
    row1 = {
        "schema_version": "1.0.0", "consumer_id": "CONSUMER-1", "timestamp": "2026-07-21T09:00:00",
        "consumer_status": "AVAILABLE", "publication_id": "PUBLICATION-1", "replay_id": "REPLAY-1",
        "certification_id": "C-1", "archive_id": "A-1", "lineage_id": "L-1", "compatibility_id": "COMP-1",
        "environment_id": "E-1", "artifact_id": "ART-1",
        "provenance": {"source": "replay", "ruleset_version": "1.0.0"},
    }
    journal_path.write_text(json.dumps(row1) + "\n")
    config = IntelligenceAdapterConfig(enabled=True, consumer_journal_path=journal_path)
    adapter = IntelligenceAdapter(config)
    first = adapter.load_latest_snapshot()
    assert first.publication_id == "PUBLICATION-1"

    row2 = dict(row1, consumer_id="CONSUMER-2", publication_id="PUBLICATION-2", replay_id="REPLAY-2")
    with open(journal_path, "a") as fh:
        fh.write(json.dumps(row2) + "\n")
    second = adapter.load_latest_snapshot()
    assert second.publication_id == "PUBLICATION-2"


# ---------------------------------------------------------------------- #
# Feature flag disabled / enabled
# ---------------------------------------------------------------------- #
def _timed_config():
    """Builds a second, independent config with the same timing overrides
    as conftest.py's own `config` fixture -- needed because a single
    fixture instance cannot be used to build two independent
    ReplayEngine runs (each engine may mutate the config object it's
    given, e.g. `ensure_dirs()`), so this test constructs its own second
    copy rather than requesting a nonexistent `config2` fixture."""
    from datetime import time as _time
    from bujji.core.config import AppConfig
    cfg = AppConfig()
    cfg.timing.orb_start = _time(9, 15)
    cfg.timing.orb_end = _time(9, 20)
    cfg.timing.trading_start = _time(9, 20)
    cfg.timing.trading_end = _time(15, 15)
    cfg.timing.hard_exit = _time(15, 5)
    return cfg


def _replay_config(base_config, tmp_dir, enable_adapter):
    """`base_config` must be the project's own `config` pytest fixture
    (conftest.py) -- it sets `hard_exit`/ORB timing this test suite's
    synthetic candles depend on; a bare `AppConfig()` has different
    timing defaults and produces a different (but not adapter-related)
    trade outcome."""
    cfg = base_config
    cfg.paths.journal_csv = tmp_dir / "j.csv"
    cfg.paths.database = tmp_dir / "b.db"
    cfg.paths.state_file = tmp_dir / "s.json"
    cfg.paths.decision_journal = tmp_dir / "decision_journal.jsonl"
    cfg.paths.ops_restart_count = tmp_dir / "ops_restart_count.json"
    cfg.paths.incident_log = tmp_dir / "incident_log.jsonl"
    cfg.broker.order_timeout_seconds = 0.05
    cfg.broker.poll_interval_seconds = 0.01
    cfg.intelligence_adapter.enabled = enable_adapter
    cfg.intelligence_adapter.consumer_journal_path = REAL_CONSUMER_JOURNAL
    return cfg


def test_feature_flag_defaults_to_disabled():
    from bujji.core.config import AppConfig
    assert AppConfig().intelligence_adapter.enabled is False


@pytest.mark.asyncio
async def test_flag_disabled_produces_no_intelligence_reference(tmp_path, logger, config):
    cfg = _replay_config(config, tmp_path, enable_adapter=False)
    engine = ReplayEngine(cfg, logger)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    await engine.run(candles)
    rows = [json.loads(l) for l in cfg.paths.decision_journal.read_text().splitlines()]
    assert len(rows) == 1
    assert "intelligence_reference" not in rows[0]


@pytest.mark.asyncio
async def test_flag_enabled_adds_only_reference_metadata(tmp_path, logger, config):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    cfg = _replay_config(config, tmp_path, enable_adapter=True)
    engine = ReplayEngine(cfg, logger)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    await engine.run(candles)
    rows = [json.loads(l) for l in cfg.paths.decision_journal.read_text().splitlines()]
    assert len(rows) == 1
    ref = rows[0]["intelligence_reference"]
    assert set(ref.keys()) == {"snapshot_id", "publication_id", "replay_id"}


# ---------------------------------------------------------------------- #
# No behavioural change / byte-identical replay output
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_trading_behaviour_byte_identical_with_flag_on_or_off(tmp_path, logger, config):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 25, 22000, 22040, 21990, 22035, vol=1200),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]

    dir_off = tmp_path / "off"
    dir_on = tmp_path / "on"
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
    assert len(rows_off) == len(rows_on)
    for row_off, row_on in zip(rows_off, rows_on):
        row_on_stripped = dict(row_on)
        row_on_stripped.pop("intelligence_reference", None)
        assert row_off == row_on_stripped


@pytest.mark.asyncio
async def test_missing_journal_file_never_blocks_trading(tmp_path, logger, config):
    """Even with the flag enabled, a missing/unreadable Consumer Journal
    must never affect the trade -- the adapter's own try/except in
    _enter() swallows the failure and journals a None reference."""
    cfg = _replay_config(config, tmp_path, enable_adapter=True)
    cfg.intelligence_adapter.consumer_journal_path = tmp_path / "does_not_exist.jsonl"
    engine = ReplayEngine(cfg, logger)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    result = await engine.run(candles)
    assert result.final_state == "DONE_FOR_DAY"
    assert len(result.trades) == 1


# ---------------------------------------------------------------------- #
# Isolation audit
# ---------------------------------------------------------------------- #
def test_adapter_never_imports_mic_reasoning_engines():
    import ast
    root = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter"
    forbidden_substrings = (
        "mic_v2.fusion", "mic_v2.hypothesis", "mic_v2.candidate", "mic_v2.qualification",
        "mic_v2.memory", "mic_v2.quality", "mic_v2.graph", "mic_v2.counterfactual",
        "mic_v2.consistency", "mic_v2.laboratory", "mic_v2.experiment", "mic_v2.simulation",
        "mic_v2.certification", "mic_v2.registry", "mic_v2.lineage", "mic_v2.compatibility",
        "mic_v2.artifact", "mic_v2.environment", "mic_v2.archive", "mic_v2.diff",
        "mic_v2.explain", "mic_v2.knowledge", "mic_v2.publication", "mic_v2.runtime", "mic_v2.live",
        "mic_v2.replay", "mic_v2.observation_builder", "mic_v2.engine",
    )
    for f in root.rglob("*.py"):
        text = f.read_text()
        for forbidden in forbidden_substrings:
            assert forbidden not in text, f"{f.name} references forbidden MIC v2 module {forbidden}"


def test_adapter_only_imports_consumer_api_surface():
    """The only mic_v2 imports anywhere in this package must be the
    Consumer Journal (the published Consumer API's own durable record,
    Sprint 29) and, as of Integration Series 4 Sprint 1, the Opinion
    Journal (the published Opinion API's own durable record,
    Engineering Series 19 Sprint 1 / Addendum 8) -- a deliberate,
    disclosed extension from one permitted module to exactly two,
    both named explicitly here rather than allowed by a wildcard."""
    import ast
    PERMITTED_MIC_V2_MODULES = ("mic_v2.journal.consumer_journal", "mic_v2.journal.opinion_journal")
    root = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter"
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mic_v2"):
                assert node.module in PERMITTED_MIC_V2_MODULES, (
                    f"{f.name} imports {node.module} -- only {PERMITTED_MIC_V2_MODULES} are permitted"
                )


def test_adapter_never_opens_files_outside_the_consumer_journal_path():
    import ast
    f = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter" / "adapter.py"
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open"), \
            "adapter.py must never open a file directly -- only query.py, via ConsumerJournal, does"


def test_adapter_has_no_order_or_broker_capability():
    import ast
    forbidden = ("order", "buy", "sell", "execute", "trade", "broker", "place_order", "strategy", "risk", "size")
    root = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter"
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name_parts = node.name.lower().split("_")
                for word in forbidden:
                    assert word not in name_parts, f"{f.name} defines suspicious method '{node.name}'"


def test_adapter_never_mutates_loaded_records():
    """No attribute of a LOADED record/snapshot is ever assigned to --
    the only attribute assignments permitted anywhere in this package
    are `self.<own attribute>` inside a class's own methods (ordinary
    object construction), never a mutation of something read from the
    Consumer API."""
    import ast
    root = Path(__file__).resolve().parent.parent / "bujji" / "intelligence" / "mic_adapter"
    for fname in ("adapter.py", "mapper.py", "query.py"):
        f = root / fname
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assert isinstance(target.value, ast.Name) and target.value.id == "self", (
                            f"{fname} mutates a non-self attribute -- forbidden"
                        )
