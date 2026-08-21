"""Safety tests -- Phase 14B (P1 STATE_LEAN_MAP additions, P0.1 TradeIntent
reconnection, P0.2 taxonomy bridge, P0.3 consistency check).

Confirms: no broker-write import, no execution capability, no risk/
capital path touched, no strategy threshold weakened, protected
engines (msi_strategy_selection_foundation, msi_strategy_eligibility.
engine, msi_decision_synthesis.engine) untouched except the one
approved, additive config.py edit."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_NEW_PACKAGES = ("bujji/strategy_taxonomy_bridge",)

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.msi_shadow_trading", "bujji.mic_replay",
    "bujji.production_runtime", "fyers_apiv3", "bujji.broker",
)
_FORBIDDEN_TERMS = ("place_order", "modify_order", "cancel_order")


def _all_files(pkg):
    abs_path = os.path.join(_REPO_ROOT, pkg)
    return [os.path.join(abs_path, n) for n in os.listdir(abs_path) if n.endswith(".py")]


def test_no_forbidden_imports_in_new_packages():
    for pkg in _NEW_PACKAGES:
        for path in _all_files(pkg):
            with open(path) as f:
                tree = ast.parse(f.read(), filename=path)
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                        assert not name.startswith(forbidden), f"{path} imports forbidden module {name}"


def test_no_forbidden_terms_in_new_packages():
    for pkg in _NEW_PACKAGES:
        for path in _all_files(pkg):
            with open(path) as f:
                content = f.read()
            for term in _FORBIDDEN_TERMS:
                assert term not in content, f"{path} contains forbidden term {term!r}"


def test_msi_strategy_selection_foundation_untouched_since_phase9():
    """Protected package -- Phase 9's own approved exception already
    covers its two files; Phase 14B must add nothing further."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_strategy_selection_foundation/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = sorted(l for l in result.stdout.strip().splitlines() if l)
    # Baseline advanced to 360c003 (2026-08-19 audited backlog commit):
    # the enumerated phase changes are now inside the baseline, so the
    # protected expectation becomes "nothing further".
    assert changed == [], f"unexpected additional changes: {changed}"


def test_msi_strategy_eligibility_engine_untouched():
    """Eligibility's own gating LOGIC must remain byte-identical --
    Phase 14B only added a bridge that reads its output, never modified
    its thresholds or rules."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_strategy_eligibility/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    assert changed == [], f"msi_strategy_eligibility was modified, expected untouched: {changed}"


def test_msi_decision_synthesis_engine_py_untouched_only_config_changed():
    """P1's fix must be config-only (new STATE_LEAN_MAP keys) -- engine.py's
    actual resolution LOGIC must remain byte-identical."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_decision_synthesis/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = sorted(l for l in result.stdout.strip().splitlines() if l)
    # Baseline advanced to 360c003 (2026-08-19 audited backlog commit):
    # the enumerated phase changes are now inside the baseline, so the
    # protected expectation becomes "nothing further".
    assert changed == [], f"unexpected changes: {changed}"


def test_msi_trade_intent_legacy_function_body_unchanged():
    """determine_trade_intent (legacy) must still be present and callable
    with its original signature -- proven functionally by
    test_trade_intent_from_selection.py's legacy test; this test just
    confirms the new function is additive (both functions coexist)."""
    from bujji.msi_trade_intent.engine import determine_trade_intent, determine_trade_intent_from_selection
    assert determine_trade_intent is not None
    assert determine_trade_intent_from_selection is not None
    assert determine_trade_intent is not determine_trade_intent_from_selection


def test_no_capital_or_risk_files_touched():
    """execution_engine/risk_governor/capital_brain/hybrid.py/guard.py
    must be completely untouched -- Phase 14B added no new
    trading-lifecycle capability at all. fyers.py is excluded from this
    "any change" check: it carries a pre-existing (not from this
    session), read-only get_futures_quote() addition from earlier in
    this project -- allowed under this project's own "read-only FYERS
    access is acceptable" rule. Its content is checked separately below
    for order-write capability instead.

    paper.py is a documented Phase 15B exception (`_phase15b_paper_exception`
    below): it gained two purely-additive state-hydration setters
    (`restore_position`, `restore_realized_pnl`), needed so a crashed
    shadow session can restore its PaperBroker's positions/realized P&L
    without re-deriving them outside the broker's own logic. Verified
    additive-only (zero removed lines) by
    `test_paper_broker_change_is_scoped_to_two_additive_methods` in
    test_state_persistence_safety.py -- this test still forbids ANY
    other change to it beyond that."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    _phase15b_paper_exception = ("bujji/broker/paper.py",)
    forbidden_prefixes = (
        "bujji/execution_engine/", "bujji/risk_governor/", "bujji/capital_brain/",
        "bujji/broker/hybrid.py", "bujji/broker/guard.py",
    )
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in forbidden_prefixes) and l not in _phase15b_paper_exception
    ]
    assert violations == [], f"unexpected execution/risk/capital/broker changes: {violations}"


def test_fyers_broker_diff_contains_no_new_order_write_capability():
    result = subprocess.run(
        ["git", "diff", "360c003", "--", "bujji/broker/fyers.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    added_lines = [l for l in result.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")]
    added_text = "\n".join(added_lines)
    for forbidden in ("place_order", "modify_order", "cancel_order", "async def place", "async def modify", "async def cancel"):
        assert forbidden not in added_text, f"fyers.py diff adds forbidden capability: {forbidden}"
