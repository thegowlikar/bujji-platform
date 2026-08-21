"""Safety tests -- Phase 11 Intelligence Depth Upgrade.

Confirms every new Phase 11 module (market_regime_memory,
market_narrative, context_window, intelligence_completeness's
expansion, intelligence_report) stays strictly observational: no
execution/risk/capital/broker import, no strategy threshold changed,
no forced strategy eligibility, and every module preserves honest
UNKNOWN when evidence is insufficient."""
from __future__ import annotations

import ast
import os
import subprocess

# PaperBroker v2: the ONE authorized change inside the protected trading
# brain -- portfolio_risk_aggregator now distinguishes an empty book's
# genuinely-zero concentration from unknown data, which previously made
# the first trade of every fresh journal unplaceable (RISK_INVALID). Every
# fail-closed path for a NON-empty book is unchanged; see
# tests/test_portfolio_risk_empty_book.py. This guard still fails on any
# OTHER change under the protected packages.
_PAPERBROKER_V2_AUTHORIZED = ("bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",)


_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PHASE11_PACKAGES = (
    "bujji/market_regime_memory",
    "bujji/market_narrative",
    "bujji/context_window",
    "bujji/intelligence_report",
)

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.msi_trade_construction", "bujji.msi_shadow_trading",
    "bujji.mic_replay", "bujji.production_runtime", "fyers_apiv3", "bujji.broker",
)

_FORBIDDEN_ACTION_TERMS = ("place_order", "modify_order", "cancel_order", "execute_trade")


def _all_files(package_rel_path):
    out = []
    abs_path = os.path.join(_REPO_ROOT, package_rel_path)
    for name in os.listdir(abs_path):
        if name.endswith(".py"):
            out.append(os.path.join(abs_path, name))
    return out


def test_no_forbidden_imports_in_any_phase11_package():
    for pkg in _PHASE11_PACKAGES:
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


def test_no_forbidden_action_terms_in_any_phase11_package():
    for pkg in _PHASE11_PACKAGES:
        for path in _all_files(pkg):
            with open(path) as f:
                content = f.read()
            for term in _FORBIDDEN_ACTION_TERMS:
                assert term not in content, f"{path} contains forbidden term {term!r}"


def test_no_strategy_or_execution_packages_modified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_strategy_selector/", "bujji/msi_trade_intent/",
        "bujji/trading_brain/", "bujji/execution_engine/", "bujji/risk_governor/",
    )
    # Phase 14B: msi_trade_intent/engine.py carries its own additive,
    # already-approved exception -- see
    # docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md and
    # tests/test_phase14b_safety.py.
    _phase14b_exception = ("bujji/msi_trade_intent/engine.py",)
    violations = [l for l in changed if any(l.startswith(p) for p in forbidden_prefixes) and l not in _phase14b_exception]
    assert violations == [], f"unexpected strategy/execution-package changes in Phase 11: {violations}"


def test_regime_memory_never_forces_a_transition_on_none():
    """Rule 6/7: honest UNKNOWN/no-reading must never be converted into
    a fabricated regime transition."""
    from bujji.market_regime_memory.models import RegimeMemoryState
    state = RegimeMemoryState().advance("RANGING").advance(None).advance(None)
    assert state.current_regime == "RANGING"
    assert state.total_transitions == 0


def test_completeness_expansion_never_upgrades_status_without_real_input():
    from bujji.intelligence_completeness.engine import evaluate_cycle_extended
    from bujji.intelligence_completeness.models import STATUS_UNKNOWN
    report = evaluate_cycle_extended({})
    for d in report.domains:
        assert d.status == STATUS_UNKNOWN, f"{d.domain} should be UNKNOWN with zero real input, got {d.status}"


def test_intelligence_report_has_no_action_controls():
    from bujji.intelligence_report.engine import build_report_dict, render_report_text
    report = build_report_dict({})
    text = render_report_text(report)
    for forbidden in ("buy", "sell", "place order", "execute", "confirm"):
        assert forbidden not in text.lower()
