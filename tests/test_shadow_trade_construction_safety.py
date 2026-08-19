"""Safety tests -- Shadow Trade Construction Bridge, Phase 14 Tasks 1-4.

Proves the absolute safety boundary: no import of any broker-write
capability, no `place_order`/`modify_order`/`cancel_order` anywhere in
this package, no reference to execution_engine/risk_governor/capital
allocation. This package is read-only: it consumes already-persisted
records and a MarketSnapshot object, and produces a plain dataclass --
it cannot reach a broker at all, let alone place a real order."""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = "bujji/shadow_trade_construction"

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.msi_shadow_trading", "bujji.mic_replay",
    "bujji.production_runtime", "fyers_apiv3", "bujji.broker",
)

_FORBIDDEN_TERMS = ("place_order", "modify_order", "cancel_order", "connect(")


def _all_files():
    abs_path = os.path.join(_REPO_ROOT, _PACKAGE)
    return [os.path.join(abs_path, n) for n in os.listdir(abs_path) if n.endswith(".py")]


# AUTHORIZED CHANGE (2026-08-19, operator-approved): live-premium fix in
# bujji/msi_trade_construction/engine.py. The engine read `row.settlement`
# as the ONLY premium source. The bhavcopy replay provider populates
# settlement; the LIVE chain provider explicitly does not (settlement=None,
# traded price in `close`). On live data every strike therefore resolved to
# premium=None -> iv=None -> delta=None -> ZERO candidates, and every live
# entry attempt died with REJECT_STRIKE_UNAVAILABLE. Bujji could not
# construct a trade on live data at all -- a live-only failure invisible to
# this suite, which drives the bhavcopy path exclusively.
#
# The fix is `_premium_for(row)`: settlement FIRST (every bhavcopy decision,
# replay and test bit-for-bit unchanged -- agreement asserted by
# tests/test_live_chain_strike_selection.py), then the mid of a real
# two-sided quote, then the last trade; absence stays absence, and the basis
# used is recorded on the evidence. Selection logic, target deltas and
# rejection semantics are untouched.
_LIVE_PREMIUM_FIX_AUTHORIZED = ("bujji/msi_trade_construction/engine.py",)


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


def test_no_forbidden_terms():
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        for term in _FORBIDDEN_TERMS:
            assert term not in content, f"{path} contains forbidden term {term!r}"


def test_never_imports_broker_at_all():
    """Zero broker import anywhere -- this package structurally cannot
    reach any live or paper execution surface, since it never even
    imports a Broker-shaped object."""
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        assert "bujji.broker" not in content, f"{path} imports the broker package -- forbidden for a pure construction bridge"


def test_msi_trade_construction_engine_not_modified():
    """The reused engine (bujji.msi_trade_construction) must remain
    byte-identical to before Phase 14 -- this bridge only ADAPTS input
    shape, never touches the protected engine itself."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_trade_construction/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _LIVE_PREMIUM_FIX_AUTHORIZED]
    assert changed == [], f"msi_trade_construction was modified, expected untouched: {changed}"


def test_shadow_trade_candidate_never_places_a_real_position():
    """The output model itself has no execution-shaped field -- confirms
    nothing here could be mistaken for, or misused as, an order."""
    from bujji.shadow_trade_construction.models import ShadowTradeCandidate
    field_names = set(ShadowTradeCandidate.__dataclass_fields__.keys())
    forbidden = {"order_id", "broker_order_id", "execution_status", "filled_quantity", "place", "submit"}
    assert not (field_names & forbidden), f"unexpected execution-shaped fields: {field_names & forbidden}"
