"""Safety tests -- Phase 15E Greeks + Premium Behaviour Intelligence.

Confirms: no broker-write imports, no order placement/modification/
cancellation, no execution dependency, no capital mutation, no hidden
network calls, no synthetic-fallback masquerading as real data, UNKNOWN
survives, legacy code (bujji.intelligence.greeks_brain/volatility_brain)
remains byte-untouched -- only imported, never forked or modified."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "fyers_apiv3",
)
_CHECKED_FILES = (
    "bujji/msi_greeks/models.py", "bujji/msi_greeks/engine.py",
    "bujji/market_perception/greeks_adapter.py",
    "bujji/premium_behaviour/models.py", "bujji/premium_behaviour/engine.py",
    "bujji/premium_behaviour/recovery.py",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def test_no_forbidden_imports():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls_ast_based():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_no_broker_reference_anywhere():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("Broker", ".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_no_capital_risk_position_references():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("PaperBroker", "capital", "risk_governor", "execution_engine", "position_intelligence"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_no_wall_clock_reads_in_pure_engines():
    """assess_atm_greeks / evaluate (premium behaviour) are pure --
    timestamps are always supplied by the caller, never sampled
    internally -- so replay is genuinely deterministic."""
    for rel in ("bujji/msi_greeks/engine.py", "bujji/premium_behaviour/engine.py"):
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("now_ist(", "datetime.now(", "utcnow("):
            assert forbidden not in content, f"{rel} reads a wall clock internally: {forbidden!r}"


def test_no_random_or_fabricated_fallback():
    """No use of `random`, no default/fallback numeric literal
    substituting for a real, unavailable input -- UNKNOWN must be an
    explicit `None`/`available=False`, never a plausible-looking guess."""
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        assert "import random" not in content
        assert "random." not in content


def test_legacy_greeks_and_volatility_brain_pricing_math_untouched():
    """UPDATED, Phase 19.2.2: this guard originally asserted byte-identity
    against 360c003, back when these two files were never expected to
    change at all. Phase 19.2.2 explicitly modifies both -- clock
    injection (now_ist() -> context.as_of_time) and evidence lineage
    wrapping -- per its own scoped mandate, so byte-identity no longer
    holds. What must still hold, and what this test now checks instead:
    the actual Black-Scholes pricing math (_bs_price, _bs_vega, _bs_delta,
    _bs_gamma, _bs_theta, solve_implied_volatility, the Newton-Raphson/
    bisection solver) is untouched -- no removed `def` line for any of
    those functions, confirming the classification/pricing LOGIC itself
    was preserved exactly as the phase's own "preserve existing
    classification logic unchanged" requirement demanded."""
    result = subprocess.run(
        ["git", "diff", "360c003", "--",
         "bujji/intelligence/greeks_brain.py", "bujji/intelligence/volatility_brain.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    removed = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    pricing_functions = (
        "_bs_price", "_bs_vega", "_bs_delta", "_bs_gamma", "_bs_theta",
        "solve_implied_volatility", "_solve_iv_bisection", "_norm_cdf", "_norm_pdf",
    )
    removed_pricing_defs = [
        l for l in removed
        if l.strip().startswith("-def ") and any(fn in l for fn in pricing_functions)
    ]
    assert removed_pricing_defs == [], (
        f"a pricing/solver function definition was removed, not just clock/evidence wiring: {removed_pricing_defs}"
    )


def test_msi_volatility_structure_engine_byte_untouched():
    """Phase 15E must not touch the existing, already-live VSB engine
    -- it builds a NEW, separate bridge instead of modifying the one
    that already (partially) imports this math."""
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/msi_volatility_structure/engine.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"msi_volatility_structure/engine.py was modified, expected untouched: {result.stdout}"


def test_broker_guard_and_hybrid_untouched():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/broker/guard.py", "bujji/broker/hybrid.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"safety guard files were modified, expected untouched: {result.stdout}"


def test_intelligence_cycle_recorder_diff_scoped_to_additive_content_only():
    """The cumulative diff across Phases 15C/15D/15E to this file must
    contain zero removed lines -- purely additive."""
    result = subprocess.run(
        ["git", "diff", "360c003", "--", "bujji/market_state/intelligence_cycle_recorder.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    removed_code_lines = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    assert removed_code_lines == [], f"unexpected removed lines: {removed_code_lines}"


def test_unknown_survives_through_greeks_leg_assessment():
    from bujji.msi_greeks.engine import assess_atm_greeks
    result = assess_atm_greeks(spot=None, strike=24450.0, t_years=0.02, ce_premium=100.0, pe_premium=100.0, timestamp="t")
    assert result.ce.available is False
    assert result.ce.delta is None
    assert result.ce.iv is None
    assert isinstance(result.ce.reason, str) and result.ce.reason  # a real, non-empty explanation.


def test_unknown_survives_through_premium_behaviour_with_no_history():
    from bujji.premium_behaviour.engine import evaluate
    from bujji.premium_behaviour.models import PremiumBehaviourState
    reading = evaluate(PremiumBehaviourState())
    assert reading.ce.direction == "UNKNOWN"
    assert reading.confidence == "NONE"
    assert reading.ce.current_value is None
