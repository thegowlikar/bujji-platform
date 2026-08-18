"""Safety tests -- State Persistence, Phase 15B.

Confirms: no broker-write import, no place_order/modify_order/cancel_order
call anywhere in this package, hydration never touches FYERS/live
execution, and PaperBroker's guard/hybrid safety boundary is untouched."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = "bujji/state_persistence"

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "fyers_apiv3",
)
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")


def _all_files():
    abs_path = os.path.join(_REPO_ROOT, _PACKAGE)
    return [os.path.join(abs_path, n) for n in os.listdir(abs_path) if n.endswith(".py")]


def test_no_forbidden_imports():
    for path in _all_files():
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


def test_no_forbidden_order_calls():
    """AST-based, not text search -- so a docstring merely DISCUSSING
    `place_order()` (as this package's own module docstrings do, to
    explain why they only ever call the read-only `get_open_positions`/
    `get_realized_pnl`) is not a false positive. Only an actual Call
    node naming one of the forbidden methods counts."""
    for path in _all_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{path} calls forbidden method {name}()"


def test_only_broker_import_is_paper_broker_itself():
    """The only broker this package may reference at all is PaperBroker
    -- never FyersBroker, never HybridPaperBroker's live-data leg."""
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        assert "FyersBroker" not in content
        assert "HybridPaperBroker" not in content


def test_hydration_never_calls_connect():
    """Hydration must work entirely from the persisted file -- zero
    network/broker calls of any kind."""
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        assert ".connect(" not in content


def test_broker_guard_module_untouched():
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--", "bujji/broker/guard.py", "bujji/broker/hybrid.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"safety guard files were modified, expected untouched: {result.stdout}"


def test_phase_15b_hydration_contract_is_still_intact():
    """Phase 15B's hydration contract -- the two additive restore methods
    and the exact in-memory state they write -- must survive every later
    change to paper.py.

    SCOPE CHANGE (PaperBroker v2): this test previously asserted that the
    whole-file `git diff` of paper.py against b148e39 contained NO removed
    lines at all. That was a scope freeze for Phase 15B's own change, and
    it correctly held until a later, explicitly authorized phase modified
    existing behaviour in paper.py on purpose (weighted average cost
    basis, a real cancel_order, and a real used-margin ledger). A
    permanent whole-file freeze would now fail on any future authorized
    edit while proving nothing about hydration itself, so this test is
    re-anchored to the invariant that actually matters and that Phase 15B
    was really protecting: hydration still restores exactly the state it
    always did. The file's other guards (no FyersBroker, no HybridPaperBroker,
    no .connect() during hydration, broker/guard.py untouched, restore
    methods never place an order) are unchanged and still enforced.
    """
    import inspect
    from bujji.broker.paper import PaperBroker

    restore_position_src = inspect.getsource(PaperBroker.restore_position)
    # The caller-supplied entry timestamp is the ONE thing that
    # distinguishes restore_position from seed_position: it restores a
    # position's REAL original entry time rather than stamping "now". If
    # this assignment ever regressed to a wall-clock read, hydration
    # would silently lie about when a recovered position was opened.
    assert '"entry_timestamp": entry_timestamp' in restore_position_src
    for field in ('"symbol"', '"side"', '"qty"', '"avg_price"'):
        assert field in restore_position_src, f"hydration stopped restoring {field}"

    restore_pnl_src = inspect.getsource(PaperBroker.restore_realized_pnl)
    assert "self._realized_pnl[symbol] = amount" in restore_pnl_src


def test_restore_methods_never_place_a_real_order():
    """The new restore_position/restore_realized_pnl methods only
    mutate the broker's own in-memory dicts -- confirmed by reading
    their real source, not by name alone."""
    import inspect
    from bujji.broker.paper import PaperBroker
    source = inspect.getsource(PaperBroker.restore_position) + inspect.getsource(PaperBroker.restore_realized_pnl)
    for forbidden in ("place_order", "_call(", "await "):
        assert forbidden not in source
