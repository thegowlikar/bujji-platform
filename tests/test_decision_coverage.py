"""Tests for bujji.decision_coverage (Sprint 111)."""
from collections import Counter

from bujji import decision_coverage as dc


def test_classify_buckets_match_spec_exactly():
    assert dc.classify(0) == dc.BUCKET_NEVER_OBSERVED
    assert dc.classify(1) == dc.BUCKET_RARE
    assert dc.classify(4) == dc.BUCKET_RARE
    assert dc.classify(5) == dc.BUCKET_OCCASIONAL
    assert dc.classify(20) == dc.BUCKET_OCCASIONAL
    assert dc.classify(21) == dc.BUCKET_COMMON


def test_coverage_matrix_never_fabricates_a_vocabulary_value():
    """Every entry must trace to a REAL taxonomy value imported from its
    owning frozen module -- never a value this module invents."""
    from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
    matrix = dc.build_coverage_matrix({})
    families_in_matrix = {e.value for e in matrix.by_category("strategy_family")}
    assert families_in_matrix == set(ssf_taxonomy.ALL_STRATEGY_FAMILIES)


def test_coverage_matrix_reports_honest_zero_for_unobserved_values():
    observed = {"strategy_family": Counter({"IRON_CONDOR": 3})}
    matrix = dc.build_coverage_matrix(observed)
    iron_condor = next(e for e in matrix.by_category("strategy_family") if e.value == "IRON_CONDOR")
    ratio = next(e for e in matrix.by_category("strategy_family") if e.value == "RATIO")
    assert iron_condor.count == 3 and iron_condor.bucket == dc.BUCKET_RARE
    assert ratio.count == 0 and ratio.bucket == dc.BUCKET_NEVER_OBSERVED


def test_dead_logic_report_only_lists_never_observed_entries():
    observed = {"strategy_family": Counter({"IRON_CONDOR": 1})}
    matrix = dc.build_coverage_matrix(observed)
    dead = dc.dead_logic_report(matrix)
    assert all(f.value != "IRON_CONDOR" for f in dead if f.category == "strategy_family")
    assert any(f.value == "RATIO" for f in dead if f.category == "strategy_family")


def test_dead_logic_findings_are_categorised_by_kind():
    observed = {}
    matrix = dc.build_coverage_matrix(observed)
    dead = dc.dead_logic_report(matrix)
    kinds = {f.kind for f in dead}
    assert "unused_strategy_family" in kinds
    assert "unused_roll_path" in kinds


def test_reliability_dashboard_reuses_series101_sample_size_by_identity():
    from bujji.msi_performance_analytics import config as paev_config
    matrix = dc.build_coverage_matrix({})
    dashboard = dc.build_reliability_dashboard(shadow_trades_completed=5, evidence_collected_days=41, matrix=matrix)
    assert dashboard.min_reliable_sample_size == paev_config.MIN_RELIABLE_SAMPLE_SIZE
    assert dashboard.statistically_reliable is False  # 5 < 30


def test_recommendation_is_continue_evidence_collection_below_the_floor():
    matrix = dc.build_coverage_matrix({})
    dashboard = dc.build_reliability_dashboard(shadow_trades_completed=3, evidence_collected_days=41, matrix=matrix)
    assert dc.recommend(dashboard) == dc.RECOMMEND_CONTINUE_EVIDENCE_COLLECTION


def test_recommendation_requires_both_sample_size_and_coverage_for_paper_trading():
    # High coverage but too few shadow trades -> still Continue Evidence Collection.
    observed = {cat: Counter({v: 10 for v in vocab}) for cat, vocab in dc.EXPECTED_VOCABULARY.items()}
    matrix = dc.build_coverage_matrix(observed)
    low_trades = dc.build_reliability_dashboard(shadow_trades_completed=1, evidence_collected_days=41, matrix=matrix)
    assert dc.recommend(low_trades) == dc.RECOMMEND_CONTINUE_EVIDENCE_COLLECTION

    high_trades = dc.build_reliability_dashboard(shadow_trades_completed=40, evidence_collected_days=41, matrix=matrix)
    assert dc.recommend(high_trades) in (dc.RECOMMEND_READY_FOR_PAPER_TRADING, dc.RECOMMEND_READY_FOR_CAPITAL_PILOT)


def test_recommendation_capital_pilot_requires_full_coverage():
    vocab_total = sum(len(v) for v in dc.EXPECTED_VOCABULARY.values())
    observed_partial = {cat: Counter({vocab[0]: 25}) for cat, vocab in dc.EXPECTED_VOCABULARY.items()}
    matrix_partial = dc.build_coverage_matrix(observed_partial)
    dashboard_partial = dc.build_reliability_dashboard(shadow_trades_completed=40, evidence_collected_days=41, matrix=matrix_partial)
    assert dashboard_partial.coverage_pct < 100.0
    assert dc.recommend(dashboard_partial) != dc.RECOMMEND_READY_FOR_CAPITAL_PILOT
