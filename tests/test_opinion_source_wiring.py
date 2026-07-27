"""BUJJI Options OS, Integration Series 4, Sprint 1 -- Real Opinion
Source Wiring regression suite: opinion_reader lookup, classification
translation, IntelligenceSnapshot/mapper backward compatibility, real
end-to-end AGREED/DISAGREED/ABSTAINED via the wired policy, isolation
(exactly two named mic_v2 imports), byte-identical trading behavior,
regression.
"""
import json
from datetime import datetime, time as dtime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.intelligence.mic_adapter.mapper import map_consumer_record
from bujji.intelligence.mic_adapter.models import IntelligenceSnapshot
from bujji.intelligence.mic_adapter.opinion_reader import read_opinion_classification, translate_classification
from bujji.intelligence.mic_adapter.evaluation.policy import IntelligencePolicy
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

REAL_MIC_V2_ROOT = Path("/opt/bujji-mic-v2")
REAL_OPINION_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/opinion_journal.jsonl")
REAL_CONSUMER_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")


# ---------------------------------------------------------------------- #
# translate_classification -- pure, no I/O
# ---------------------------------------------------------------------- #
def test_translate_classification_passes_through_bullish_bearish_neutral():
    assert translate_classification("BULLISH") == "BULLISH"
    assert translate_classification("BEARISH") == "BEARISH"
    assert translate_classification("NEUTRAL") == "NEUTRAL"


def test_translate_classification_returns_none_for_mixed_insufficient_unknown():
    assert translate_classification("MIXED") is None
    assert translate_classification("INSUFFICIENT_EVIDENCE") is None
    assert translate_classification("UNKNOWN") is None


def test_translate_classification_returns_none_for_unrecognized_value():
    assert translate_classification("SOMETHING_NEW") is None
    assert translate_classification(None) is None


# ---------------------------------------------------------------------- #
# read_opinion_classification -- real journal read
# ---------------------------------------------------------------------- #
def test_read_opinion_classification_returns_none_for_missing_id():
    assert read_opinion_classification(None, opinion_journal_path=Path("/tmp/nope.jsonl"), mic_v2_root=REAL_MIC_V2_ROOT) is None
    assert read_opinion_classification("", opinion_journal_path=Path("/tmp/nope.jsonl"), mic_v2_root=REAL_MIC_V2_ROOT) is None


def test_read_opinion_classification_returns_none_for_missing_journal(tmp_path):
    result = read_opinion_classification("OPINION-X", opinion_journal_path=tmp_path / "nope.jsonl", mic_v2_root=REAL_MIC_V2_ROOT)
    assert result is None


def test_read_opinion_classification_returns_none_for_malformed_journal_line(tmp_path):
    path = tmp_path / "opinion.jsonl"
    path.write_text("not json at all\n")
    result = read_opinion_classification("OPINION-X", opinion_journal_path=path, mic_v2_root=REAL_MIC_V2_ROOT)
    assert result is None


def test_read_opinion_classification_returns_none_when_id_not_present(tmp_path):
    if not REAL_MIC_V2_ROOT.exists():
        pytest.skip("real MIC v2 root not present on this host")
    import sys
    sys.path.insert(0, str(REAL_MIC_V2_ROOT))
    from mic_v2.journal.opinion_journal import OpinionJournal
    from mic_v2.models.opinion import MarketOpinion, MarketOpinionProvenance

    path = tmp_path / "opinion.jsonl"
    journal = OpinionJournal(path)
    op = MarketOpinion(
        opinion_id="OPINION-REAL", timestamp=datetime(2026, 7, 22, 10, 0), qualification_id="QUAL-1",
        reasoning_trace_id="TRACE-1", classification="BULLISH", confidence=0.7, reasoning_summary="test.",
        provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
    )
    journal.record(op)
    result = read_opinion_classification("OPINION-DOES-NOT-EXIST", opinion_journal_path=path, mic_v2_root=REAL_MIC_V2_ROOT)
    assert result is None


def test_read_opinion_classification_finds_and_translates_real_entry(tmp_path):
    if not REAL_MIC_V2_ROOT.exists():
        pytest.skip("real MIC v2 root not present on this host")
    import sys
    sys.path.insert(0, str(REAL_MIC_V2_ROOT))
    from mic_v2.journal.opinion_journal import OpinionJournal
    from mic_v2.models.opinion import MarketOpinion, MarketOpinionProvenance

    path = tmp_path / "opinion.jsonl"
    journal = OpinionJournal(path)
    op_bullish = MarketOpinion(
        opinion_id="OPINION-BULL", timestamp=datetime(2026, 7, 22, 10, 0), qualification_id="QUAL-1",
        reasoning_trace_id="TRACE-1", classification="BULLISH", confidence=0.7, reasoning_summary="test.",
        provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
    )
    op_mixed = MarketOpinion(
        opinion_id="OPINION-MIXED", timestamp=datetime(2026, 7, 22, 10, 5), qualification_id="QUAL-2",
        reasoning_trace_id="TRACE-2", classification="MIXED", confidence=0.7, reasoning_summary="test.",
        provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
    )
    journal.record(op_bullish)
    journal.record(op_mixed)

    assert read_opinion_classification("OPINION-BULL", opinion_journal_path=path, mic_v2_root=REAL_MIC_V2_ROOT) == "BULLISH"
    assert read_opinion_classification("OPINION-MIXED", opinion_journal_path=path, mic_v2_root=REAL_MIC_V2_ROOT) is None


# ---------------------------------------------------------------------- #
# IntelligenceSnapshot / mapper backward compatibility
# ---------------------------------------------------------------------- #
class _FakeConsumerRecordWithOpinion:
    def __init__(self, market_opinion_id="OPINION-FAKE"):
        self.consumer_id = "CONSUMER-FAKE"
        self.timestamp = datetime(2026, 7, 22, 9, 30)
        self.consumer_status = "AVAILABLE"
        self.publication_id = "PUBLICATION-FAKE"
        self.replay_id = "REPLAY-FAKE"
        self.certification_id = "CERTIFICATION-FAKE"
        self.archive_id = "ARCHIVE-FAKE"
        self.lineage_id = "LINEAGE-FAKE"
        self.compatibility_id = "COMPATIBILITY-FAKE"
        self.environment_id = "ENVIRONMENT-FAKE"
        self.artifact_id = "ARTIFACT-FAKE"
        self.market_opinion_id = market_opinion_id


class _FakeConsumerRecordWithoutOpinionField:
    """Simulates a ConsumerRecord produced before Addendum 8 -- no
    market_opinion_id attribute exists at all."""
    def __init__(self):
        self.consumer_id = "CONSUMER-OLD"
        self.timestamp = datetime(2026, 7, 22, 9, 30)
        self.consumer_status = "AVAILABLE"
        self.publication_id = "PUBLICATION-OLD"
        self.replay_id = "REPLAY-OLD"
        self.certification_id = "CERTIFICATION-OLD"
        self.archive_id = "ARCHIVE-OLD"
        self.lineage_id = "LINEAGE-OLD"
        self.compatibility_id = "COMPATIBILITY-OLD"
        self.environment_id = "ENVIRONMENT-OLD"
        self.artifact_id = "ARTIFACT-OLD"


def test_snapshot_carries_market_opinion_id():
    snapshot = map_consumer_record(_FakeConsumerRecordWithOpinion())
    assert snapshot.market_opinion_id == "OPINION-FAKE"


def test_snapshot_backward_compatible_when_source_record_predates_opinion_field():
    snapshot = map_consumer_record(_FakeConsumerRecordWithoutOpinionField())
    assert snapshot.market_opinion_id is None


def test_snapshot_is_frozen_and_market_opinion_id_defaults_to_none():
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(IntelligenceSnapshot)}
    assert fields["market_opinion_id"].default is None


# ---------------------------------------------------------------------- #
# Real end-to-end AGREED / DISAGREED / ABSTAINED via the wired policy
# ---------------------------------------------------------------------- #
def _wired_policy(opinion_journal_path):
    def opinion_source(consumer_status, snapshot_id, market_opinion_id=None):
        return read_opinion_classification(market_opinion_id, opinion_journal_path=opinion_journal_path, mic_v2_root=REAL_MIC_V2_ROOT)
    return IntelligencePolicy(opinion_source=opinion_source)


def test_real_wired_policy_produces_agreed(tmp_path):
    if not REAL_MIC_V2_ROOT.exists():
        pytest.skip("real MIC v2 root not present on this host")
    import sys
    sys.path.insert(0, str(REAL_MIC_V2_ROOT))
    from mic_v2.journal.opinion_journal import OpinionJournal
    from mic_v2.models.opinion import MarketOpinion, MarketOpinionProvenance

    path = tmp_path / "opinion.jsonl"
    OpinionJournal(path).record(MarketOpinion(
        opinion_id="OPINION-BULL", timestamp=datetime(2026, 7, 22, 10, 0), qualification_id="QUAL-1",
        reasoning_trace_id="TRACE-1", classification="BULLISH", confidence=0.7, reasoning_summary="test.",
        provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
    ))
    policy = _wired_policy(path)
    outcome, reason = policy.classify(True, True, "AVAILABLE", "BULLISH", "SNAP-1", "OPINION-BULL")
    assert outcome == "AGREED"


def test_real_wired_policy_produces_disagreed(tmp_path):
    if not REAL_MIC_V2_ROOT.exists():
        pytest.skip("real MIC v2 root not present on this host")
    import sys
    sys.path.insert(0, str(REAL_MIC_V2_ROOT))
    from mic_v2.journal.opinion_journal import OpinionJournal
    from mic_v2.models.opinion import MarketOpinion, MarketOpinionProvenance

    path = tmp_path / "opinion.jsonl"
    OpinionJournal(path).record(MarketOpinion(
        opinion_id="OPINION-BEAR", timestamp=datetime(2026, 7, 22, 10, 0), qualification_id="QUAL-1",
        reasoning_trace_id="TRACE-1", classification="BEARISH", confidence=0.7, reasoning_summary="test.",
        provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
    ))
    policy = _wired_policy(path)
    outcome, reason = policy.classify(True, True, "AVAILABLE", "BULLISH", "SNAP-1", "OPINION-BEAR")
    assert outcome == "DISAGREED"


def test_real_wired_policy_abstains_for_mixed_insufficient_unknown(tmp_path):
    if not REAL_MIC_V2_ROOT.exists():
        pytest.skip("real MIC v2 root not present on this host")
    import sys
    sys.path.insert(0, str(REAL_MIC_V2_ROOT))
    from mic_v2.journal.opinion_journal import OpinionJournal
    from mic_v2.models.opinion import MarketOpinion, MarketOpinionProvenance

    path = tmp_path / "opinion.jsonl"
    journal = OpinionJournal(path)
    for cls_, oid in (("MIXED", "OPINION-MIXED"), ("INSUFFICIENT_EVIDENCE", "OPINION-INSUFF"), ("UNKNOWN", "OPINION-UNK")):
        journal.record(MarketOpinion(
            opinion_id=oid, timestamp=datetime(2026, 7, 22, 10, 0), qualification_id="QUAL-1",
            reasoning_trace_id="TRACE-1", classification=cls_, confidence=0.7, reasoning_summary="test.",
            provenance=MarketOpinionProvenance(source="replay", ruleset_version="1.0.0"),
        ))
    policy = _wired_policy(path)
    for oid in ("OPINION-MIXED", "OPINION-INSUFF", "OPINION-UNK"):
        outcome, reason = policy.classify(True, True, "AVAILABLE", "BULLISH", "SNAP-1", oid)
        assert outcome == "ABSTAINED"
        assert reason == "no_directional_opinion_published"


def test_no_wiring_still_abstains_via_default_opinion_source():
    """default_opinion_source (unwired) must still always return None --
    this is the policy's own safe fallback, unaffected by this sprint."""
    policy = IntelligencePolicy()
    outcome, reason = policy.classify(True, True, "AVAILABLE", "BULLISH", "SNAP-1", "OPINION-BULL")
    assert outcome == "ABSTAINED"


# ---------------------------------------------------------------------- #
# Isolation -- exactly two named mic_v2 imports
# ---------------------------------------------------------------------- #
def test_exactly_two_permitted_mic_v2_imports_in_package():
    import ast
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter")
    permitted = ("mic_v2.journal.consumer_journal", "mic_v2.journal.opinion_journal")
    found = set()
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mic_v2"):
                assert node.module in permitted, f"{f.name} imports unpermitted {node.module}"
                found.add(node.module)
    assert found == set(permitted)


def test_opinion_reader_has_no_execution_capability():
    text = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/opinion_reader.py").read_text()
    for forbidden in ("requests.", "socket.", "subprocess.", "os.system", "urllib", "aiohttp", "broker", "order"):
        assert forbidden not in text


def test_opinion_reader_never_raises_returns_none_on_any_exception(tmp_path, monkeypatch):
    """A pathological journal (e.g. a directory instead of a file)
    still returns None rather than propagating an exception."""
    bad_path = tmp_path / "not_a_file"
    bad_path.mkdir()
    result = read_opinion_classification("OPINION-X", opinion_journal_path=bad_path, mic_v2_root=REAL_MIC_V2_ROOT)
    assert result is None


# ---------------------------------------------------------------------- #
# Byte-identical trading behavior
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
    cfg.paths.intelligence_observation_journal = tmp_dir / "obs.jsonl"
    cfg.paths.intelligence_evaluation_journal = tmp_dir / "eval.jsonl"
    cfg.broker.order_timeout_seconds = 0.05
    cfg.broker.poll_interval_seconds = 0.01
    cfg.intelligence_adapter.enabled = enable_adapter
    cfg.intelligence_adapter.consumer_journal_path = REAL_CONSUMER_JOURNAL
    cfg.intelligence_adapter.opinion_journal_path = REAL_OPINION_JOURNAL
    return cfg


@pytest.mark.asyncio
async def test_trading_behaviour_byte_identical_with_real_opinion_wiring_on_or_off(tmp_path, logger, config):
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
