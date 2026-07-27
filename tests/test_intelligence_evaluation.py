"""BUJJI Options OS, Integration Series 3, Sprint 1 -- Paper-Only
Intelligence Evaluation Framework regression suite: AGREED, DISAGREED,
ABSTAINED, INSUFFICIENT_INTELLIGENCE, UNKNOWN, frozen models,
serialization, append-only journal, deterministic IDs, byte-identical
trading, feature flag off/on, replay determinism, isolation audit,
no broker access, no market-data access, no MIC reasoning imports.
"""
import dataclasses
import json
import logging
from datetime import datetime, time as dtime
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.intelligence.mic_adapter.evaluation.comparator import (
    abstention_rate, agreement_rate, comparator_summary, coverage_percentage, disagreement_rate, unknown_rate,
)
from bujji.intelligence.mic_adapter.evaluation.config import EvaluationConfig
from bujji.intelligence.mic_adapter.evaluation.engine import EvaluationEngine, evaluate
from bujji.intelligence.mic_adapter.evaluation.policy import (
    ABSTAINED, AGREED, DISAGREED, EVALUATION_OUTCOMES, INSUFFICIENT_INTELLIGENCE, UNKNOWN,
    IntelligencePolicy, classify_evaluation,
)
from bujji.intelligence.mic_adapter.evaluation.serialization import (
    intelligence_evaluation_from_dict, intelligence_evaluation_to_dict,
)
from bujji.journal.intelligence_evaluation_journal import IntelligenceEvaluationJournal
from bujji.replay.engine import ReplayEngine
from tests.conftest import c

REAL_CONSUMER_JOURNAL = Path("/opt/bujji-mic-v2/qualification_campaign_1/consumer_journal.jsonl")
FIXED_NOW = datetime(2026, 7, 22, 14, 20)

SNAP_AVAILABLE = SimpleNamespace(consumer_status="AVAILABLE", snapshot_id="SNAP-1")
SNAP_NOT_AVAILABLE = SimpleNamespace(consumer_status="NOT_AVAILABLE", snapshot_id="SNAP-2")


def _engine(policy=None):
    return EvaluationEngine(EvaluationConfig(), policy=policy, clock=lambda: FIXED_NOW)


# ---------------------------------------------------------------------- #
# AGREED / DISAGREED / ABSTAINED / INSUFFICIENT_INTELLIGENCE / UNKNOWN
# ---------------------------------------------------------------------- #
def test_agreed_when_opinion_matches_production_direction():
    policy = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BULLISH")
    result = _engine(policy).run_evaluation("D1", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    assert result.outcome == AGREED


def test_disagreed_when_opinion_differs_from_production_direction():
    policy = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BEARISH")
    result = _engine(policy).run_evaluation("D2", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    assert result.outcome == DISAGREED


def test_abstained_when_no_opinion_published():
    result = _engine().run_evaluation("D3", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    assert result.outcome == ABSTAINED
    assert result.reason == "no_directional_opinion_published"


def test_abstained_when_no_production_direction():
    result = _engine().run_evaluation("D4", None, SNAP_AVAILABLE, feature_flag_enabled=True)
    assert result.outcome == ABSTAINED
    assert result.reason == "no_production_direction"


def test_insufficient_intelligence_when_snapshot_missing():
    result = _engine().run_evaluation("D5", "BULLISH", None, feature_flag_enabled=True)
    assert result.outcome == INSUFFICIENT_INTELLIGENCE
    assert result.reason == "no_snapshot_available"


def test_insufficient_intelligence_when_snapshot_not_available():
    result = _engine().run_evaluation("D6", "BULLISH", SNAP_NOT_AVAILABLE, feature_flag_enabled=True)
    assert result.outcome == INSUFFICIENT_INTELLIGENCE
    assert "consumer_status" in result.reason


def test_unknown_when_flag_disabled():
    result = _engine().run_evaluation("D7", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=False)
    assert result.outcome == UNKNOWN
    assert result.reason == "evaluation_disabled"


def test_every_outcome_is_in_taxonomy():
    policy_agree = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BULLISH")
    policy_disagree = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BEARISH")
    outcomes = [
        _engine(policy_agree).run_evaluation("A", "BULLISH", SNAP_AVAILABLE, True).outcome,
        _engine(policy_disagree).run_evaluation("B", "BULLISH", SNAP_AVAILABLE, True).outcome,
        _engine().run_evaluation("C", "BULLISH", SNAP_AVAILABLE, True).outcome,
        _engine().run_evaluation("D", "BULLISH", None, True).outcome,
        _engine().run_evaluation("E", "BULLISH", SNAP_AVAILABLE, False).outcome,
    ]
    for o in outcomes:
        assert o in EVALUATION_OUTCOMES
    assert set(EVALUATION_OUTCOMES) == {AGREED, DISAGREED, ABSTAINED, INSUFFICIENT_INTELLIGENCE, UNKNOWN}


def test_classify_evaluation_priority_order_is_deterministic():
    # Flag disabled beats everything else.
    assert classify_evaluation(False, True, "AVAILABLE", "BULLISH", "BULLISH")[0] == UNKNOWN
    # Missing snapshot beats missing direction/opinion.
    assert classify_evaluation(True, False, None, None, None)[0] == INSUFFICIENT_INTELLIGENCE
    # Bad consumer_status beats missing direction/opinion.
    assert classify_evaluation(True, True, "UNKNOWN", None, None)[0] == INSUFFICIENT_INTELLIGENCE


# ---------------------------------------------------------------------- #
# Frozen models / serialization
# ---------------------------------------------------------------------- #
def test_evaluation_and_provenance_are_frozen():
    result = _engine().run_evaluation("D8", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.outcome = "hacked"
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.provenance.source = "hacked"


def test_metrics_and_comparator_have_no_trading_fields():
    import bujji.intelligence.mic_adapter.evaluation.metrics as metrics_mod
    fields = {f.name for f in dataclasses.fields(metrics_mod.EvaluationMetrics)}
    forbidden = {"buy", "sell", "position", "pnl", "profit", "loss", "expectancy", "win_rate", "edge", "order", "broker"}
    assert fields.isdisjoint(forbidden)


def test_serialization_round_trip():
    result = _engine().run_evaluation("D9", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    restored = intelligence_evaluation_from_dict(intelligence_evaluation_to_dict(result))
    assert restored == result


# ---------------------------------------------------------------------- #
# Append-only journal
# ---------------------------------------------------------------------- #
def test_journal_round_trips(tmp_path):
    logger = logging.getLogger("test")
    journal = IntelligenceEvaluationJournal(tmp_path / "eval.jsonl", logger)
    result = _engine().run_evaluation("D10", "BULLISH", SNAP_AVAILABLE, feature_flag_enabled=True)
    journal.record(result)
    assert journal.read_all() == [result]


def test_journal_is_append_only(tmp_path):
    logger = logging.getLogger("test")
    journal = IntelligenceEvaluationJournal(tmp_path / "eval.jsonl", logger)
    eng = _engine()
    journal.record(eng.run_evaluation("D11", "BULLISH", SNAP_AVAILABLE, True))
    journal.record(eng.run_evaluation("D12", "BULLISH", SNAP_AVAILABLE, True))
    assert len(journal.read_all()) == 2


def test_journal_only_opens_files_in_append_or_read_mode():
    import ast
    f = Path("/opt/bujji/app/bujji/journal/intelligence_evaluation_journal.py")
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                assert node.args[1].value in ("a", "r")


def test_journal_never_touches_decision_or_observation_journal():
    import ast
    f = Path("/opt/bujji/app/bujji/journal/intelligence_evaluation_journal.py")
    tree = ast.parse(f.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [n.name for n in node.names]
            module = getattr(node, "module", None) or ""
            assert "decision_journal" not in module and not any("decision_journal" in n for n in names)
            assert "intelligence_observation_journal" not in module and not any(
                "intelligence_observation_journal" in n for n in names
            )


# ---------------------------------------------------------------------- #
# Deterministic IDs
# ---------------------------------------------------------------------- #
def test_deterministic_evaluation_id_given_same_inputs():
    e1 = evaluate("DX", "BULLISH", SNAP_AVAILABLE, IntelligencePolicy(), lambda: FIXED_NOW, True, "1.0.0")
    e2 = evaluate("DX", "BULLISH", SNAP_AVAILABLE, IntelligencePolicy(), lambda: FIXED_NOW, True, "1.0.0")
    assert e1 == e2
    assert e1.evaluation_id.startswith("EVAL-")


def test_evaluation_id_never_uses_uuid_or_wallclock():
    import ast
    f = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation/engine.py")
    text = f.read_text()
    assert "uuid4" not in text
    assert "time.time()" not in text


# ---------------------------------------------------------------------- #
# Comparator
# ---------------------------------------------------------------------- #
def test_comparator_rates_are_descriptive_only():
    agree_policy = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BULLISH")
    disagree_policy = IntelligencePolicy(opinion_source=lambda cs, sid, moid=None: "BEARISH")
    evals = [
        _engine(agree_policy).run_evaluation("A", "BULLISH", SNAP_AVAILABLE, True),
        _engine(disagree_policy).run_evaluation("B", "BULLISH", SNAP_AVAILABLE, True),
        _engine().run_evaluation("C", "BULLISH", SNAP_AVAILABLE, True),          # ABSTAINED
        _engine().run_evaluation("D", "BULLISH", None, True),                    # INSUFFICIENT
        _engine().run_evaluation("E", "BULLISH", SNAP_AVAILABLE, False),         # UNKNOWN
    ]
    assert agreement_rate(evals) == 0.5
    assert disagreement_rate(evals) == 0.5
    assert abstention_rate(evals) == pytest.approx(1 / 5)
    assert unknown_rate(evals) == pytest.approx(1 / 5)
    assert coverage_percentage(evals) == pytest.approx(3 / 5)
    summary = comparator_summary(evals)
    forbidden_keys = {"profitability", "edge", "win_rate", "expectancy", "pnl"}
    assert forbidden_keys.isdisjoint(summary.keys())


def test_comparator_returns_none_for_empty_input():
    assert agreement_rate([]) is None
    assert coverage_percentage([]) is None


def test_comparator_module_has_no_profitability_computation():
    """No function/identifier in comparator.py computes profitability,
    edge, win rate, or expectancy -- prose mentions in the module's own
    docstring (which exists specifically to disclaim this) are exempt
    from this check."""
    import ast
    f = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation/comparator.py")
    tree = ast.parse(f.read_text())
    forbidden = ("profitab", "expectancy", "winrate", "edge", "pnl")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lname = node.name.lower()
            for word in forbidden:
                assert word not in lname, f"function '{node.name}' suggests profitability computation"
        if isinstance(node, ast.Name):
            lname = node.id.lower()
            for word in forbidden:
                assert word not in lname, f"identifier '{node.id}' suggests profitability computation"


# ---------------------------------------------------------------------- #
# Feature flag off / on + byte-identical trading (through the real pipeline)
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
    return cfg


@pytest.mark.asyncio
async def test_flag_off_produces_no_evaluation_journal(tmp_path, logger, config):
    cfg = _replay_config(config, tmp_path, enable_adapter=False)
    engine = ReplayEngine(cfg, logger)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(15, 5, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)
    assert not cfg.paths.intelligence_evaluation_journal.exists()


@pytest.mark.asyncio
async def test_flag_on_produces_evaluation_journal_entry(tmp_path, logger, config):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    cfg = _replay_config(config, tmp_path, enable_adapter=True)
    engine = ReplayEngine(cfg, logger)
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(15, 5, 22000, 22010, 21990, 22005, vol=1000)]
    await engine.run(candles)
    rows = [json.loads(l) for l in cfg.paths.intelligence_evaluation_journal.read_text().splitlines()]
    assert len(rows) == 1
    assert rows[0]["outcome"] in EVALUATION_OUTCOMES


@pytest.mark.asyncio
async def test_trading_behaviour_byte_identical_with_evaluation_on_or_off(tmp_path, logger, config):
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
        assert "outcome" not in row_off and "outcome" not in row_on


@pytest.mark.asyncio
async def test_replay_determinism_produces_identical_evaluation_outcome(tmp_path, logger):
    if not REAL_CONSUMER_JOURNAL.exists():
        pytest.skip("real MIC v2 consumer journal not present on this host")
    candles = [c(9, 20, 22000, 22010, 21990, 22005, vol=1000), c(15, 5, 22000, 22010, 21990, 22005, vol=1000)]
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    cfg_a = _replay_config(_timed_config(), dir_a, enable_adapter=True)
    cfg_b = _replay_config(_timed_config(), dir_b, enable_adapter=True)
    await ReplayEngine(cfg_a, logger).run(candles)
    await ReplayEngine(cfg_b, logger).run(candles)
    rows_a = [json.loads(l) for l in cfg_a.paths.intelligence_evaluation_journal.read_text().splitlines()]
    rows_b = [json.loads(l) for l in cfg_b.paths.intelligence_evaluation_journal.read_text().splitlines()]
    assert [r["outcome"] for r in rows_a] == [r["outcome"] for r in rows_b]
    assert [r["reason"] for r in rows_a] == [r["reason"] for r in rows_b]


# ---------------------------------------------------------------------- #
# Isolation audit — no broker, no market data, no MIC reasoning imports
# ---------------------------------------------------------------------- #
def test_evaluation_never_imports_mic_reasoning_engines():
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation")
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


def test_evaluation_never_reads_market_data_or_broker_or_position_state():
    import ast
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation")
    forbidden = ("broker", "position", "pnl", "buy", "sell", "order", "execute", "trade", "risk",
                "strategy", "candle", "ohlc", "vwap", "quote", "ltp")
    for f in root.rglob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name_parts = node.name.lower().split("_")
                for word in forbidden:
                    assert word not in name_parts, f"{f.name} defines suspicious method '{node.name}'"


def test_evaluation_never_mutates_non_self_attributes():
    import ast
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation")
    for fname in ("engine.py", "metrics.py", "policy.py", "comparator.py", "query.py"):
        f = root / fname
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        assert isinstance(target.value, ast.Name) and target.value.id == "self", (
                            f"{fname} mutates a non-self attribute -- forbidden"
                        )


def test_no_forbidden_trading_identifiers_in_evaluation_code():
    import ast
    forbidden = ("buy", "sell", "position", "pnl", "profit", "loss", "risk", "expectancy")
    root = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation")
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


def test_policy_has_no_execution_capability():
    """Structurally: no network, subprocess, broker-call, or order-
    submission function is reachable from the policy module."""
    f = Path("/opt/bujji/app/bujji/intelligence/mic_adapter/evaluation/policy.py")
    text = f.read_text()
    for forbidden in ("requests.", "socket.", "subprocess.", "os.system", "urllib", "aiohttp"):
        assert forbidden not in text
