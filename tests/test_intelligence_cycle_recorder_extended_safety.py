"""Safety verification -- Intelligence Cycle Recorder extension
(Strategy Suitability/Selection/Trade Intent), 2026-08-06.

Confirms the recorder still imports only description-producing MSI
engines -- never Trade Construction, Risk Governor, Execution, or any
broker order/position/margin method -- and that all newly-called
engines remain unmodified.
"""
from __future__ import annotations

import subprocess

# PaperBroker v2: the ONE authorized change inside the protected trading
# brain -- portfolio_risk_aggregator now distinguishes an empty book's
# genuinely-zero concentration from unknown data, which previously made
# the first trade of every fresh journal unplaceable (RISK_INVALID). Every
# fail-closed path for a NON-empty book is unchanged; see
# tests/test_portfolio_risk_empty_book.py. This guard still fails on any
# OTHER change under the protected packages.
_PAPERBROKER_V2_AUTHORIZED = ("bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",)

# Lot-size-authoritative fix (2026-08-18): the ONE authorized change to the
# production_runtime lineage since the b148e39 baseline. The 2026-07-19 audit
# (bujji/broker/instrument_master.py module docstring) found the live symbol
# master says NIFTY lot=65 while RuntimeConfig defaulted to 75 and the
# composition root sized from that default. config.py demotes lot_size to an
# optional cross-check; composition_root.py resolves the authoritative value
# from the instrument master and fails closed (CompositionError) when it
# cannot. Covered by tests/test_lot_size_from_master.py. No decision,
# strategy, or execution semantics changed.
_LOT_SIZE_AUTHORITATIVE_AUTHORIZED = (
    "bujji/production_runtime/config.py",
    "bujji/production_runtime/composition_root.py",
)


RECORDER_FILE = "bujji/market_state/intelligence_cycle_recorder.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_trade_construction|msi_shadow_trading|execution_integration|"
    r"broker\b|mic_replay|mic_v2)\b"
)


def _grep(pattern, path, flags="-rnE"):
    return subprocess.run(
        ["grep", flags, pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_no_forbidden_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, RECORDER_FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_order_position_margin_calls():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        RECORDER_FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_strategy_selection_assessment_has_no_execution_shaped_fields():
    from bujji.msi_strategy_selector.models import StrategySelectionAssessment
    field_names = set(StrategySelectionAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_trade_intent_assessment_has_no_execution_shaped_fields():
    from bujji.msi_trade_intent.models import TradeIntentAssessment
    field_names = set(TradeIntentAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_strategy_suitability_assessment_has_no_execution_shaped_fields():
    from bujji.msi_strategy_selection_foundation.models import StrategySuitabilityAssessment
    field_names = set(StrategySuitabilityAssessment.__dataclass_fields__.keys())
    forbidden = {
        "entry", "exit", "strike", "quantity", "order", "capital",
        "risk_approval", "position", "option_type",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_newly_called_engines_unmodified():
    """msi_strategy_selector and msi_trade_intent remain exactly as they
    were when first wired -- this recorder only ever CALLS them.

    msi_strategy_selection_foundation is deliberately EXCLUDED from this
    "fully unmodified" check as of Phase 9's Liquidity Intelligence
    Bridge -- unlike every other bridge built this session, adding a
    real per-call liquidity parameter genuinely required modifying that
    package itself (engine.py's function signatures/logic, taxonomy.py's
    domain lists), not just calling existing unmodified functions. This
    was flagged in advance as a qualitatively different, more invasive
    change and required (and received) separate explicit approval
    before being made -- see test_strategy_selection_foundation_change_
    is_scoped_to_liquidity_bridge below for the narrower guarantee that
    now applies to it instead.

    Phase 14B ADDITION: msi_trade_intent/engine.py was deliberately,
    additively modified (a new `determine_trade_intent_from_selection`
    function; the legacy `determine_trade_intent` is byte-identical --
    see tests/test_phase14b_safety.py and
    docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md). msi_strategy_selector
    remains fully unmodified and is still checked strictly below."""
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--", "bujji/msi_strategy_selector/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"a newly-consumed engine was modified: {result.stdout}"


def test_strategy_selection_foundation_change_is_scoped_to_liquidity_bridge():
    """msi_strategy_selection_foundation WAS deliberately modified
    (Phase 9), but only its two declarative/pure-function files, and
    only those two -- no test files, no other package touched under its
    cover, no execution/order/risk logic introduced."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39", "--", "bujji/msi_strategy_selection_foundation/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = sorted(l for l in result.stdout.strip().splitlines() if l)
    assert changed == [
        "bujji/msi_strategy_selection_foundation/engine.py",
        "bujji/msi_strategy_selection_foundation/taxonomy.py",
    ], f"unexpected file set changed in msi_strategy_selection_foundation: {changed}"


def test_no_protected_execution_lineage_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_trade_construction/", "bujji/risk_governor/", "bujji/execution_engine/",
        "bujji/trading_brain/", "bujji/msi_shadow_trading/", "bujji/mic_replay/",
        "bujji/production_runtime/",
    )
    violations = [l for l in changed if any(l.startswith(p) for p in forbidden_prefixes)]
    assert violations == [], f"forbidden module changes found: {violations}"
