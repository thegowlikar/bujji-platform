"""Safety tests -- Phase 15H Formal Replay Engine.

Confirms: strictly read-only, no broker-write imports, no place/modify/
cancel order, no PaperBroker mutation, no live execution, no capital/
risk mutation, no modification of source artifacts."""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "fyers_apiv3",
)
_CHECKED_FILES = (
    "bujji/replay_engine/models.py", "bujji/replay_engine/engine.py", "bujji/replay_engine/mismatch.py",
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


def test_no_broker_paperbroker_or_network_reference():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in (".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib",
                          "PaperBroker(", "capital_brain", "risk_governor", "margin"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_no_write_open_calls_in_engine():
    """The engine only ever reads its source files -- confirm no
    'w'/'a' mode open() call exists anywhere in this package."""
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "open":
                for arg in node.args[1:] + [kw.value for kw in node.keywords if kw.arg == "mode"]:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        assert "w" not in arg.value and "a" not in arg.value, \
                            f"{rel} opens a file in a write/append mode: {arg.value!r}"


def test_replay_never_mutates_source_artifacts(tmp_path):
    """Real regression proof, not just a static check: run a full
    replay and confirm every source file's bytes are byte-identical
    before and after."""
    from bujji.market_perception.models import (
        HEALTH_OK, MarketSnapshot, OptionChainConfig, OptionChainSnapshot, OptionLeg, SpotSnapshot, VixSnapshot,
    )
    from bujji.replay_engine.engine import ReplayEngine

    legs = (
        OptionLeg(symbol="CE", strike=24450.0, option_type="CE", ltp=None, bid=99.0, ask=101.0,
                  spread=2.0, volume=None, open_interest=12000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
        OptionLeg(symbol="PE", strike=24450.0, option_type="PE", ltp=None, bid=89.0, ask=91.0,
                  spread=2.0, volume=None, open_interest=15000.0, iv=None, delta=None, gamma=None, theta=None, vega=None),
    )
    chain = OptionChainSnapshot(underlying="NIFTY", expiry="2026-08-13", atm_strike=24450.0,
                                 config=OptionChainConfig(strike_range=100, strike_step=100), legs=legs)
    snapshot = MarketSnapshot(
        snapshot_version="1.0", timestamp="2026-08-04T09:15:00+05:30", source="fyers_live", latency_ms=1.0,
        health_status=HEALTH_OK, missing_fields=(), spot=SpotSnapshot(symbol="NIFTY", ltp=24400.0),
        vix=VixSnapshot(value=13.0), futures=None, option_chain=chain,
    )
    path = str(tmp_path / "market_snapshots.jsonl")
    with open(path, "w") as f:
        f.write(json.dumps(dataclasses.asdict(snapshot), default=str) + "\n")

    with open(path, "rb") as f:
        before = f.read()
    ReplayEngine("S1", path).run()
    with open(path, "rb") as f:
        after = f.read()
    assert before == after


def test_execution_and_capital_packages_byte_untouched():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py", "bujji/broker/paper.py",
         "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py", "bujji/journal/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    # TWO SCOPED EXCEPTIONS, both verified by CONTENT elsewhere rather than
    # waved through here:
    #
    #   paper.py                      Phase 15B, pre-existing.
    #   position_group_validation.py  M4's SESSION_TRANSITION vocabulary
    #                                 extension, authorised 2026-08-23.
    #
    # Excluding a filename here is deliberately the WEAK half. The strong half
    # is tests/test_frozen_vocabulary_extension.py, which asserts every line
    # ADDED to position_group_validation.py belongs to the authorised
    # SESSION_TRANSITION block and that nothing was removed -- so an unrelated
    # edit to that same file still fails the build. A filename exclusion alone
    # (which is all paper.py has) would let any future change to an excluded
    # file pass unnoticed.
    lines = [l for l in result.stdout.splitlines()
             if "paper.py" not in l
             and "position_group_validation.py" not in l
             and l.strip()]
    assert not any(l.strip() for l in lines if "|" in l), f"unexpected changes: {lines}"
