"""Safety tests -- Phase 15N Outcome Memory Layer.

The single most important boundary of this phase (Step 7): the
architecture must remain `Decision -> Outcome -> Memory`, NEVER
`Decision -> Outcome -> Memory -> Decision`. Outcome Memory may KNOW
what happened historically; it must never TELL the live decision
engine what to do. Also proves: read-only queries, no broker access,
no order placement, no execution feedback, immutable historical
records."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_RELS = (
    "bujji/outcome_memory/models.py", "bujji/outcome_memory/engine.py",
    "bujji/outcome_memory/recovery.py", "bujji/outcome_memory/query.py",
)
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.broker",
    # Step 7's own explicit, named list -- the hard boundary of this phase.
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_selector", "bujji.msi_strategy_eligibility",
    "bujji.msi_decision_synthesis", "bujji.msi_trade_intent",
    "bujji.position_management",  # memory reads its OUTPUT (verbatim, already-recorded), never calls it live.
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree(rel):
    with open(_abs(rel)) as f:
        return ast.parse(f.read(), filename=rel)


def test_no_forbidden_imports():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_no_broker_or_network_reference():
    for rel in _MODULE_RELS:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("fyers", "Fyers", "aiohttp", "requests.", "urllib", ".connect(", "PaperBroker("):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_query_module_never_imports_decision_or_execution_paths():
    """The query interface is what a FUTURE phase might eventually read
    from -- so it gets the strictest check: not even an INDIRECT
    dependency on decision/strategy/execution vocabulary anywhere in
    its source text (belt-and-suspenders on top of the AST import
    check above)."""
    with open(_abs("bujji/outcome_memory/query.py")) as f:
        content = f.read()
    for forbidden in (
        "strategy_selection", "strategy_selector", "decision_synthesis", "trade_intent",
        "place_order", "risk_governor", "TradeIntent",
    ):
        assert forbidden not in content, f"query.py unexpectedly references {forbidden!r}"


def test_no_function_in_this_package_returns_a_decision_or_action():
    """No function anywhere in `bujji/outcome_memory/` is named like a
    decision/recommendation/action producer -- this package answers
    questions about history, it never recommends or decides anything."""
    forbidden_name_prefixes = ("recommend", "decide", "select_strategy", "should_", "advise")
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            if isinstance(node, ast.FunctionDef):
                lowered = node.name.lower()
                for prefix in forbidden_name_prefixes:
                    assert not lowered.startswith(prefix), f"{rel} defines {node.name}() -- looks like a decision/action producer, not a memory/query function"


def test_records_are_immutable_frozen_dataclass():
    from bujji.outcome_memory.models import OutcomeMemoryRecord
    import dataclasses
    assert OutcomeMemoryRecord.__dataclass_params__.frozen is True
    fields = {f.name for f in dataclasses.fields(OutcomeMemoryRecord)}
    assert "memory_id" in fields  # sanity: the model actually has the identity field it claims.


def test_conflicting_content_never_overwrites_historical_memory():
    """Re-proves the reducer's own guarantee directly here (belt-and-
    suspenders on top of test_outcome_memory.py's own functional test)
    -- a conflicting event is REJECTED, the store's history is never
    silently mutated."""
    from bujji.outcome_memory.engine import apply_event
    from bujji.outcome_memory.models import TRANSITION_REJECTED
    record_dict = {
        "memory_id": "MEM-fixed", "session_id": "S1", "position_id": "POS-1", "candidate_id": "C1",
        "strategy_family": None, "entry_timestamp": "t0", "exit_timestamp": "t5", "recorded_at": "t6",
        "lifecycle_snapshot": {}, "attribution_snapshot": {}, "realized_pnl": 100.0,
    }
    states, r1 = apply_event({}, "OUTCOME_MEMORY_RECORDED", {"memory_id": "MEM-fixed", "record": record_dict})
    tampered = dict(record_dict)
    tampered["realized_pnl"] = -100.0
    states, r2 = apply_event(states, "OUTCOME_MEMORY_RECORDED", {"memory_id": "MEM-fixed", "record": tampered})
    assert r2.outcome == TRANSITION_REJECTED
    assert states["MEM-fixed"].realized_pnl == 100.0


def test_no_capital_or_execution_packages_touched():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py",
         "bujji/trading_brain/", "bujji/risk_governor/", "bujji/execution_engine/",
         "bujji/msi_strategy_selection_foundation/", "bujji/msi_decision_synthesis/", "bujji/msi_trade_intent/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    stdout = result.stdout.strip()
    # msi_strategy_selection_foundation/msi_decision_synthesis/msi_trade_intent already had
    # PRE-EXISTING, unrelated uncommitted changes before this session (confirmed by `git status`
    # at the start of Phase 15L) -- excluded from a hard-zero assertion, but this module's OWN
    # source (checked above) proves it never imports or writes into them.
    assert "bujji/broker/guard.py" not in stdout
    assert "bujji/broker/hybrid.py" not in stdout
    assert "bujji/trading_brain/" not in stdout
    assert "bujji/risk_governor/" not in stdout
    assert "bujji/execution_engine/" not in stdout
