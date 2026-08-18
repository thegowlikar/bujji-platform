"""Safety verification -- Continuous Intelligence Observatory.

Confirms the recorder and its wiring into ShadowSessionRunner never
import/call Strategy Selector, Trade Intent, Trade Construction, Risk
Governor, Execution, or any broker order/position/margin method -- and
that every bridge it calls remains unmodified.
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
# production_runtime lineage since the 360c003 baseline. The 2026-07-19 audit
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
RUNNER_FILE = "bujji/shadow_runtime/shadow_session_runner.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    # msi_strategy_selector and msi_trade_intent REMOVED from this list
    # 2026-08-06 -- now legitimately imported (description-only outputs,
    # no execution capability -- see
    # test_intelligence_cycle_recorder_extended_safety.py for their own
    # dedicated, still-strict safety coverage).
    r"msi_trade_construction|"
    r"msi_strategy_eligibility_foundation|msi_shadow_trading|execution_integration|"
    r"broker\b|mic_replay|mic_v2)\b"
)


def _grep(pattern, path, flags="-rnE"):
    return subprocess.run(
        ["grep", flags, pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_no_forbidden_module_imports_in_recorder():
    out = _grep(FORBIDDEN_IMPORTS, RECORDER_FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_order_position_margin_calls_in_recorder():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        RECORDER_FILE,
    )
    assert out == "", f"forbidden call found: {out}"


def test_no_strategy_selector_or_trade_intent_referenced_in_runner():
    out = _grep(
        r"(msi_strategy_selector|msi_trade_intent|msi_trade_construction|risk_governor|execution_engine)",
        RUNNER_FILE,
    )
    assert out == "", f"forbidden reference found in runner: {out}"


def test_no_new_dataclass_defined_recorder_stays_a_thin_orchestrator():
    out = _grep(r"^\s*@dataclass", RECORDER_FILE)
    assert out == "", f"unexpected new dataclass defined: {out}"


# Phase 14B deliberately, additively modified exactly these two files --
# see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md and
# tests/test_phase14b_safety.py.
_PHASE14B_EXCEPTION = (
    "bujji/msi_decision_synthesis/config.py",
    "bujji/msi_trade_intent/engine.py",
)


def test_all_consumed_bridges_and_engines_unmodified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/market_state/direction_bridge.py", "bujji/market_state/synthesizer.py",
         "bujji/market_state/domain_view_adapter.py", "bujji/market_state/trade_thesis_bridge.py",
         "bujji/market_state/strategy_eligibility_bridge.py",
         "bujji/market_perception/msi_adapter.py", "bujji/market_perception/intelligence_adapter.py",
         "bujji/market_state_builder/market_state.py",
         "bujji/msi_consensus/", "bujji/msi_decision_synthesis/",
         "bujji/msi_trade_thesis/", "bujji/msi_strategy_eligibility/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed_files = [l.split("|")[0].strip() for l in result.stdout.strip().splitlines() if l and "|" in l]
    unexpected = [f for f in changed_files if f not in _PHASE14B_EXCEPTION]
    assert unexpected == [], f"a reused bridge/engine was modified: {unexpected}"


def test_no_protected_lineage_package_touched():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED + _LOT_SIZE_AUTHORITATIVE_AUTHORIZED]
    forbidden_prefixes = (
        "bujji/msi_strategy_selector/", "bujji/msi_trade_intent/", "bujji/msi_trade_construction/",
        "bujji/risk_governor/", "bujji/execution_engine/", "bujji/trading_brain/",
        "bujji/msi_shadow_trading/", "bujji/mic_replay/", "bujji/production_runtime/",
    )
    # bujji/broker/ deliberately excluded here -- its only diff
    # (get_futures_quote(), fyers.py) predates this phase and was
    # already approved/verified in Phase 1; this test only guards
    # against THIS phase touching a forbidden lineage.
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in forbidden_prefixes) and l not in _PHASE14B_EXCEPTION
    ]
    assert violations == [], f"forbidden module changes found: {violations}"


def test_shadow_session_runner_diff_has_no_removed_code():
    """UPDATED, Phase 19.2.2: Phase 19.2.2 makes LiquidityBrain.analyze()
    require a new `context: IntelligenceContext` argument (clock injection
    -- see docs/PHASE_19_2_2_INTELLIGENCE_DETERMINISM_HARDENING_IMPLEMENTATION.md).
    Every caller, including this one, must pass it. The one line this
    diff removes is the OLD analyze() call being reformatted onto
    multiple lines to carry the new `context=` kwarg -- the same call, on
    the same object, with the same four positional arguments, not
    different logic. That single known-safe rewrite is allow-listed by
    exact substring below; anything else removed still fails this test."""
    KNOWN_SAFE_REMOVED_LINE = (
        "reading = self._liquidity_brain.analyze(pair.ce_leg.bid, pair.ce_leg.ask, pair.pe_leg.bid, pair.pe_leg.ask)"
    )
    result = subprocess.run(
        ["git", "diff", "360c003", "--", RUNNER_FILE],
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
    assert code_removed == [], f"unexpected removed CODE lines: {code_removed}"


def test_intelligence_cycle_enabled_defaults_to_false():
    import sys
    sys.path.insert(0, "/opt/bujji/app")
    import inspect
    from bujji.shadow_runtime.shadow_session_runner import ShadowSessionRunner
    sig = inspect.signature(ShadowSessionRunner.__init__)
    assert sig.parameters["intelligence_cycle_enabled"].default is False
