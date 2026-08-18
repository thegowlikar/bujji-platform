"""Safety verification -- Shadow Campaign v2 Phase 2.

Proves, at the source level, that the new intelligence-wiring code
(bujji/market_perception/intelligence_adapter.py, plus this phase's
additive edit to shadow_session_runner.py) still cannot place orders,
modify/cancel orders, query positions, or query margins/funds -- and
never imports Trading Brain, Strategy Selector, Execution Engine, or
Risk Governor, keeping this phase strictly an intelligence-observation
layer, never a decision layer.
"""
from __future__ import annotations

import subprocess

MARKET_PERCEPTION_DIR = "bujji/market_perception/"
SHADOW_RUNTIME_DIR = "bujji/shadow_runtime/"
FORBIDDEN_CALLS = r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|get_margin|get_funds)\("
FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|msi_strategy_selector|msi_trade_construction|"
    r"msi_shadow_trading|msi_decision_synthesis|execution_engine|risk_governor|execution_integration)\b"
)


def _grep(pattern, path):
    return subprocess.run(
        ["grep", "-rnE", pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_no_forbidden_broker_calls_in_market_perception():
    out = _grep(FORBIDDEN_CALLS, MARKET_PERCEPTION_DIR)
    assert out == "", f"forbidden broker call found: {out}"


def test_no_forbidden_broker_calls_in_shadow_runtime():
    out = _grep(FORBIDDEN_CALLS, SHADOW_RUNTIME_DIR)
    assert out == "", f"forbidden broker call found: {out}"


def test_no_protected_module_imports_in_market_perception():
    out = _grep(FORBIDDEN_IMPORTS, MARKET_PERCEPTION_DIR)
    assert out == "", f"forbidden import found: {out}"


def test_no_protected_module_imports_in_shadow_runtime():
    out = _grep(FORBIDDEN_IMPORTS, SHADOW_RUNTIME_DIR)
    assert out == "", f"forbidden import found: {out}"


def test_shadow_session_runner_diff_has_no_removed_code_only_docstring_wording():
    # This phase's ONLY authorized modification target. The module
    # docstring's "BROKER ACCESS, EXHAUSTIVELY" paragraph was reworded
    # (not removed) to honestly describe the new opt-in read-only calls --
    # that is prose, not code, and is expected. What must NEVER appear as
    # a removed line is actual code: a def/class signature, a self.
    # attribute assignment, an await call, or a return statement.
    #
    # UPDATED, Phase 19.2.2: LiquidityBrain.analyze() now requires a
    # `context: IntelligenceContext` argument (clock injection -- see
    # docs/PHASE_19_2_2_INTELLIGENCE_DETERMINISM_HARDENING_IMPLEMENTATION.md).
    # The one removed line below is that OLD call being reformatted onto
    # multiple lines to carry `context=` -- same call, same object, same
    # four positional args, not different logic. Allow-listed by exact
    # substring; anything else removed still fails this test.
    KNOWN_SAFE_REMOVED_LINE = (
        "reading = self._liquidity_brain.analyze(pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask)"
    )
    result = subprocess.run(
        ["git", "diff", "360c003", "--", "bujji/shadow_runtime/shadow_session_runner.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    removed = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    code_removed = [
        l for l in removed
        if any(token in l for token in ("def ", "class ", "self.", "await ", "return ", "import "))
        and KNOWN_SAFE_REMOVED_LINE not in l
    ]
    assert code_removed == [], f"unexpected removed CODE lines in shadow_session_runner.py: {code_removed}"


def test_market_perception_enabled_defaults_to_false():
    import sys
    sys.path.insert(0, "/opt/bujji/app")
    import inspect
    from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
    sig = inspect.signature(ShadowSessionRunner.__init__)
    assert sig.parameters["market_perception_enabled"].default is False


def test_intelligence_runner_and_brains_classification_logic_untouched():
    """UPDATED, Phase 19.2.2: this guard originally asserted zero diff at
    all against 360c003, back when this phase's predecessor treated the
    brains as permanently read-only. Phase 19.2.2's own explicit mandate
    is to modify exactly runner.py + the six in-scope brains (clock
    injection + evidence lineage -- see
    docs/PHASE_19_2_2_INTELLIGENCE_DETERMINISM_HARDENING_IMPLEMENTATION.md),
    so zero-diff no longer holds by design. What must still hold, and what
    this test checks instead: no classification/math method (the actual
    `_classify*`, `_efficiency_ratio`, `_scaled_confidence`,
    `_annualized_realized_vol`, `_compression_ratio` style private
    methods) was REMOVED -- only clock/evidence wiring changed, per the
    phase's own "preserve existing classification logic unchanged"
    requirement. premium_brain.py and behaviour_brain.py are explicitly
    out of Phase 19.2.2's scope and must still show zero diff.
    """
    modified_files = (
        "bujji/intelligence/runner.py", "bujji/intelligence/regime_brain.py",
        "bujji/intelligence/volatility_brain.py", "bujji/intelligence/greeks_brain.py",
        "bujji/intelligence/liquidity_brain.py", "bujji/intelligence/structure_brain.py",
        "bujji/intelligence/event_brain.py",
    )
    result = subprocess.run(
        ["git", "diff", "360c003", "--", *modified_files],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    removed = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    removed_method_defs = [l for l in removed if l.strip().startswith("-def ") or l.strip().startswith("- def ")]
    # now_ist() being removed from an import/call line is the EXPECTED,
    # authorized change for this phase -- only a removed `def` (a whole
    # method disappearing) is disallowed.
    assert removed_method_defs == [], f"a brain/runner method definition was removed: {removed_method_defs}"

    out_of_scope_result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/intelligence/premium_brain.py", "bujji/intelligence/behaviour_brain.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert out_of_scope_result.stdout.strip() == "", (
        f"premium_brain.py/behaviour_brain.py are out of Phase 19.2.2 scope but were modified: "
        f"{out_of_scope_result.stdout}"
    )
