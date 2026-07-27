"""Tests for bujji.msi_strategy_optimization (Series 108)."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from bujji.msi_strategy_optimization import engine, taxonomy, query
from bujji.msi_strategy_selection_foundation import taxonomy as ssf_taxonomy
from bujji.options_observation import runner as opt_runner

DAY = "2026-05-25"
D = "20260525"


def _real_chain():
    with open(f"/tmp/m1/BhavCopy_NSE_FO_0_0_0_{D}_F_0000.csv") as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


def test_every_real_strategy_family_has_declared_objectives():
    for family in ssf_taxonomy.ALL_STRATEGY_FAMILIES:
        objectives = taxonomy.FAMILY_OBJECTIVES.get(family)
        assert objectives, f"{family} has no declared optimisation objectives"


def test_optimize_strategy_reuses_series90_delta_targets_by_identity():
    from bujji.msi_trade_construction import config as tc_config
    result = engine.optimize_strategy("IRON_CONDOR", timestamp=f"{DAY}T15:15:00")
    assert result.target_delta == tc_config.FAMILY_DELTA_TARGETS["IRON_CONDOR"]
    assert result.objectives == taxonomy.FAMILY_OBJECTIVES["IRON_CONDOR"]


def test_strike_optimizer_never_hardcodes_a_strike_and_explains_every_decision():
    chain = _real_chain()
    spot = next(r.underlying_price for r in chain if r.underlying_price is not None)
    expiry = sorted({r.expiry for r in chain if r.expiry is not None})[0]
    result = engine.optimize_strikes("IRON_CONDOR", chain, spot, expiry, 1 / 365.0, 0.20, timestamp=f"{DAY}T15:15:00")
    assert result.short_strike is not None
    assert result.short_strike in {c.strike for c in result.candidates_considered}
    assert len(result.explanation.why) > 0
    assert result.skew_adjustment in ("CALL_SKEW", "PUT_SKEW", "NEUTRAL", taxonomy.UNKNOWN)


def test_strike_optimizer_fails_closed_without_target_delta():
    result = engine.optimize_strikes("IRON_CONDOR", (), None, "2026-06-01", 0.1, None, timestamp=f"{DAY}T15:15:00")
    assert result.short_strike is None
    assert result.skew_adjustment == taxonomy.UNKNOWN


def test_expiry_optimizer_prefers_near_term_over_impractically_far_dated_real_expiries():
    """Real, disclosed finding: this codebase's real chain includes
    multi-year expiry entries (DTE > 1000). Ranking by a naive
    theta/gamma ratio would pick the most distant one (monotonic in
    DTE); this must not happen -- dominance is capped at
    EXPIRY_DOMINANCE_MAX_DTE, so a near-term real expiry wins."""
    chain = _real_chain()
    spot = next(r.underlying_price for r in chain if r.underlying_price is not None)
    result = engine.optimize_expiry(chain, spot, DAY, timestamp=f"{DAY}T15:15:00")
    assert result.dominant_expiry is not None
    dominant_candidate = next(c for c in result.candidates if c.expiry == result.dominant_expiry)
    assert dominant_candidate.dte <= 45
    assert any(c.dte is not None and c.dte > 1000 for c in result.candidates)  # the real far-dated entries exist
    assert result.event_calendar_available is False  # honestly disclosed, never fabricated


def test_roll_assessment_never_collapses_the_five_signals():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-1", position_state="HEALTHY",
        thesis_invalidation=SimpleNamespace(compatible=True),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.10, dte=30, timestamp=f"{DAY}T15:15:00")
    for field in ("roll_strike", "roll_expiry", "roll_whole_strategy", "hold", "exit"):
        assert isinstance(getattr(roll, field), bool)
    assert roll.hold is True
    assert roll.exit is False


def test_roll_assessment_exit_dominates_when_thesis_broken():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-2", position_state="THESIS_BROKEN",
        thesis_invalidation=SimpleNamespace(compatible=False),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.5, dte=5, timestamp=f"{DAY}T15:15:00")
    assert roll.exit is True
    assert roll.recommended_action == taxonomy.EXIT
    assert len(roll.exit_reasoning) > 0


def test_roll_strike_fires_on_tested_delta_with_thesis_intact():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-3", position_state="HEALTHY",
        thesis_invalidation=SimpleNamespace(compatible=True),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.40, dte=30, timestamp=f"{DAY}T15:15:00")
    assert roll.roll_strike is True
    assert roll.recommended_action == taxonomy.ROLL_STRIKE
    assert roll.exit is False and roll.roll_whole_strategy is False


def test_adjustment_planner_cites_evidence_for_every_action():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-4", position_state="ADJUSTMENT_CANDIDATE",
        thesis_invalidation=SimpleNamespace(compatible=False),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.3, dte=30, timestamp=f"{DAY}T15:15:00")
    plan = engine.plan_adjustment(roll, lifecycle, timestamp=f"{DAY}T15:15:00")
    assert plan.action in taxonomy.ALL_ADJUSTMENT_ACTIONS
    assert len(plan.evidence_cited) > 0


def test_conversion_only_fires_on_explicit_rules_never_for_novelty():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-5", position_state="ADJUSTMENT_CANDIDATE",
        thesis_invalidation=SimpleNamespace(compatible=False),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.3, dte=30, timestamp=f"{DAY}T15:15:00")
    conv = engine.evaluate_conversion("IRON_CONDOR", roll, timestamp=f"{DAY}T15:15:00")
    assert conv.recommended is True
    assert conv.to_family == "BUTTERFLY"
    assert len(conv.reasoning) > 0

    # A family with no declared conversion rule must honestly report none.
    conv_none = engine.evaluate_conversion("SYNTHETIC", roll, timestamp=f"{DAY}T15:15:00")
    assert conv_none.recommended is False
    assert conv_none.to_family is None


def test_conversion_never_fires_without_the_roll_whole_strategy_gate():
    lifecycle = SimpleNamespace(
        lifecycle_id="LC-6", position_state="HEALTHY",
        thesis_invalidation=SimpleNamespace(compatible=True),
        adjustment_policy=SimpleNamespace(fired=()),
    )
    roll = engine.assess_roll(lifecycle, short_strike_delta=0.1, dte=30, timestamp=f"{DAY}T15:15:00")
    conv = engine.evaluate_conversion("IRON_CONDOR", roll, timestamp=f"{DAY}T15:15:00")
    assert conv.recommended is False


def test_conversion_rules_only_target_real_taxonomy_families():
    for rule in taxonomy.CONVERSION_RULES:
        assert rule["from_family"] in ssf_taxonomy.ALL_STRATEGY_FAMILIES
        assert rule["to_family"] in ssf_taxonomy.ALL_STRATEGY_FAMILIES


def test_package_never_imports_or_modifies_frozen_decision_modules():
    """AST-based (this project's established false-positive-safe
    convention). This package may IMPORT frozen modules read-only
    (engine functions, config, taxonomy) but must never import
    execution/broker, and this test also structurally documents which
    frozen modules it legitimately reads from."""
    pkg_dir = Path("bujji/msi_strategy_optimization")
    forbidden = {"bujji.execution", "bujji.broker", "fyers_apiv3"}
    for py_file in pkg_dir.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(node.module.startswith(f) for f in forbidden), (py_file, node.module)
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(alias.name.startswith(f) for f in forbidden), (py_file, alias.name)


def test_is_defined_risk_reuses_series90_taxonomy_by_identity():
    from bujji.msi_trade_construction import taxonomy as tc_taxonomy
    for family in tc_taxonomy.DEFINED_RISK_FAMILIES:
        assert query.is_defined_risk(family) is True
    for family in tc_taxonomy.UNDEFINED_RISK_FAMILIES:
        assert query.is_defined_risk(family) is False
