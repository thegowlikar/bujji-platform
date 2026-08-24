"""Tests for the offline quant research package.

The six NEGATIVE CONTROLS at the end are the point of this file. Each plants a
specific defect that would corrupt a research result and asserts the
architecture refuses it. A control that passes when the defect is present is a
vacuous test, and this project has shipped three of those before -- so each
control here also asserts the UNDEFECTED case passes, which is what makes the
refusal attributable to the defect rather than to anything else.
"""
from __future__ import annotations

import json
import math
import pytest

from bujji.quant_research.manifest import (
    PHASE_INVALID, PHASE_UNMEASURED, PHASE_VALID, DatasetManifest, PhaseWindow,
    REFUSE_HASH_MISMATCH, REFUSE_INVALID_PHASE_INCLUDED,
    REFUSE_NO_PHASE_CLASSIFICATION, REFUSE_NO_UNIVERSE_IDENTITY,
    REFUSE_SOURCE_MISSING, REFUSE_UNMEASURED_PHASE_INCLUDED,
    sha256_file, verify_manifest)
from bujji.quant_research.dataset import (
    DatasetRefused, LookAheadError, PointInTime, SOURCE_REST_CHAIN, SOURCE_TICK,
    TickDataset, TickRecord, label_forward_outcome)
from bujji.quant_research.diagnostics import CorpusDiagnostics, classify_symbol
from bujji.quant_research.evaluation import (
    ExperimentResult, ExperimentSpec, GATE_INSUFFICIENT_OBSERVATIONS,
    GATE_LTP_ONLY_PNL, GATE_MULTIPLE_TESTING, GATE_NO_ACCEPTANCE_CRITERION,
    GATE_SINGLE_SESSION, VERDICT_INSUFFICIENT, VERDICT_NOT_REJECTED,
    deflated_sharpe, evaluate, expected_max_sharpe)

from pathlib import Path


# ---------------------------------------------------------------- fixtures
def _write_corpus(path: Path, rows) -> str:
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    return sha256_file(path)


def _rows(n=40, t0=1000.0, step=10.0, symbol="NSE:X25CE"):
    return [{"seq": i, "recv_ts": t0 + i * step,
             "payload": {"symbol": symbol, "ltp": 100.0 + i,
                         "bid_price": 99.0 + i, "ask_price": 101.0 + i,
                         "bid_size": 50, "ask_size": 50}}
            for i in range(n)]


@pytest.fixture
def corpus(tmp_path):
    p = tmp_path / "c.jsonl"
    h = _write_corpus(p, _rows())
    return p, h


@pytest.fixture
def manifest(corpus):
    p, h = corpus
    return DatasetManifest(
        dataset_id="d1", session_date="2026-08-24", sources={str(p): h},
        universe_id="u1",
        phases=[PhaseWindow(name="whole", classification=PHASE_VALID)])


# ------------------------------------------------------- dataset identity
def test_clean_manifest_verifies(manifest):
    assert verify_manifest(manifest) == []


def test_fingerprint_is_stable(manifest):
    assert manifest.fingerprint() == manifest.fingerprint()


def test_fingerprint_changes_with_inclusion_rule(manifest):
    before = manifest.fingerprint()
    manifest.included_classifications = (PHASE_VALID, PHASE_UNMEASURED)
    assert manifest.fingerprint() != before, (
        "the same bytes filtered differently are a different dataset")


def test_missing_source_refused(manifest):
    manifest.sources = {"/nonexistent/x.jsonl": "0" * 64}
    codes = {r["code"] for r in verify_manifest(manifest)}
    assert REFUSE_SOURCE_MISSING in codes


def test_no_universe_identity_refused(manifest):
    manifest.universe_id = None
    manifest.universe_sha256 = None
    assert REFUSE_NO_UNIVERSE_IDENTITY in {
        r["code"] for r in verify_manifest(manifest)}


def test_no_phase_classification_refused(manifest):
    manifest.phases = []
    assert REFUSE_NO_PHASE_CLASSIFICATION in {
        r["code"] for r in verify_manifest(manifest)}


def test_unknown_classification_refused(manifest):
    manifest.phases = [PhaseWindow(name="w", classification="PROBABLY_FINE")]
    assert REFUSE_NO_PHASE_CLASSIFICATION in {
        r["code"] for r in verify_manifest(manifest)}


# -------------------------------------------------------- ordering / access
def test_stream_is_event_ordered(manifest):
    ts = [r.recv_ts for r in TickDataset(manifest).stream()]
    assert ts == sorted(ts)


def test_until_ts_is_inclusive_and_bounded(manifest):
    ds = TickDataset(manifest)
    got = list(ds.stream(until_ts=1100.0))
    assert got and all(r.recv_ts <= 1100.0 for r in got)
    assert len(got) < len(list(ds.stream()))


def test_absent_field_is_none_never_zero():
    r = TickRecord(seq=1, recv_ts=1.0, symbol="S", payload={"ltp": 5.0})
    assert r.field("bid_price") is None
    assert r.field("bid_price") != 0


def test_point_in_time_has_no_future(manifest):
    pit = TickDataset(manifest).at(1100.0)
    with pytest.raises(LookAheadError):
        pit.future()


def test_point_in_time_history_stops_at_now(manifest):
    pit = TickDataset(manifest).at(1100.0)
    assert all(r.recv_ts <= pit.now for r in pit.history())


def test_label_is_marked_as_forward_looking(manifest):
    lab = label_forward_outcome(TickDataset(manifest), "NSE:X25CE", 1100.0, 100.0)
    assert lab["kind"] == "LABEL"
    assert "never be used as a feature" in lab["warning"]


def test_tick_and_rest_provenance_are_distinct():
    assert SOURCE_TICK != SOURCE_REST_CHAIN


# ------------------------------------------------------------- diagnostics
def test_classify_symbol():
    assert classify_symbol("NSE:NIFTY25000CE") == "OPTION"
    assert classify_symbol("NSE:NIFTY-INDEX") == "INDEX"
    assert classify_symbol("NSE:NIFTYFUT") == "FUTURE"
    assert classify_symbol("X", {"type": "if"}) == "INDEX"


def test_diagnostics_report_carries_denominators(manifest):
    rep = CorpusDiagnostics(TickDataset(manifest)).report()
    assert rep["coverage"]["records"] == 40
    assert rep["liquidity"]["OPTION"]["relative_spread"]["n"] > 0
    assert rep["authorizes"].startswith("NOTHING")


def test_diagnostics_reports_insufficient_rather_than_fabricating(tmp_path):
    """A corpus with no quotes must say so, not report a spread of zero."""
    p = tmp_path / "ltp.jsonl"
    rows = [{"seq": i, "recv_ts": 1000.0 + i * 10,
             "payload": {"symbol": "NSE:X25CE", "ltp": 100.0 + i}}
            for i in range(20)]
    h = _write_corpus(p, rows)
    m = DatasetManifest(dataset_id="d", session_date="2026-08-24",
                        sources={str(p): h}, universe_id="u",
                        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    liq = CorpusDiagnostics(TickDataset(m)).report()["liquidity"]["OPTION"]
    assert liq["relative_spread"]["status"] == "INSUFFICIENT_DATA"
    assert liq["relative_spread"]["n"] == 0
    assert "median" not in liq["relative_spread"]


def test_crossed_book_excluded_from_spread(tmp_path):
    p = tmp_path / "x.jsonl"
    rows = [{"seq": 0, "recv_ts": 1.0, "payload": {
        "symbol": "NSE:X25CE", "bid_price": 105.0, "ask_price": 95.0}}]
    h = _write_corpus(p, rows)
    m = DatasetManifest(dataset_id="d", session_date="s", sources={str(p): h},
                        universe_id="u",
                        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    liq = CorpusDiagnostics(TickDataset(m)).report()["liquidity"]["OPTION"]
    assert liq["crossed_quotes"] == 1
    assert liq["relative_spread"]["n"] == 0, "a crossed book is not a spread"


def _computed_only(obj):
    """Strip explanatory prose, keeping only measured values.

    A key naming a note, reason, limit, basis or detail holds prose ABOUT the
    measurement, not the measurement. Scanning it for banned vocabulary makes
    the test match the very disclaimer whose job is to deny the thing -- a
    defect this project has now produced four times, so the fix is a rule
    rather than another special case.
    """
    prose = ("note", "reason", "limit", "basis", "detail", "warning",
             "authorizes", "status")
    if isinstance(obj, dict):
        return {k: _computed_only(v) for k, v in obj.items()
                if not any(w in k.lower() for w in prose)}
    if isinstance(obj, list):
        return [_computed_only(v) for v in obj]
    return obj


def test_feasibility_never_claims_profit(manifest):
    f = CorpusDiagnostics(TickDataset(manifest)).report()["method_feasibility"]
    blob = json.dumps(_computed_only(f)).lower()
    assert "measured_inputs" in blob, "anchor: computed content survives the strip"
    for word in ("profitable", "edge", "alpha", "recommend"):
        assert word not in blob


# -------------------------------------------------------------- evaluation
def test_deflation_rises_with_trials():
    v = 1 / 999.0
    vals = [expected_max_sharpe(n, v) for n in (1, 10, 100, 1000)]
    assert vals == sorted(vals) and vals[0] == 0.0


def test_deflation_falls_as_trials_rise():
    p = [deflated_sharpe(0.08, 1000, n) for n in (1, 10, 100, 1000)]
    assert p == sorted(p, reverse=True)


def test_deflation_rises_with_sharpe():
    p = [deflated_sharpe(sr, 1000, 10) for sr in (0.02, 0.05, 0.10)]
    assert p == sorted(p)


def test_deflation_is_not_vacuous():
    """A genuinely strong single-trial result must be able to pass.

    Guards the failure this file found during development: an omitted sqrt(V)
    scaling made every result fail, which reads as rigour and is actually a
    broken comparison between two different units.
    """
    assert deflated_sharpe(0.12, 1000, 1) > 0.95


def test_verdict_vocabulary_never_says_profitable():
    spec = ExperimentSpec(name="e", hypothesis="h", dataset_fingerprint="f",
                          feature_version="v", policy_version="p")
    out = evaluate(spec, ExperimentResult())
    assert out["verdict"] == VERDICT_INSUFFICIENT
    assert "profit" not in json.dumps(out).lower().replace(
        "not a profitability claim", "")


def test_default_experiment_fails_many_gates():
    spec = ExperimentSpec(name="e", hypothesis="h", dataset_fingerprint="f",
                          feature_version="v", policy_version="p")
    out = evaluate(spec, ExperimentResult())
    codes = {g["code"] for g in out["failed_gates"]}
    assert GATE_NO_ACCEPTANCE_CRITERION in codes
    assert GATE_INSUFFICIENT_OBSERVATIONS in codes
    assert GATE_SINGLE_SESSION in codes


def _passing_pair():
    spec = ExperimentSpec(
        name="e", hypothesis="h", dataset_fingerprint="f", feature_version="v",
        policy_version="p", acceptance_criterion="DSR > 0.95 out of sample",
        trials_run=1, baselines=["always_flat"],
        cost_model={"fee_bps": 5, "spread": "modelled"}, seed=7)
    res = ExperimentResult(
        observations=500, distinct_sessions=30, sharpe=0.12,
        max_drawdown=-0.1, worst_observation=-0.05,
        baseline_sharpes={"always_flat": 0.0}, used_quotes_for_fills=True,
        out_of_sample_after_freeze=True, leakage_checked=True,
        stability={"by_month": "consistent"})
    return spec, res


def test_a_complete_experiment_reaches_not_rejected():
    """The A side of the A/B: without this, every gate test is vacuous."""
    out = evaluate(*_passing_pair())
    assert out["failed_gates"] == [], out["failed_gates"]
    assert out["verdict"] == VERDICT_NOT_REJECTED
    assert "authorizes no trading" in out["note"]


# =========================================================================
# NEGATIVE CONTROLS. Each must FIRE.
# =========================================================================
def test_control_1_future_tick_leaked_into_feature(manifest):
    """Reading past the decision instant must be structurally impossible."""
    ds = TickDataset(manifest)
    pit = ds.at(1100.0)
    assert list(pit.history()), "control anchor: history must be non-empty"
    with pytest.raises(LookAheadError):
        pit.future(horizon=60)


def test_control_2_invalid_phase_included(corpus):
    """The INVALID full-sustained phase must not be admissible as evidence."""
    p, h = corpus
    ok = DatasetManifest(
        dataset_id="d", session_date="s", sources={str(p): h}, universe_id="u",
        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    assert verify_manifest(ok) == [], "control anchor: clean manifest passes"

    bad = DatasetManifest(
        dataset_id="d", session_date="s", sources={str(p): h}, universe_id="u",
        phases=[PhaseWindow(name="full_sustained", classification=PHASE_INVALID)],
        included_classifications=(PHASE_VALID, PHASE_INVALID))
    assert REFUSE_INVALID_PHASE_INCLUDED in {
        r["code"] for r in verify_manifest(bad)}
    with pytest.raises(DatasetRefused):
        TickDataset(bad)


def test_control_2b_unmeasured_phase_included(corpus):
    p, h = corpus
    bad = DatasetManifest(
        dataset_id="d", session_date="s", sources={str(p): h}, universe_id="u",
        phases=[PhaseWindow(name="gap", classification=PHASE_UNMEASURED)],
        included_classifications=(PHASE_VALID, PHASE_UNMEASURED))
    assert REFUSE_UNMEASURED_PHASE_INCLUDED in {
        r["code"] for r in verify_manifest(bad)}


def test_control_3_rest_oi_used_as_tick_time_fact():
    """A REST-chain fact must never be presented on the tick timeline."""
    tick = TickRecord(seq=1, recv_ts=100.0, symbol="S", payload={"ltp": 1.0})
    assert tick.source == SOURCE_TICK, "control anchor: ticks are tick-sourced"

    oi = TickRecord(seq=None, recv_ts=None, symbol="S",
                    payload={"open_interest": 500}, source=SOURCE_REST_CHAIN)
    assert oi.source != tick.source
    assert oi.recv_ts is None, (
        "a chain snapshot has a fetch time, not a receiver tick time; giving "
        "it one would make it falsely synchronous with the tick stream")


def test_control_4_pnl_from_ltp_without_quotes():
    spec, res = _passing_pair()
    assert evaluate(spec, res)["failed_gates"] == [], "control anchor"

    res.used_quotes_for_fills = False
    res.used_ltp_for_fills = True
    out = evaluate(spec, res)
    assert GATE_LTP_ONLY_PNL in {g["code"] for g in out["failed_gates"]}
    assert out["verdict"] == VERDICT_INSUFFICIENT


def test_control_5_source_changed_without_manifest_change(tmp_path):
    """Mutating the bytes behind a declared hash must refuse the dataset."""
    p = tmp_path / "c.jsonl"
    h = _write_corpus(p, _rows())
    m = DatasetManifest(
        dataset_id="d", session_date="s", sources={str(p): h}, universe_id="u",
        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    assert verify_manifest(m) == [], "control anchor: unmodified file passes"

    _write_corpus(p, _rows(n=41))          # same manifest, different bytes
    assert REFUSE_HASH_MISMATCH in {r["code"] for r in verify_manifest(m)}
    with pytest.raises(DatasetRefused):
        TickDataset(m)


def test_control_6_one_day_in_sample_result_called_profitable():
    spec, res = _passing_pair()
    assert evaluate(spec, res)["verdict"] == VERDICT_NOT_REJECTED, "anchor"

    res.distinct_sessions = 1
    res.observations = 40
    res.out_of_sample_after_freeze = False
    spec.trials_run = 200
    out = evaluate(spec, res)
    codes = {g["code"] for g in out["failed_gates"]}
    assert GATE_SINGLE_SESSION in codes
    assert GATE_INSUFFICIENT_OBSERVATIONS in codes
    assert GATE_MULTIPLE_TESTING in codes
    assert out["verdict"] != VERDICT_NOT_REJECTED


def test_control_7_out_of_order_corpus_refused(tmp_path):
    """Event order is asserted, not assumed."""
    p = tmp_path / "bad.jsonl"
    rows = _rows(n=5)
    rows[3]["seq"] = 0                      # sequence goes backwards
    h = _write_corpus(p, rows)
    m = DatasetManifest(
        dataset_id="d", session_date="s", sources={str(p): h}, universe_id="u",
        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    with pytest.raises(LookAheadError):
        list(TickDataset(m).stream())


# ------------------------------------------------------ offline by reachability
def test_quant_research_is_offline_by_reachability():
    """Not offline because a docstring says so.

    In this codebase "offline" in a docstring has meant reachable-and-simply-
    not-called-yet more than once, so this asserts the closure directly.
    """
    import subprocess
    import sys
    r = json.loads(subprocess.run(
        [sys.executable, "tools/reachability.py", "--json"],
        cwd=str(Path(__file__).resolve().parent.parent),
        capture_output=True, text=True).stdout)
    assert r["reachable_modules"], "anchor: the closure is non-empty"
    leaked = sorted(m for m in r["reachable_modules"]
                    if m.startswith("bujji.quant_research"))
    assert not leaked, (
        f"quant_research reachable from a declared entrypoint: {leaked}")


# ============================================ features: provenance and joins
from bujji.quant_research.features import (  # noqa: E402
    ChainFeatureRow, FeatureRow, FeatureValue, StalenessUnknown,
    join_with_staleness, tick_features_at)
from bujji.quant_research.ledger import (  # noqa: E402
    ExperimentLedger, LedgerRefused)


def test_every_feature_carries_source_and_timestamp(manifest):
    row = tick_features_at(TickDataset(manifest), "NSE:X25CE", 1200.0)
    assert row.values
    for name, fv in row.values.items():
        assert fv.source == SOURCE_TICK, name
        assert fv.computed_at_ts == 1200.0, name


def test_feature_row_refuses_a_future_observation():
    row = FeatureRow(symbol="S", as_of_ts=100.0)
    with pytest.raises(LookAheadError):
        row.add(FeatureValue("f", 1.0, SOURCE_TICK, observed_ts=101.0,
                             computed_at_ts=100.0))


def test_missing_spread_is_none_not_zero(tmp_path):
    p = tmp_path / "n.jsonl"
    rows = [{"seq": i, "recv_ts": 1000.0 + i,
             "payload": {"symbol": "S", "ltp": 10.0}} for i in range(5)]
    h = _write_corpus(p, rows)
    m = DatasetManifest(dataset_id="d", session_date="s", sources={str(p): h},
                        universe_id="u",
                        phases=[PhaseWindow(name="w", classification=PHASE_VALID)])
    row = tick_features_at(TickDataset(m), "S", 1010.0)
    assert row.values["mean_relative_spread"].value is None
    assert row.values["mean_relative_spread"].value != 0


def test_tick_and_chain_rows_are_different_types():
    assert FeatureRow is not ChainFeatureRow


def test_join_records_the_chain_age():
    tick = FeatureRow(symbol="S", as_of_ts=1000.0)
    chain = ChainFeatureRow(symbol="S", snapshot_ts=940.0,
                            values={"oi": FeatureValue(
                                "oi", 500.0, SOURCE_REST_CHAIN, 940.0, 940.0)})
    out = join_with_staleness(tick, chain, max_staleness_s=120.0)
    assert out["chain_age_s"] == 60.0
    assert out["chain_admitted"] is True
    assert out["chain_snapshot_ts"] == 940.0


def test_join_drops_an_over_stale_chain_fact():
    tick = FeatureRow(symbol="S", as_of_ts=1000.0)
    chain = ChainFeatureRow(symbol="S", snapshot_ts=100.0,
                            values={"oi": FeatureValue(
                                "oi", 500.0, SOURCE_REST_CHAIN, 100.0, 100.0)})
    out = join_with_staleness(tick, chain, max_staleness_s=120.0)
    assert out["chain_admitted"] is False
    assert out["chain_features"] is None


def test_control_8_chain_fact_joined_without_a_timestamp():
    """An unknown age must refuse, not default to zero."""
    tick = FeatureRow(symbol="S", as_of_ts=1000.0)
    ok = ChainFeatureRow(symbol="S", snapshot_ts=990.0)
    assert join_with_staleness(tick, ok, max_staleness_s=60.0)["chain_age_s"] == 10.0

    bad = ChainFeatureRow(symbol="S", snapshot_ts=None)
    with pytest.raises(StalenessUnknown):
        join_with_staleness(tick, bad, max_staleness_s=60.0)


def test_control_9_chain_snapshot_from_the_future():
    tick = FeatureRow(symbol="S", as_of_ts=1000.0)
    future = ChainFeatureRow(symbol="S", snapshot_ts=1500.0)
    with pytest.raises(LookAheadError):
        join_with_staleness(tick, future, max_staleness_s=10_000.0)


# ------------------------------------------------------------------ ledger
def _eval_blob(name="e", hypothesis="h", verdict="INSUFFICIENT_EVIDENCE"):
    return {"experiment": name, "hypothesis": hypothesis,
            "dataset_fingerprint": "f", "feature_version": "v",
            "policy_version": "p", "verdict": verdict}


def test_ledger_counts_every_trial_including_failures(tmp_path):
    led = ExperimentLedger(tmp_path / "l.jsonl")
    for i in range(5):
        led.record(_eval_blob(name=f"e{i}"), recorded_at_ts=float(i))
    assert led.trials_for("h") == 5
    assert led.summary()["total_experiments"] == 5


def test_ledger_refuses_an_unreproducible_result(tmp_path):
    led = ExperimentLedger(tmp_path / "l.jsonl")
    blob = _eval_blob()
    del blob["dataset_fingerprint"]
    with pytest.raises(LedgerRefused):
        led.record(blob, recorded_at_ts=1.0)


def test_ledger_is_append_only(tmp_path):
    led = ExperimentLedger(tmp_path / "l.jsonl")
    led.record(_eval_blob(name="a"), recorded_at_ts=1.0)
    led.record(_eval_blob(name="b"), recorded_at_ts=2.0)
    names = [e["experiment"] for e in led.entries()]
    assert names == ["a", "b"]


def test_control_10_hidden_failed_trials_understate_the_bar(tmp_path):
    """Suppressing failures makes the deflation too weak. Show it numerically."""
    led = ExperimentLedger(tmp_path / "l.jsonl")
    for i in range(50):
        led.record(_eval_blob(name=f"e{i}"), recorded_at_ts=float(i))
    honest = led.trials_for("h")
    assert honest == 50

    dishonest = 1                      # "we only report the one that worked"
    p_honest = deflated_sharpe(0.08, 1000, honest)
    p_dishonest = deflated_sharpe(0.08, 1000, dishonest)
    assert p_dishonest > p_honest, (
        "hiding failed trials must make a result look better; if it did not, "
        "the deflation would not be doing anything")
    assert p_dishonest > 0.95 and p_honest < 0.95, (
        "the same Sharpe should flip from passing to failing once the true "
        "trial count is used")
